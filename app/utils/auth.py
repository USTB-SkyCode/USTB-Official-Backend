import logging
import os
import secrets
import hashlib
import base64
from urllib.parse import urlparse
import requests
from flask_session.sessions import RedisSessionInterface
from datetime import datetime, timezone, timedelta
from functools import wraps
from flask import session, redirect, url_for, jsonify, current_app, request

from app.models.session import UserSession, parse_session_timestamp
from app.utils.env import get_env_bool, get_env_int, get_env_str

logger = logging.getLogger(__name__)

# These endpoints must stay public because they either bootstrap a browser
# session or act as gateway callbacks for file and MCA authorization.
PUBLIC_API_ENDPOINTS = {
    'api.get_csrf_token',
    'api.get_rss_feeds',
    'api.get_rss_entries',
    'api.get_all_mc_server_status',
    'api.get_files',
    'api.issue_file_download_token',
    'api.verify_file_download_token',
    'api.authorize_file_download',
    'api.authorize_mca_download',
}
def _extract_access_token(token_data):
    return token_data.get('access_token') or token_data.get('token')


def _merge_scope_value(scope_value, *required_scopes):
    scopes = []
    for item in str(scope_value or '').replace(',', ' ').split():
        if item and item not in scopes:
            scopes.append(item)
    for item in required_scopes:
        if item and item not in scopes:
            scopes.append(item)
    return ' '.join(scopes)

class RedirectURIValidator:
    """重定向 URI 验证器"""
    
    DEFAULT_ALLOWED_DOMAINS = [
        'localhost',
        '127.0.0.1',
    ]

    DEFAULT_ALLOWED_PORTS = [3000, 5000, 5175, 8080, 443]

    @classmethod
    def _get_allowed_domains(cls):
        domains = current_app.config.get('OAUTH_ALLOWED_REDIRECT_HOSTS')
        return domains or cls.DEFAULT_ALLOWED_DOMAINS

    @classmethod
    def _get_allowed_ports(cls):
        return cls.DEFAULT_ALLOWED_PORTS

    @classmethod
    def _validate_uri(cls, uri, *, allowed_domains, allow_http_localhost):
        parsed = urlparse(uri)

        if parsed.scheme not in ['https', 'http']:
            return False

        hostname = parsed.hostname
        if not hostname:
            return False

        if hostname not in allowed_domains:
            return False

        localhost_hosts = {'localhost', '127.0.0.1'}
        if hostname in localhost_hosts:
            if parsed.scheme == 'http' and not allow_http_localhost:
                return False
        elif parsed.scheme != 'https':
            return False

        if parsed.port and parsed.port not in cls._get_allowed_ports():
            return False

        return True
    
    @classmethod
    def validate_redirect_uri(cls, uri):
        """验证重定向 URI 是否安全"""
        try:
            allowed_domains = cls._get_allowed_domains()
            allow_http_localhost = current_app.config.get('OAUTH_ALLOW_HTTP_LOCALHOST', True)
            return cls._validate_uri(
                uri,
                allowed_domains=allowed_domains,
                allow_http_localhost=allow_http_localhost,
            )

        except Exception:
            return False

    @classmethod
    def validate_app_return_uri(cls, uri):
        """验证登录完成后的前端跳转地址。"""
        try:
            allowed_domains = current_app.config.get('APP_ALLOWED_RETURN_HOSTS') or cls.DEFAULT_ALLOWED_DOMAINS
            allow_http_localhost = current_app.config.get('APP_ALLOW_HTTP_LOCALHOST', True)
            return cls._validate_uri(
                uri,
                allowed_domains=allowed_domains,
                allow_http_localhost=allow_http_localhost,
            )

        except Exception:
            return False

class PKCEGenerator:
    """PKCE (Proof Key for Code Exchange) 生成器"""
    
    @staticmethod
    def generate_code_verifier():
        """生成 code_verifier"""
        return base64.urlsafe_b64encode(secrets.token_bytes(32)).decode('utf-8').rstrip('=')
    
    @staticmethod
    def generate_code_challenge(verifier):
        """根据 code_verifier 生成 code_challenge"""
        digest = hashlib.sha256(verifier.encode('utf-8')).digest()
        return base64.urlsafe_b64encode(digest).decode('utf-8').rstrip('=')
from flask_wtf.csrf import generate_csrf

