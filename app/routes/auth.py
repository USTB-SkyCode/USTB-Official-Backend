"""OAuth login, callback handling, and controlled development auth entrypoints."""

import html
import ipaddress
import logging
import secrets
from urllib.parse import urlencode, urlparse

import requests
from flask import Blueprint, request, redirect, session, jsonify, current_app

from app.models.session import UserSession
from app.utils.same_origin_assets import rewrite_avatar_url_for_same_origin
from app.utils.auth import PKCEGenerator, RedirectURIValidator
from app.debugger import debug_oauth_login, debug_oauth_callback, session_debugger
from flask_wtf.csrf import generate_csrf

auth_bp = Blueprint('auth', __name__,url_prefix='/auth')
logger = logging.getLogger(__name__)

DEV_AUTH_PRESETS = {
    'guest': {
        'id': 'dev-guest',
        'username': 'guest',
        'nickname': 'Guest',
        'email': 'guest@dev.local',
        'avatar_url': '',
        'permission': 0,
    },
    'user': {
        'id': 'dev-user',
        'username': 'developer',
        'nickname': 'Developer',
        'email': 'developer@dev.local',
        'avatar_url': '',
        'permission': 0,
    },
    'admin': {
        'id': 'dev-admin',
        'username': 'admin',
        'nickname': 'Admin',
        'email': 'admin@dev.local',
        'avatar_url': '',
        'permission': 2,
    },
}


def _extract_request_host_candidates():
    """Collect hostnames from caller-controlled headers used by dev auth allowlists."""
    hosts = set()
    for header_name in ('Origin', 'Referer'):
        header_value = request.headers.get(header_name, '').strip()
        if not header_value:
            continue
        try:
            parsed = urlparse(header_value)
        except Exception:
            continue
        if parsed.hostname:
            hosts.add(parsed.hostname)
    return hosts


def _is_dev_auth_request_allowed():
    """Gate dev-login requests behind explicit config, secret, and source checks."""
    if not current_app.config.get('DEV_AUTH_ENABLED', False):
        return False, 'DEV_AUTH_ENABLED is false'

    if current_app.config.get('DEV_AUTH_REQUIRE_DEBUG', True) and not current_app.debug:
        return False, 'DEV_AUTH requires DEBUG mode'

    required_secret = current_app.config.get('DEV_AUTH_SHARED_SECRET', '')
    if required_secret:
        provided_secret = request.headers.get('X-Dev-Auth-Secret', '')
        if not provided_secret or not secrets.compare_digest(provided_secret, required_secret):
            return False, 'Invalid X-Dev-Auth-Secret'

    allowed_hosts = set(current_app.config.get('DEV_AUTH_ALLOWED_SOURCE_HOSTS', []))
    allowed_cidrs = current_app.config.get('DEV_AUTH_ALLOWED_SOURCE_CIDRS', [])

    for host in _extract_request_host_candidates():
        if host in allowed_hosts:
            return True, None

    remote_addr = (request.remote_addr or '').strip()
    if not remote_addr:
        return False, 'Missing remote address'

    try:
        remote_ip = ipaddress.ip_address(remote_addr)
    except ValueError:
        return False, f'Invalid remote address: {remote_addr}'

    for cidr in allowed_cidrs:
        try:
            network = ipaddress.ip_network(cidr, strict=False)
        except ValueError:
            continue
        if remote_ip in network:
            return True, None

    return False, f'DEV auth request rejected from {remote_addr}'


def _build_dev_user(payload):
    """Merge a preset dev user with explicit payload overrides."""
    preset_name = str(payload.get('preset', 'admin')).strip().lower()
    preset = DEV_AUTH_PRESETS.get(preset_name, DEV_AUTH_PRESETS['admin']).copy()

    overrides = {
        'id': payload.get('user_id', payload.get('id')),
        'username': payload.get('username'),
        'nickname': payload.get('nickname'),
        'email': payload.get('email'),
        'avatar_url': payload.get('avatar_url'),
        'permission': payload.get('permission'),
    }
    for key, value in overrides.items():
        if value is None:
            continue
        if key == 'permission':
            preset[key] = int(value)
        else:
            preset[key] = str(value)

    if not preset.get('nickname'):
        preset['nickname'] = preset.get('username', '')
    return preset

@auth_bp.route('/github')
def github_login():
    """Start the GitHub OAuth login flow."""
    return oauth2_login('github')


