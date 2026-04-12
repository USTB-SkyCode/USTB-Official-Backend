import json

import requests
from flask import Blueprint, Response, current_app, jsonify, request, session

from app.utils.auth import require_login_and_refresh
from app.services.SceneCameraPreset import load_scene_camera_preset_override_map
from app.utils.same_origin_assets import is_allowed_external_asset_url

main_bp = Blueprint('main', __name__)


class AssetProxyResponseTooLarge(Exception):
	"""Raised when an upstream asset response exceeds the configured byte limit."""


def _strip_trailing_slash(value):
	if not isinstance(value, str):
		return ''

	trimmed = value.strip()
	if not trimmed:
		return ''

	return trimmed[:-1] if trimmed.endswith('/') else trimmed


def _build_runtime_app_config(config):
	"""Build a stable runtime config object from startup-loaded app config."""
	api_base_url = _strip_trailing_slash(config.get('RUNTIME_CONFIG_API_BASE_URL'))
	auth_base_url = _strip_trailing_slash(config.get('RUNTIME_CONFIG_AUTH_BASE_URL')) or api_base_url

	return {
		'API_BASE_URL': api_base_url,
		'AUTH_BASE_URL': auth_base_url,
		'APP_BASE_URL': _strip_trailing_slash(config.get('RUNTIME_CONFIG_APP_BASE_URL')),
		'SKIN_API_BASE_URL': _strip_trailing_slash(config.get('RUNTIME_CONFIG_SKIN_API_BASE_URL')),
		'MCA_BASE_URL': _strip_trailing_slash(config.get('RUNTIME_CONFIG_MCA_BASE_URL')),
		'SKIN_BASE_URL': _strip_trailing_slash(config.get('RUNTIME_CONFIG_SKIN_BASE_URL')) or '/assets/skin',
		'DEV_BACKEND_PROXY_ENABLED': bool(config.get('RUNTIME_CONFIG_DEV_BACKEND_PROXY_ENABLED', False)),
	}


def prepare_runtime_app_config(app):
	"""Freeze the static portion of frontend bootstrap config at app startup."""
	app.config['RUNTIME_APP_CONFIG'] = _build_runtime_app_config(app.config)


@main_bp.route('/healthz', methods=['GET'])
def healthz():
	return jsonify({'status': 'ok'})


@main_bp.route('/config.js', methods=['GET'])
def runtime_config_js():
	base_payload = current_app.config.get('RUNTIME_APP_CONFIG') or _build_runtime_app_config(current_app.config)
	payload = {
		**base_payload,
		'SCENE_CAMERA_PRESET_OVERRIDES': load_scene_camera_preset_override_map(),
	}
	body = 'window.APP_CONFIG = ' + json.dumps(payload, ensure_ascii=True) + ';\n'
	response = Response(body, mimetype='application/javascript')
	response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
	response.headers['Pragma'] = 'no-cache'
	return response


def _build_cross_origin_isolation_server_payload():
	"""Return the server-side snapshot for the public isolation probe."""
	return {
		'host': request.host,
		'path': request.path,
		'scheme': request.scheme,
		'forwarded_proto': request.headers.get('X-Forwarded-Proto', ''),
		'sec_fetch_dest': request.headers.get('Sec-Fetch-Dest', ''),
		'sec_fetch_mode': request.headers.get('Sec-Fetch-Mode', ''),
		'sec_fetch_site': request.headers.get('Sec-Fetch-Site', ''),
		'user_agent': request.headers.get('User-Agent', ''),
		'notes': {
			'open_on_app_host': 'Use the app domain for a real COOP/COEP check.',
			'expected_browser_signals': [
				'window.crossOriginIsolated === true',
				"typeof SharedArrayBuffer === 'function'",
			],
		},
	}


@main_bp.route('/diagnostics/cross-origin-isolation.json', methods=['GET'])
def cross_origin_isolation_diagnostics_json():
	response = jsonify({'data': _build_cross_origin_isolation_server_payload(), 'error': None})
	response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
	response.headers['Pragma'] = 'no-cache'
	return response