class SessionRefreshManager:
    """会话刷新管理器 - 处理 token 刷新和用户信息更新"""
    @staticmethod
    def test_refresh_token(session, provider_config):
        """"测试标准oauth2刷新token"""
        return SessionRefreshManager.refresh_token(session, provider_config)

    @staticmethod
    def should_refresh_session(session):
        """检查是否需要刷新会话"""
        if not session.get('logged_in'):
            return False
            
        provider = session.get('oauth_provider')
        if not provider:
            return False
            
        # 获取提供商配置
        from flask import current_app
        provider_config = current_app.config['OAUTH_PROVIDERS'].get(provider)
        if not provider_config:
            return False
            
        last_refresh = parse_session_timestamp(session.get('last_refresh_time'))
        if last_refresh is None:
            last_refresh = parse_session_timestamp(session.get('login_time'))
        if last_refresh is None:
            return True

        refresh_interval = provider_config.get('refresh_time', 7200)
        next_refresh = last_refresh + timedelta(seconds=refresh_interval)
        logger.debug("Next refresh time for provider %s: %s", provider, next_refresh.isoformat())
        return datetime.now(timezone.utc) >= next_refresh
    
    @staticmethod
    def refresh_token(session, provider_config):
        """刷新访问令牌"""
        
        if not provider_config.get('refresh'):
            return True  # 不支持刷新，返回成功
        refresh_mode = provider_config.get('refresh_mode')
        refresh_url = provider_config.get('refresh_url')
        token_url = provider_config.get('token_url')

        if refresh_mode != 'oauth2_token' and not refresh_url:
            return False
        if refresh_mode == 'oauth2_token' and not token_url:
            return False
        current_token = session.get('refresh_token')
        if not current_token:
            return False
        try:
            if refresh_mode == 'oauth2_token':
                refresh_response = requests.post(
                    token_url,
                    data={
                        'grant_type': 'refresh_token',
                        'refresh_token': current_token,
                        'client_id': provider_config.get('client_id', ''),
                        'client_secret': provider_config.get('client_secret', ''),
                    },
                    headers={'Accept': 'application/json'},
                    timeout=10,
                    allow_redirects=False,
                )
            else:
                refresh_response = requests.post(
                    refresh_url,
                    headers={
                        'Authorization': f'Bearer {current_token}',
                        'Accept': 'application/json'
                    },
                    timeout=10
                )
            if refresh_response.status_code == 200:
                token_data = refresh_response.json()
                new_token = _extract_access_token(token_data)
                if new_token:
                    session['access_token'] = new_token
                    if token_data.get('refresh_token'):
                        session['refresh_token'] = token_data['refresh_token']
                    return True
                logger.warning("Refresh response did not include a token")
                return False

            logger.warning(
                "Refresh token request failed with status %s and body preview %s",
                refresh_response.status_code,
                refresh_response.text[:200],
            )
            return False
        except requests.RequestException as exc:
            logger.warning("Refresh token request failed: %s", exc)
            return False
    
    @staticmethod
    def refresh_user_info(session, provider_config):
        """刷新用户信息"""
        current_token = session.get('access_token')
        if not current_token:
            return False
            
        try:
            # 获取用户信息
            user_response = requests.get(
                provider_config['user_url'],
                headers={'Authorization': f'Bearer {current_token}'},
                timeout=10
            )
            
            if user_response.status_code == 200:
                try:
                    user_info = user_response.json()
                except ValueError:
                    return False

                # 处理用户信息
                try:
                    from app.routes.auth import sanitize_user_info
                    safe_user_info = sanitize_user_info(user_info, session.get('oauth_provider'))
                except Exception:
                    return False

                session_data = UserSession.from_session(session)
                session_data.update_profile(safe_user_info)
                session_data.mark_refreshed()
                session_data.apply_to_session(session)
                return True

            logger.warning("Refresh user info request failed with status %s", user_response.status_code)
            return False
        except requests.RequestException as exc:
            logger.warning("Refresh user info request failed: %s", exc)
            return False
    
    @staticmethod
    def perform_session_refresh(session):
        """执行完整的会话刷新"""
        provider = session.get('oauth_provider')
        if not provider:
            return False
        
        provider_config = current_app.config['OAUTH_PROVIDERS'].get(provider)
        if not provider_config:
            return False

        try:
            token_refresh_success = SessionRefreshManager.refresh_token(session, provider_config)
            if provider_config.get('refresh') and not token_refresh_success:
                return False

            user_info_refresh_success = SessionRefreshManager.refresh_user_info(session, provider_config)
            return user_info_refresh_success
        except Exception:
            logger.exception("Unexpected error while refreshing session")
            return False