@auth_bp.route('/mua')
def mua_login():
    """Start the MUA Union OAuth login flow."""
    return oauth2_login('mua')


@auth_bp.route('/ustb')
def ustb_login():
    """Start the USTB vSkin OAuth login flow."""
    return oauth2_login('ustb')


@auth_bp.route('/login/github/callback')
def github_callback():
    """Finish the GitHub OAuth login flow."""
    return oauth2_callback('github')


@auth_bp.route('/login/mua/callback')
def mua_callback():
    """Finish the MUA Union OAuth login flow."""
    return oauth2_callback('mua')


@auth_bp.route('/oauth/callback')
@auth_bp.route('/login/ustb/callback')
def ustb_callback():
    """Finish the USTB vSkin OAuth flow using the provider remembered in session."""
    provider = session.get('oauth_provider', 'ustb')
    return oauth2_callback(provider)


@auth_bp.route('/dev-login', methods=['POST'])
def dev_login():
    """Create a synthetic session for trusted development callers only."""
    allowed, reason = _is_dev_auth_request_allowed()
    if not allowed:
        logger.warning('Rejected dev login request: %s', reason)
        return jsonify({'error': 'Dev login is not allowed'}), 403

    payload = request.get_json(silent=True) or {}
    try:
        user = _build_dev_user(payload)
    except (TypeError, ValueError) as exc:
        return jsonify({'error': f'Invalid dev login payload: {exc}'}), 400

    provider = str(payload.get('provider') or current_app.config.get('DEV_AUTH_DEFAULT_PROVIDER', 'dev')).strip()
    create_secure_session(user, provider)

    resp = jsonify({
        'data': {
            'message': 'Dev login successful',
            'user': {
                'user_id': session.get('user_id'),
                'username': session.get('username'),
                'email': session.get('email'),
                'provider': session.get('oauth_provider'),
                'permission': session.get('permission', 0),
            },
        },
        'error': None,
    })
    token = generate_csrf()
    resp.set_cookie(
        'XSRF-TOKEN',
        token,
        secure=current_app.config.get('SESSION_COOKIE_SECURE', True),
        samesite=current_app.config.get('SESSION_COOKIE_SAMESITE', 'Lax'),
        httponly=False,
    )
    return resp


@debug_oauth_login
def oauth2_login(provider):
    """Build the provider authorization redirect and persist anti-CSRF state."""
    provider_config = current_app.config['OAUTH_PROVIDERS'].get(provider)
    if not provider_config:
        return jsonify({'error': f'Unknown OAuth provider: {provider}'}), 400
    
    if not provider_config.get('client_id') or not provider_config.get('client_secret'):
        return jsonify({'error': f'{provider_config["name"]} OAuth配置不完整，请检查环境变量'}), 500
    
    if not provider_config.get('redirect_uri'):
        return jsonify({'error': f'{provider_config["name"]} 回调URI未配置'}), 500

    session_debugger.debug(f"{provider_config['name']} login initiated from {request.remote_addr}")
    supports_pkce = provider_config.get('supports_pkce', True)
    state = secrets.token_urlsafe(32)
    session['oauth_state'] = state
    session['oauth_provider'] = provider

    return_to = request.args.get('return_to', '').strip()
    if return_to:
        if not RedirectURIValidator.validate_app_return_uri(return_to):
            return jsonify({'error': 'Invalid return_to parameter'}), 400
        session['oauth_return_to'] = return_to
    else:
        session.pop('oauth_return_to', None)

    params = {
        'client_id': provider_config['client_id'],
        'redirect_uri': provider_config['redirect_uri'],
        'response_type': 'code',
        'state': state,
    }

    if supports_pkce:
        code_verifier = PKCEGenerator.generate_code_verifier()
        code_challenge = PKCEGenerator.generate_code_challenge(code_verifier)
        session['code_verifier'] = code_verifier
        params.update({
            'code_challenge': code_challenge,
            'code_challenge_method': 'S256'
        })

    if provider_config.get('scope'):
        params['scope'] = provider_config['scope']

    redirect_uri = provider_config['redirect_uri']
    
    session_debugger.debug(f"Redirect URI: {redirect_uri}")
    session_debugger.debug(f"Client ID: {provider_config['client_id']}")
    session_debugger.debug(f"PKCE Support: {supports_pkce}")

    if not RedirectURIValidator.validate_redirect_uri(redirect_uri):
        session_debugger.error(f"Invalid redirect URI: {redirect_uri}")
        return jsonify({'error': 'Invalid redirect URI'}), 400
    
    auth_url = f"{provider_config['authorize_url']}?{urlencode(params)}"
    session_debugger.debug(f"Auth URL: {auth_url}")
    return redirect(auth_url)