@main_bp.route('/diagnostics/cross-origin-isolation', methods=['GET'])
def cross_origin_isolation_diagnostics_page():
	"""Render a standalone browser probe for COOP/COEP and SharedArrayBuffer."""
	server_payload = json.dumps(_build_cross_origin_isolation_server_payload(), ensure_ascii=True)
	body = f"""<!doctype html>
<html lang=\"en\">
<head>
	<meta charset=\"utf-8\">
	<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">
	<title>Cross-Origin Isolation Diagnostics</title>
	<style>
		:root {{ color-scheme: light; }}
		body {{ margin: 0; padding: 24px; font: 14px/1.5 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; background: #f3f6fb; color: #122033; }}
		pre {{ margin: 0; padding: 20px; border: 1px solid #c9d5e6; border-radius: 12px; background: #fff; white-space: pre-wrap; word-break: break-word; }}
	</style>
</head>
<body>
	<pre id=\"output\">Running browser diagnostics...</pre>
	<script>
		const output = document.getElementById('output');
		const serverSnapshot = {server_payload};

		async function runProbe() {{
			const result = {{
				browser: {{
					href: window.location.href,
					origin: window.location.origin,
					userAgent: navigator.userAgent,
					isSecureContext: window.isSecureContext,
					crossOriginIsolated: window.crossOriginIsolated,
					sharedArrayBufferType: typeof SharedArrayBuffer,
					sharedArrayBufferUsable: false,
					sharedArrayBufferError: '',
					atomicsType: typeof Atomics,
				}},
				server: {{
					snapshot: serverSnapshot,
					fetchStatus: 0,
					responseHeaders: {{
						coop: '',
						coep: '',
						corp: '',
						contentType: '',
						cacheControl: '',
					}},
					fetchBody: null,
					fetchError: '',
				}},
			}};

			if (typeof SharedArrayBuffer === 'function') {{
				try {{
					const probe = new SharedArrayBuffer(8);
					result.browser.sharedArrayBufferUsable = probe.byteLength === 8;
				}} catch (error) {{
					result.browser.sharedArrayBufferError = String(error && error.message ? error.message : error);
				}}
			}}

			try {{
				const response = await fetch('/diagnostics/cross-origin-isolation.json', {{
					method: 'GET',
					cache: 'no-store',
					credentials: 'same-origin',
				}});
				result.server.fetchStatus = response.status;
				result.server.responseHeaders = {{
					coop: response.headers.get('cross-origin-opener-policy') || '',
					coep: response.headers.get('cross-origin-embedder-policy') || '',
					corp: response.headers.get('cross-origin-resource-policy') || '',
					contentType: response.headers.get('content-type') || '',
					cacheControl: response.headers.get('cache-control') || '',
				}};
				result.server.fetchBody = await response.json();
			}} catch (error) {{
				result.server.fetchError = String(error && error.message ? error.message : error);
			}}

			output.textContent = JSON.stringify(result, null, 2);
		}}

		runProbe();
	</script>
</body>
</html>
"""
	response = Response(body, mimetype='text/html')
	response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
	response.headers['Pragma'] = 'no-cache'
	return response


def _copy_proxy_response(upstream_response):
	"""Copy only safe response headers from the upstream asset response."""
	content_length_header = upstream_response.headers.get('Content-Length')
	try:
		body = _read_proxy_response_body(upstream_response)
	finally:
		upstream_response.close()

	headers = {}
	for header_name in ('Content-Type', 'Cache-Control', 'ETag', 'Last-Modified'):
		header_value = upstream_response.headers.get(header_name)
		if header_value:
			headers[header_name] = header_value

	if request.method == 'HEAD':
		if content_length_header:
			headers['Content-Length'] = content_length_header
	else:
		headers['Content-Length'] = str(len(body))

	if 'Cache-Control' not in headers:
		headers['Cache-Control'] = 'private, max-age=300'

	return Response(
		body if request.method != 'HEAD' else b'',
		status=upstream_response.status_code,
		headers=headers,
	)


def _read_proxy_response_body(upstream_response):
	if request.method == 'HEAD':
		return b''

	max_bytes = max(1024, int(current_app.config.get('ASSET_PROXY_MAX_BYTES', 8 * 1024 * 1024)))
	content_length_header = (upstream_response.headers.get('Content-Length') or '').strip()
	if content_length_header:
		try:
			if int(content_length_header) > max_bytes:
				raise AssetProxyResponseTooLarge(f'Asset proxy response exceeds {max_bytes} bytes')
		except ValueError:
			pass

	chunks = []
	total_bytes = 0
	for chunk in upstream_response.iter_content(chunk_size=64 * 1024):
		if not chunk:
			continue
		total_bytes += len(chunk)
		if total_bytes > max_bytes:
			raise AssetProxyResponseTooLarge(f'Asset proxy response exceeds {max_bytes} bytes')
		chunks.append(chunk)

	return b''.join(chunks)


def _request_asset_proxy_upstream(target_url, headers):
	return requests.request(
		request.method,
		target_url,
		headers=headers,
		timeout=current_app.config.get('ASSET_PROXY_TIMEOUT', 10),
		allow_redirects=False,
		stream=True,
	)