class PatchedRedisSessionInterface(RedisSessionInterface):
    """
    兼容 redis 5.x
    """
    def _generate_sid(self):
        sid = super()._generate_sid()
        return sid.decode() if isinstance(sid, bytes) else sid

    def open_session(self, app, request):
        session = super().open_session(app, request)
        if hasattr(session, 'sid') and isinstance(session.sid, bytes):
            session.sid = session.sid.decode()
        return session

    def save_session(self, app, session, response):
        
        if hasattr(session, 'sid') and isinstance(session.sid, bytes):
            session.sid = session.sid.decode()
        session_id = getattr(session, 'sid', None)
        if isinstance(session_id, bytes):
            session.sid = session_id.decode()
        
        orig_set_cookie = response.set_cookie
        def safe_set_cookie(key, value=None, *args, **kwargs):
            # 当 delete_cookie 传入 None 时，转换为空字符串
            if value is None:
                value = ""
            # 处理 bytes 类型的值
            elif isinstance(value, bytes):
                value = value.decode()
            return orig_set_cookie(key, value, *args, **kwargs)
        response.set_cookie = safe_set_cookie
        return super().save_session(app, session, response)


def require_login_and_refresh(redirect_on_fail=True):
    """返回一个装饰器：检查登录并在需要时刷新会话。

    redirect_on_fail=True 时未登录会重定向到 main.index，否则返回 JSON 401。
    """
    def decorator(f):
        @wraps(f)
        def wrapped(*args, **kwargs):
            if not session.get('logged_in'):
                if redirect_on_fail:
                    return redirect(url_for('main.index'))
                return jsonify({"data": None, "error": "Not logged in"}), 401

            try:
                if SessionRefreshManager.should_refresh_session(session):
                    ok = SessionRefreshManager.perform_session_refresh(session)
                    if not ok:
                        session.clear()
                        token = generate_csrf()
                        if redirect_on_fail:
                            resp = redirect(url_for('main.index'))
                        else:
                            resp = jsonify({"data": None, "error": "Session refresh failed"}), 401
                        # 处理 jsonify 结构为元组的场景
                        resp_obj = resp[0] if isinstance(resp, tuple) else resp
                        resp_obj.set_cookie(
                            'XSRF-TOKEN',
                            token,
                            secure=current_app.config.get('SESSION_COOKIE_SECURE', True),
                            samesite=current_app.config.get('SESSION_COOKIE_SAMESITE', 'Lax'),
                            httponly=False,
                        )
                        return resp
            except Exception:
                current_app.logger.exception('Session refresh error')
                session.clear()
                token = generate_csrf()
                if redirect_on_fail:
                    resp = redirect(url_for('main.index'))
                else:
                    resp = jsonify({"data": None, "error": "Session refresh error"}), 401
                resp_obj = resp[0] if isinstance(resp, tuple) else resp
                resp_obj.set_cookie(
                    'XSRF-TOKEN',
                    token,
                    secure=current_app.config.get('SESSION_COOKIE_SECURE', True),
                    samesite=current_app.config.get('SESSION_COOKIE_SAMESITE', 'Lax'),
                    httponly=False,
                )
                return resp

            return f(*args, **kwargs)
        return wrapped
    return decorator


def require_api_permission(min_permission=1):
    """要求当前 API 会话具备指定权限级别。"""

    def decorator(f):
        @wraps(f)
        def wrapped(*args, **kwargs):
            permission = int(session.get('permission', 0) or 0)
            if not session.get('logged_in'):
                return jsonify({"data": None, "error": "Not logged in"}), 401
            if permission < min_permission:
                return jsonify({"data": None, "error": "Forbidden"}), 403
            return f(*args, **kwargs)

        return wrapped

    return decorator


def verify_api_session():
    """用于 API blueprint 的 before_request：强制要求登录并尝试刷新会话。

    返回值为 Flask response 表示拦截，否则返回 None 继续处理。
    """
    # 允许 OPTIONS 预检请求通过（CORS preflight）
    if request.method == 'OPTIONS':
        return None

    if request.endpoint in PUBLIC_API_ENDPOINTS:
        return None

    if not session.get('logged_in'):
        return jsonify({"data": None, "error": "Not logged in"}), 401

    try:
        if SessionRefreshManager.should_refresh_session(session):
            ok = SessionRefreshManager.perform_session_refresh(session)
            if not ok:
                session.clear()
                token = generate_csrf()
                resp = jsonify({"data": None, "error": "Session refresh failed"})
                resp.set_cookie(
                    'XSRF-TOKEN',
                    token,
                    secure=current_app.config.get('SESSION_COOKIE_SECURE', True),
                    samesite=current_app.config.get('SESSION_COOKIE_SAMESITE', 'Lax'),
                    httponly=False,
                )
                return resp, 401
    except Exception:
        current_app.logger.exception('Session refresh error')
        session.clear()
        token = generate_csrf()
        resp = jsonify({"data": None, "error": "Session refresh error"})
        resp.set_cookie(
            'XSRF-TOKEN',
            token,
            secure=current_app.config.get('SESSION_COOKIE_SECURE', True),
            samesite=current_app.config.get('SESSION_COOKIE_SAMESITE', 'Lax'),
            httponly=False,
        )
        return resp, 401

    return None