@debug_oauth_callback
def oauth2_callback(provider):
    """Exchange an auth code for tokens, fetch user info, and create the session."""
    provider_config = current_app.config['OAUTH_PROVIDERS'].get(provider)
    if not provider_config:
        return jsonify({'error': f'Unknown OAuth provider: {provider}'}), 400
    
    if not provider_config.get('client_id') or not provider_config.get('client_secret'):
        return jsonify({'error': f'{provider_config["name"]} OAuth配置不完整，请检查环境变量'}), 500
    
    session_debugger.debug(f"{provider_config['name']} callback received from {request.remote_addr}")
    session_debugger.debug(f"Callback URL: {request.url}")
    session_debugger.debug(f"Query parameters: {dict(request.args)}")
    
    received_state = request.args.get('state')
    stored_state = session.pop('oauth_state', None)
    
    if not received_state or not stored_state:
        return jsonify({'error': 'Missing state parameter'}), 400
    
    if not secrets.compare_digest(received_state, stored_state):
        return jsonify({'error': 'Invalid state parameter - possible CSRF attack'}), 400
    
    if session.pop('oauth_provider', None) != provider:
        return jsonify({'error': 'Invalid OAuth provider'}), 400
    
    error = request.args.get('error')
    if error:
        error_description = request.args.get('error_description', 'Unknown error')
        return jsonify({'error': f'OAuth authorization failed: {error_description}'}), 400
    
    code = request.args.get('code')
    if not code:
        return jsonify({'error': 'Missing authorization code'}), 400
    
    session_debugger.debug(f"{provider_config['name']} received authorization code: {code[:20]}...")
    session_debugger.debug(f"Authorization code length: {len(code)}")
    
    dangerous_chars = ['<', '>', '"', "'", '\n', '\r', '\t']
    if any(char in code for char in dangerous_chars):
        return jsonify({'error': 'Invalid authorization code format'}), 400

    try:
        supports_pkce = provider_config.get('supports_pkce', True)
        session_debugger.debug(f"{provider_config['name']} supports PKCE: {supports_pkce}")
        code_verifier = None
        if supports_pkce:
            code_verifier = session.pop('code_verifier', None)
            if not code_verifier:
                return jsonify({'error': 'Missing PKCE verifier'}), 400
            session_debugger.debug(f"{provider_config['name']} PKCE verifier found")
        else:
            session_debugger.debug(f"{provider_config['name']} does not support PKCE, skipping verifier check")

        # Token exchange must target the provider endpoint baked into server config.
        if provider_config['token_url'] != provider_config['validation']['token_endpoint_validation']:
            return jsonify({'error': 'Invalid token endpoint'}), 400

        token_data = {
            'client_id': provider_config['client_id'],
            'client_secret': provider_config['client_secret'],
            'code': code,
            'redirect_uri': provider_config['redirect_uri'],
            'grant_type': 'authorization_code'
        }

        if supports_pkce and code_verifier:
            token_data['code_verifier'] = code_verifier
            session_debugger.debug(f"{provider_config['name']} adding PKCE verifier to token request")

        session_debugger.debug(f"{provider_config['name']} token request data keys: {list(token_data.keys())}")
        session_debugger.debug(f"{provider_config['name']} token endpoint: {provider_config['token_url']}")
        token_response = requests.post(
            provider_config['token_url'],
            data=token_data,
            headers={'Accept': 'application/json'},
            timeout=10,
            allow_redirects=False
        )

        session_debugger.debug(f"{provider_config['name']} token response status: {token_response.status_code}")
        session_debugger.debug(f"{provider_config['name']} token response headers: {dict(token_response.headers)}")

        if token_response.status_code != 200:
            session_debugger.error(
                f"{provider_config['name']} token exchange failed with status {token_response.status_code}"
            )
            logger.warning(
                "%s token exchange failure preview: %s",
                provider,
                token_response.text[:200],
            )
            return jsonify({'error': f'Failed to exchange code for token: HTTP {token_response.status_code}'}), 400

        try:
            token_info = token_response.json()
        except ValueError as e:
            session_debugger.error(f"{provider_config['name']} invalid JSON response: {e}")
            return jsonify({'error': 'Invalid JSON response from token endpoint'}), 400

        session_debugger.debug(f"{provider_config['name']} token response keys: {list(token_info.keys())}")

        access_token = token_info.get('access_token')
        if not access_token:
            session_debugger.error(f"{provider_config['name']} no access_token in response")
            return jsonify({'error': 'No access token received'}), 400

        refresh_token = None
        if provider == 'ustb':
            refresh_token = token_info.get('refresh_token')
            if not refresh_token:
                session_debugger.warning("USTB OAuth2 token response did not include refresh_token")

        user_response = requests.get(
            provider_config['user_url'],
            headers={'Authorization': f'Bearer {access_token}'},
            timeout=10
        )
        if user_response.status_code != 200:
            logger.warning("%s user info request failed with status %s", provider, user_response.status_code)
            return jsonify({'error': 'Failed to get user information'}), 400
        user_info = user_response.json()

        safe_user_info = sanitize_user_info(user_info, provider)

        return_to = session.pop('oauth_return_to', None) or current_app.config.get('DEFAULT_LOGIN_SUCCESS_URL', '/home')
        create_secure_session(safe_user_info, provider, access_token, refresh_token)
        
        resp = redirect(return_to)
        token = generate_csrf()
        resp.set_cookie(
            'XSRF-TOKEN',
            token,
            secure=current_app.config.get('SESSION_COOKIE_SECURE', True),
            samesite=current_app.config.get('SESSION_COOKIE_SAMESITE', 'Lax'),
            httponly=False,
        )
        return resp
    except requests.exceptions.RequestException as e:
        logger.warning("OAuth callback request failed for %s: %s", provider, e)
        return jsonify({'error': 'OAuth callback request failed'}), 500