def _finalize_proxy_response(upstream_response, target_url):
	try:
		return _copy_proxy_response(upstream_response)
	except AssetProxyResponseTooLarge:
		current_app.logger.warning(
			'Asset proxy response too large: method=%s target=%s status=%s content_length=%s',
			request.method,
			target_url,
			upstream_response.status_code,
			upstream_response.headers.get('Content-Length'),
		)
		return jsonify({'data': None, 'error': 'Asset proxy upstream response too large'}), 413


def _build_asset_proxy_target(proxy_path: str) -> str | None:
	"""Resolve a same-origin proxy path into an upstream asset URL.

	Legacy avatar paths are intentionally translated to the documented vSkin OAuth
	avatar endpoint so older frontends continue to work during rollout.
	"""
	normalized_path = '/'.join(segment for segment in str(proxy_path or '').split('/') if segment not in ('', '.'))
	if not normalized_path or '..' in normalized_path.split('/'):
		return None

	if normalized_path == 'external':
		target_url = request.args.get('url', '').strip()
		if not is_allowed_external_asset_url(target_url):
			return None
		return target_url

	base_url = str(current_app.config.get('OAUTH_PROVIDERS', {}).get('ustb', {}).get('base_url', '') or '').rstrip('/')
	if not base_url:
		return None

	if normalized_path == 'oauth/avatar':
		return f'{base_url}/skinapi/oauth/avatar'

	if normalized_path == 'public/default-avatar':
		return f'{base_url}/skinapi/public/default-avatar'

	if normalized_path.startswith('avatar/user/'):
		return f'{base_url}/skinapi/oauth/avatar'

	query_string = request.query_string.decode().strip()
	target_url = f'{base_url}/{normalized_path}'
	if query_string:
		target_url = f'{target_url}?{query_string}'
	return target_url


def _is_avatar_oauth_proxy_path(proxy_path: str) -> bool:
	normalized_path = '/'.join(segment for segment in str(proxy_path or '').split('/') if segment not in ('', '.'))
	return normalized_path == 'oauth/avatar' or normalized_path.startswith('avatar/user/')


@main_bp.route('/skin-origin-proxy/<path:proxy_path>', methods=['GET', 'HEAD'])
@require_login_and_refresh(redirect_on_fail=False)
def proxy_same_origin_asset(proxy_path):
	"""Serve avatar and skin bytes from same-origin URLs under COEP/COOP.

	This keeps third-party asset trust decisions on the backend side. Avatar
	routes are special because older frontends still request a deprecated path.
	"""
	target_url = _build_asset_proxy_target(proxy_path)
	if not target_url:
		return jsonify({'data': None, 'error': 'Asset proxy target is invalid'}), 404

	headers = {'Accept': request.headers.get('Accept', 'image/*,*/*;q=0.8')}
	if _is_avatar_oauth_proxy_path(proxy_path):
		access_token = session.get('access_token', '').strip()
		if not access_token:
			# Logged-out requests cannot call the OAuth avatar API, so fall back to the
			# documented public default avatar instead of returning a broken image URL.
			target_url = _build_asset_proxy_target('public/default-avatar')
			if not target_url:
				return jsonify({'data': None, 'error': 'Missing access token for avatar proxy'}), 401
			try:
				upstream_response = _request_asset_proxy_upstream(target_url, headers)
			except requests.RequestException:
				return jsonify({'data': None, 'error': 'Asset proxy upstream request failed'}), 502
			return _finalize_proxy_response(upstream_response, target_url)
		headers['Authorization'] = f'Bearer {access_token}'

	try:
		upstream_response = _request_asset_proxy_upstream(target_url, headers)
	except requests.RequestException:
		return jsonify({'data': None, 'error': 'Asset proxy upstream request failed'}), 502

	if _is_avatar_oauth_proxy_path(proxy_path) and upstream_response.status_code == 404:
		# Some accounts do not have a custom avatar. In that case, mirror vSkin's
		# public default-avatar behavior instead of surfacing a 404 to the browser.
		fallback_url = _build_asset_proxy_target('public/default-avatar')
		if fallback_url:
			upstream_response.close()
			target_url = fallback_url
			try:
				upstream_response = _request_asset_proxy_upstream(target_url, {'Accept': headers['Accept']})
			except requests.RequestException:
				return jsonify({'data': None, 'error': 'Asset proxy upstream request failed'}), 502

	return _finalize_proxy_response(upstream_response, target_url)