def load_oauth_providers_from_env():
    """Build OAuth provider definitions entirely from environment variables."""
    github_token_url = get_env_str('GITHUB_TOKEN_URL', 'https://github.com/login/oauth/access_token')
    mua_token_url = get_env_str('MUA_TOKEN_URL', 'https://skin.mualliance.ltd/api/union/oauth2/token')
    ustb_token_url = get_env_str('USTB_TOKEN_URL', 'https://skin.ustb.world/skinapi/oauth/token')

    return {
        'github': {
            'name': 'GitHub',
            'client_id': get_env_str('GITHUB_CLIENT_ID', ''),
            'client_secret': get_env_str('GITHUB_CLIENT_SECRET', ''),
            'authorize_url': get_env_str('GITHUB_AUTHORIZE_URL', 'https://github.com/login/oauth/authorize'),
            'token_url': github_token_url,
            'user_url': get_env_str('GITHUB_USER_URL', 'https://api.github.com/user'),
            'scope': get_env_str('GITHUB_SCOPE', 'user:email'),
            'redirect_uri': get_env_str('GITHUB_REDIRECT_URI', ''),
            'supports_pkce': get_env_bool('GITHUB_SUPPORTS_PKCE', True),
            'user_field_mapping': {
                'id': 'id',
                'username': 'login',
                'email': 'email',
                'nickname': 'name',
                'avatar_url': 'avatar_url',
            },
            'refresh': False,
            'refresh_time': get_env_int('GITHUB_REFRESH_TIME', 20),
            'validation': {
                'required_fields': ['id', 'login'],
                'token_endpoint_validation': get_env_str('GITHUB_TOKEN_ENDPOINT_VALIDATION', github_token_url),
            },
        },
        'mua': {
            'name': 'MUA Union',
            'client_id': get_env_str('MUA_CLIENT_ID', ''),
            'client_secret': get_env_str('MUA_CLIENT_SECRET', ''),
            'authorize_url': get_env_str('MUA_AUTHORIZE_URL', 'https://skin.mualliance.ltd/api/union/oauth2/authorize'),
            'token_url': mua_token_url,
            'user_url': get_env_str('MUA_USER_URL', 'https://skin.mualliance.ltd/api/union/oauth2/user'),
            'scope': get_env_str('MUA_SCOPE', 'user'),
            'redirect_uri': get_env_str('MUA_REDIRECT_URI', ''),
            'supports_pkce': get_env_bool('MUA_SUPPORTS_PKCE', True),
            'user_field_mapping': {
                'id': 'sub',
                'username': 'nickname',
                'email': 'email',
                'nickname': 'nickname',
                'avatar_url': None,
            },
            'refresh': False,
            'refresh_time': get_env_int('MUA_REFRESH_TIME', 7200),
            'validation': {
                'required_fields': ['sub', 'nickname'],
                'token_endpoint_validation': get_env_str('MUA_TOKEN_ENDPOINT_VALIDATION', mua_token_url),
            },
        },
        'ustb': {
            'name': 'USTB',
            'client_id': get_env_str('USTB_CLIENT_ID', ''),
            'client_secret': get_env_str('USTB_CLIENT_SECRET', ''),
            'authorize_url': get_env_str('USTB_AUTHORIZE_URL', 'https://skin.ustb.world/oauth/authorize'),
            'token_url': ustb_token_url,
            'user_url': get_env_str('USTB_USER_URL', 'https://skin.ustb.world/skinapi/oauth/userinfo'),
            'skin_url': get_env_str('USTB_SKIN_URL', 'https://skin.ustb.world/skinapi/oauth/skin'),
            'scope': _merge_scope_value(get_env_str('USTB_SCOPE', 'userinfo avatar email permission'), 'skin'),
            'redirect_uri': get_env_str('USTB_REDIRECT_URI', ''),
            'supports_pkce': get_env_bool('USTB_SUPPORTS_PKCE', False),
            'user_field_mapping': {
                'id': 'sub',
                'username': 'username',
                'email': 'email',
                'nickname': 'username',
                'avatar_url': 'avatar_url',
            },
            'refresh': get_env_bool('USTB_REFRESH_ENABLED', True),
            'refresh_mode': get_env_str('USTB_REFRESH_MODE', 'oauth2_token'),
            'refresh_time': get_env_int('USTB_REFRESH_TIME', 20),
            'validation': {
                'required_fields': ['sub', 'username'],
                'token_endpoint_validation': get_env_str('USTB_TOKEN_ENDPOINT_VALIDATION', ustb_token_url),
            },
            'base_url': get_env_str('USTB_BASE_URL', 'https://skin.ustb.world'),
        },
    }