def sanitize_user_info(user_info, provider):
    """Normalize provider user info into the local session schema.

    Avatar URLs are rewritten here so frontend code only ever consumes
    same-origin asset URLs.
    """
    provider_config = current_app.config['OAUTH_PROVIDERS'].get(provider)
    if not provider_config:
        raise ValueError(f'Unknown provider for user info sanitize: {provider}')
    field_mapping = provider_config['user_field_mapping']
    required_fields = provider_config['validation']['required_fields']
    sanitized = {}
    source_values = {}

    for standard_field, provider_field in field_mapping.items():
        if provider_field:
            value = user_info.get(provider_field)
            source_values[provider_field] = value
            if value is not None:
                if standard_field == 'avatar_url':
                    sanitized[standard_field] = rewrite_avatar_url_for_same_origin(value, provider=provider)
                else:
                    sanitized[standard_field] = html.escape(str(value)) if isinstance(value, str) else value

    if provider == 'ustb' and 'avatar_url' not in sanitized:
        sanitized['avatar_url'] = rewrite_avatar_url_for_same_origin('', provider=provider)

    for field in required_fields:
        raw_value = source_values.get(field, user_info.get(field))
        if raw_value is None:
            raise ValueError(f"Missing required user information: {field}")
        if isinstance(raw_value, str) and not raw_value.strip():
            raise ValueError(f"Missing required user information: {field}")

    if provider == 'ustb':
        permission_value = user_info.get('permission')
        user_group = user_info.get('user_group')

        if permission_value is not None:
            sanitized['permission'] = permission_value
        elif isinstance(user_group, str):
            permission_map = {
                'user': 0,
                'teacher': 0,
                'admin': 1,
                'super_admin': 2,
            }
            sanitized['permission'] = permission_map.get(user_group, 0)

    return sanitized


def create_secure_session(user, provider, access_token=None, refresh_token=None):
    """Rotate the session and persist the normalized authenticated user payload."""
    session.clear()

    if hasattr(session, 'sid'):
        generate_sid = getattr(current_app.session_interface, '_generate_sid', None)
        if callable(generate_sid):
            try:
                session.sid = generate_sid()
            except Exception:
                logger.warning('Failed to rotate session id during login', exc_info=True)

    session_data = UserSession.from_oauth_user(
        user,
        provider,
        access_token=access_token,
        refresh_token=refresh_token,
    )
    session_data.apply_to_session(session)
