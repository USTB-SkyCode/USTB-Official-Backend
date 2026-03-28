# -*- coding: utf-8 -*-
import requests
from flask import Blueprint, current_app, session, jsonify, request
from flask import make_response
from marshmallow import ValidationError
from urllib.parse import urlsplit

from app.api.Schema import (
	FileDownloadAuditQuerySchema,
	FileCreateSchema,
	FileDownloadVerifyQuerySchema,
	FileListQuerySchema,
	FileUpdateSchema,
	McServerCreateSchema,
	McServerSortSchema,
	McServerStatusQuerySchema,
	McServerUpdateSchema,
	RssFeedEntryQuerySchema,
	RssFeedListQuerySchema,
	UserSchema,
)
from flask_wtf.csrf import generate_csrf

from app.services.ServerDataService import MCLocalStorage, LocalStorageError
from app.services.ServerDataService import normalize_status_payload
from app.services.Feed import FeedServiceError, FeedSyncConflictError, RssFeedService
from app.services.FileCatalog import FileCatalogAuthorizationError, FileCatalogError, FileCatalogService
from app.services.JobStatus import JobStatusError, JobStatusService
from app.services.McaDownload import McaDownloadAuthorizationError, McaDownloadAuthorizationService
from app.utils.same_origin_assets import build_ustb_texture_proxy_url, rewrite_avatar_url_for_same_origin
from app.utils.timezone import serialize_datetime_for_api

api_bp = Blueprint('api', __name__, url_prefix='/api')

from app.utils.auth import require_api_permission, verify_api_session

api_bp.before_request(verify_api_session)

user_schema = UserSchema()
mc_server_create_schema = McServerCreateSchema()
mc_server_update_schema = McServerUpdateSchema()
mc_server_sort_schema = McServerSortSchema()
mc_server_status_query_schema = McServerStatusQuerySchema()
rss_feed_list_query_schema = RssFeedListQuerySchema()
rss_feed_entry_query_schema = RssFeedEntryQuerySchema()
file_list_query_schema = FileListQuerySchema()
file_create_schema = FileCreateSchema()
file_update_schema = FileUpdateSchema()
file_download_verify_query_schema = FileDownloadVerifyQuerySchema()
file_download_audit_query_schema = FileDownloadAuditQuerySchema()


def _log_storage_error(message, exc):
	current_app.logger.warning('%s: %s', message, exc)


def _log_unexpected_error(message):
	current_app.logger.exception(message)


def _get_file_access_levels():
	permission = int(session.get('permission', 0) or 0)
	logged_in = bool(session.get('logged_in'))
	access_levels = ['public']
	if logged_in:
		access_levels.append('authenticated')
	if permission >= 1:
		access_levels.append('admin')
	return logged_in, permission, access_levels


def _sanitize_mc_status_payload(payload, *, expose_ip: bool, include_icon: bool):
	if payload is None:
		return payload
	if isinstance(payload, dict):
		blocked_keys = set()
		if not expose_ip:
			blocked_keys.update({'host', 'ip', 'port'})
		if not include_icon:
			blocked_keys.update({'favicon', 'icon'})
		return {
			key: _sanitize_mc_status_payload(value, expose_ip=expose_ip, include_icon=include_icon)
			for key, value in payload.items()
			if key not in blocked_keys
		}
	if isinstance(payload, list):
		return [_sanitize_mc_status_payload(item, expose_ip=expose_ip, include_icon=include_icon) for item in payload]
	return payload


def _build_download_audit_actor():
	return {
		'user_id': session.get('user_id'),
		'username': session.get('username'),
		'permission': int(session.get('permission', 0) or 0),
		'remote_addr': (request.headers.get('X-Forwarded-For', '').split(',')[0].strip() or request.remote_addr or '').strip() or None,
	}


def _record_download_audit(service, **kwargs):
	if service is None or not hasattr(service, 'record_download_audit'):
		return
	try:
		service.record_download_audit(**kwargs)
	except FileCatalogError as exc:
		_log_storage_error('Failed to record file download audit', exc)


def _empty_skin_response():
	return {
		'skin_url': '',
		'skin_version': '',
		'skin_model': '',
	}


def _build_vskin_texture_url(skin_version):
	"""Return the same-origin URL used by the frontend to fetch the active skin."""
	if not skin_version:
		return ''
	return build_ustb_texture_proxy_url(skin_version)


@api_bp.route('/jobs/statuses', methods=['GET'])
@require_api_permission(1)
def get_job_status():
	service = None
	try:
		service = JobStatusService()
		return jsonify({"data": service.list_statuses(), "error": None})
	except JobStatusError as e:
		_log_storage_error('Failed to query job status', e)
		return jsonify({"data": None, "error": '任务状态读取失败'}), 500
	finally:
		if service:
			service.close()

# 获取当前用户信息
@api_bp.route('/users/me', methods=['GET'])
def get_current_user():
	"""Return the current session profile with browser-safe avatar URLs."""
	provider = session.get('oauth_provider')
	user_data = {
		'user_id': session.get('user_id'),
		'username': session.get('username'),
		'email': session.get('email'),
		'avatar_url': rewrite_avatar_url_for_same_origin(
			session.get('avatar_url'),
			provider=provider,
		),
		'login_time': session.get('login_time'),
		'provider': provider,
		'permission': session.get('permission', 0)
	}
	# 使用 schema 序列化和转义
	return jsonify({"data": user_schema.dump(user_data), "error": None})


@api_bp.route('/users/me/skin', methods=['GET'])
def get_current_user_skin():
	"""Return the active USTB skin as a same-origin URL plus version and model metadata."""
	provider = session.get('oauth_provider')
	if provider != 'ustb':
		return jsonify({"data": _empty_skin_response(), "error": None})

	access_token = session.get('access_token')
	provider_config = current_app.config.get('OAUTH_PROVIDERS', {}).get('ustb', {})
	if not access_token:
		return jsonify({"data": _empty_skin_response(), "error": None})

	skin_endpoint = provider_config.get('skin_url') or 'https://skin.ustb.world/skinapi/oauth/skin'
	response = None
	try:
		response = requests.get(
			skin_endpoint,
			headers={'Authorization': f'Bearer {access_token}'},
			timeout=10,
			stream=True,
			allow_redirects=False,
		)
		if response.status_code == 404:
			return jsonify({"data": _empty_skin_response(), "error": None})
		if response.status_code != 200:
			current_app.logger.warning('USTB skin endpoint returned status %s', response.status_code)
			return jsonify({"data": None, "error": '获取皮肤信息失败'}), 502

		skin_version = (response.headers.get('X-VSkin-Skin-Hash') or '').strip()
		skin_model = (response.headers.get('X-VSkin-Skin-Model') or '').strip()
		return jsonify({
			"data": {
				'skin_url': _build_vskin_texture_url(skin_version),
				'skin_version': skin_version,
				'skin_model': skin_model,
			},
			"error": None,
		})
	except requests.RequestException as exc:
		current_app.logger.warning('Failed to fetch USTB skin metadata: %s', exc)
		return jsonify({"data": None, "error": '获取皮肤信息失败'}), 502
	finally:
		if response is not None:
			response.close()

# 登出
@api_bp.route('/session', methods=['DELETE'])
def logout():
	session.clear()
	return jsonify({"data": {"success": True, "message": "Logged out"}, "error": None})


# --- 添加 CSRF token endpoint ---
@api_bp.route('/session/csrf-token', methods=['GET'])
def get_csrf_token():
	token = generate_csrf()
	resp = make_response(jsonify({'csrfToken': token}))
	resp.set_cookie(
		'XSRF-TOKEN',
		token,
		secure=current_app.config.get('SESSION_COOKIE_SECURE', True),
		samesite=current_app.config.get('SESSION_COOKIE_SAMESITE', 'Lax'),
		httponly=False,
	)
	return resp


# --- Servers 管理 API ---
@api_bp.route('/mc-servers', methods=['GET'])
@require_api_permission(1)
def get_mc_servers():
	"""查询所有 MC 服务器"""
	storage = None
	try:
		storage = MCLocalStorage()
		rows = storage.query_mc_server()
		return jsonify({"data": rows, "error": None})
	except LocalStorageError as e:
		_log_storage_error('Failed to query MC servers', e)
		return jsonify({"data": None, "error": '服务器列表读取失败'}), 500
	finally:
		if storage:
			storage.close()

@api_bp.route('/mc-servers', methods=['POST'])
@require_api_permission(1)
def create_mc_server():
	"""创建新的 MC 服务器资源"""
	storage = None
	try:
		payload = request.get_json(silent=True)
		if payload is None:
			return jsonify({"data": None, "error": '请求体必须为 JSON'}), 400

		data = mc_server_create_schema.load(payload)
		storage = MCLocalStorage()
		row = storage.create_mc_server(
			ip=data.get('ip'),
			name=data.get('name'),
			expose_ip=data.get('expose_ip'),
		)
		return jsonify({"data": row, "error": None}), 201
	except ValidationError as e:
		return jsonify({"data": None, "error": e.messages}), 400
	except LocalStorageError as e:
		_log_storage_error('Failed to create MC server', e)
		return jsonify({"data": None, "error": '服务器写入失败'}), 500
	except Exception:
		_log_unexpected_error('Unexpected error while creating MC server')
		return jsonify({"data": None, "error": '服务器写入失败'}), 500
	finally:
		if storage:
			storage.close()


@api_bp.route('/mc-servers/<int:id>', methods=['PATCH'])
@require_api_permission(1)
def update_mc_server(id):
	"""按 id 更新 MC 服务器资源"""
	storage = None
	try:
		payload = request.get_json(silent=True)
		if payload is None:
			return jsonify({"data": None, "error": '请求体必须为 JSON'}), 400

		data = mc_server_update_schema.load(payload)
		storage = MCLocalStorage()
		row = storage.update_mc_server(
			server_id=id,
			ip=data.get('ip'),
			name=data.get('name'),
			expose_ip=data.get('expose_ip'),
		)
		if row is None:
			return jsonify({"data": None, "error": '服务器不存在'}), 404
		return jsonify({"data": row, "error": None})
	except ValidationError as e:
		return jsonify({"data": None, "error": e.messages}), 400
	except LocalStorageError as e:
		_log_storage_error('Failed to update MC server', e)
		return jsonify({"data": None, "error": '服务器写入失败'}), 500
	except Exception:
		_log_unexpected_error('Unexpected error while updating MC server')
		return jsonify({"data": None, "error": '服务器写入失败'}), 500
	finally:
		if storage:
			storage.close()

@api_bp.route('/mc-servers/<int:id>', methods=['DELETE'])
@require_api_permission(1)
def delete_mc_server_by_id(id):
	"""按 id 删除服务器"""
	storage = None
	try:
		storage = MCLocalStorage()
		count = storage.delete_mc_server(id=id)
		if count == 0:
			return jsonify({"data": None, "error": '服务器不存在'}), 404
		return jsonify({"data": {"deleted": count}, "error": None})
	except LocalStorageError as e:
		_log_storage_error('Failed to delete MC server by id', e)
		return jsonify({"data": None, "error": '服务器删除失败'}), 500
	finally:
		if storage:
			storage.close()

@api_bp.route('/mc-servers/order', methods=['PUT'])
@require_api_permission(1)
def sort_mc_servers():
	"""按 id_list 排序服务器表"""
	storage = None
	try:
		payload = request.get_json(silent=True)
		if payload is None:
			return jsonify({"data": None, "error": '请求体必须为 JSON'}), 400

		data = mc_server_sort_schema.load(payload)
		storage = MCLocalStorage()
		count = storage.sort_mc_servers(data['id_list'])
		return jsonify({"data": {"sorted": count}, "error": None})
	except ValidationError as e:
		return jsonify({"data": None, "error": e.messages}), 400
	except LocalStorageError as e:
		_log_storage_error('Failed to sort MC servers', e)
		return jsonify({"data": None, "error": '服务器排序失败'}), 500
	except Exception:
		_log_unexpected_error('Unexpected error while sorting MC servers')
		return jsonify({"data": None, "error": '服务器排序失败'}), 500
	finally:
		if storage:
			storage.close()


# --- MC Server Status API ---
@api_bp.route('/mc-servers/statuses', methods=['GET'])
def get_all_mc_server_status():
	"""获取所有 MC 服务器状态信息"""
	storage = None
	try:
		query = mc_server_status_query_schema.load(request.args)
		include_icon = query['include_icon']
		storage = MCLocalStorage()
		with storage.conn.cursor() as cursor:
			cursor.execute(
				'''
				SELECT s.ip, ss.status, ss.last_update, COALESCE(s.name, ss.name) AS name, s.expose_ip
				FROM servers s
				LEFT JOIN server_status ss ON ss.ip = s.ip
				ORDER BY s.id ASC;
				'''
			)
			rows = cursor.fetchall()

		serialized_rows = []
		for row in rows:
			expose_ip = bool(row.get('expose_ip', False))
			status_payload = _sanitize_mc_status_payload(
				normalize_status_payload(row.get('status')),
				expose_ip=expose_ip,
				include_icon=include_icon,
			)
			serialized_rows.append({
				'ip': row.get('ip') if expose_ip else None,
				'status': status_payload,
				'last_update': serialize_datetime_for_api(row.get('last_update')),
				'name': row.get('name'),
				'expose_ip': expose_ip,
			})
		return jsonify({"data": serialized_rows, "error": None})
	except ValidationError as e:
		return jsonify({"data": None, "error": e.messages}), 400
	except Exception:
		_log_unexpected_error('Failed to query MC server status')
		return jsonify({"data": None, "error": '服务器状态读取失败'}), 500
	finally:
		if storage:
			storage.close()


@api_bp.route('/rss-feeds', methods=['GET'])
def get_rss_feeds():
	service = None
	try:
		query = rss_feed_list_query_schema.load(request.args)
		service = RssFeedService()
		return jsonify({"data": service.list_feeds(limit=query['limit'], offset=query['offset']), "error": None})
	except ValidationError as e:
		return jsonify({"data": None, "error": e.messages}), 400
	except FeedServiceError as e:
		_log_storage_error('Failed to query RSS feeds', e)
		return jsonify({"data": None, "error": 'RSS 订阅读取失败'}), 500
	finally:
		if service:
			service.close()


@api_bp.route('/rss-entries', methods=['GET'])
def get_rss_entries():
	service = None
	try:
		query = rss_feed_entry_query_schema.load(request.args)
		service = RssFeedService()
		return jsonify({"data": service.list_entries(feed_id=query.get('feed_id'), limit=query['limit'], offset=query['offset']), "error": None})
	except ValidationError as e:
		return jsonify({"data": None, "error": e.messages}), 400
	except FeedServiceError as e:
		_log_storage_error('Failed to query RSS entries', e)
		return jsonify({"data": None, "error": 'RSS 内容读取失败'}), 500
	finally:
		if service:
			service.close()


@api_bp.route('/files', methods=['GET'])
def get_files():
	service = None
	try:
		query = file_list_query_schema.load(request.args)
		logged_in, permission, access_levels = _get_file_access_levels()
		include_inactive = bool(query.get('include_inactive'))
		if include_inactive and permission < 1:
			return jsonify({"data": None, "error": 'Forbidden'}), 403

		service = FileCatalogService()
		return jsonify({
			"data": service.list_files(
				access_levels=access_levels,
				limit=query['limit'],
				offset=query['offset'],
				include_inactive=include_inactive,
			),
			"error": None,
		})
	except ValidationError as e:
		return jsonify({"data": None, "error": e.messages}), 400
	except FileCatalogError as e:
		_log_storage_error('Failed to query file catalog', e)
		return jsonify({"data": None, "error": '文件目录读取失败'}), 500
	finally:
		if service:
			service.close()


@api_bp.route('/files', methods=['POST'])
@require_api_permission(1)
def create_file():
	service = None
	try:
		payload = request.get_json(silent=True)
		if payload is None:
			return jsonify({"data": None, "error": '请求体必须为 JSON'}), 400

		data = file_create_schema.load(payload)
		service = FileCatalogService()
		row = service.create_file(**data)
		return jsonify({"data": row, "error": None}), 201
	except ValidationError as e:
		return jsonify({"data": None, "error": e.messages}), 400
	except FileCatalogError as e:
		_log_storage_error('Failed to create file catalog entry', e)
		return jsonify({"data": None, "error": '文件目录写入失败'}), 500
	finally:
		if service:
			service.close()


@api_bp.route('/files/<int:file_id>', methods=['PATCH'])
@require_api_permission(1)
def update_file(file_id):
	service = None
	try:
		payload = request.get_json(silent=True)
		if payload is None:
			return jsonify({"data": None, "error": '请求体必须为 JSON'}), 400

		data = file_update_schema.load(payload)
		service = FileCatalogService()
		row = service.update_file(file_id, **data)
		if row is None:
			return jsonify({"data": None, "error": '文件不存在'}), 404
		return jsonify({"data": row, "error": None})
	except ValidationError as e:
		return jsonify({"data": None, "error": e.messages}), 400
	except FileCatalogError as e:
		_log_storage_error('Failed to update file catalog entry', e)
		return jsonify({"data": None, "error": '文件目录写入失败'}), 500
	finally:
		if service:
			service.close()


@api_bp.route('/files/<int:file_id>/download-token', methods=['GET'])
def issue_file_download_token(file_id):
	service = None
	try:
		logged_in, permission, _ = _get_file_access_levels()
		audit_actor = _build_download_audit_actor()
		service = FileCatalogService()
		result = service.issue_download_token(file_id, logged_in=logged_in, permission=permission)
		file_payload = result.get('file') or {}
		_record_download_audit(service,
			action='issue_token',
			outcome='success',
			file_id=file_payload.get('id', file_id),
			storage_key=file_payload.get('storage_key'),
			user_id=audit_actor['user_id'],
			username=audit_actor['username'],
			permission=audit_actor['permission'],
			remote_addr=audit_actor['remote_addr'],
			details={'visibility': file_payload.get('visibility')},
		)
		return jsonify({"data": result, "error": None})
	except FileCatalogAuthorizationError as e:
		if service:
			audit_actor = _build_download_audit_actor()
			_record_download_audit(service,
				action='issue_token',
				outcome='denied',
				file_id=file_id,
				user_id=audit_actor['user_id'],
				username=audit_actor['username'],
				permission=audit_actor['permission'],
				remote_addr=audit_actor['remote_addr'],
				error_message=str(e),
			)
		return jsonify({"data": None, "error": str(e)}), e.status_code
	except FileCatalogError as e:
		_log_storage_error('Failed to issue file download token', e)
		return jsonify({"data": None, "error": '下载授权失败'}), 500
	finally:
		if service:
			service.close()


@api_bp.route('/file-downloads/verify', methods=['GET'])
def verify_file_download_token():
	service = None
	try:
		query = file_download_verify_query_schema.load(request.args)
		service = FileCatalogService()
		result = service.verify_download_token(query['token'])
		return jsonify({"data": result, "error": None})
	except ValidationError as e:
		return jsonify({"data": None, "error": e.messages}), 400
	except FileCatalogAuthorizationError as e:
		return jsonify({"data": None, "error": str(e)}), e.status_code
	except FileCatalogError as e:
		_log_storage_error('Failed to verify file download token', e)
		return jsonify({"data": None, "error": '下载授权校验失败'}), 500
	finally:
		if service:
			service.close()


@api_bp.route('/file-downloads/authorize', methods=['GET'])
def authorize_file_download():
	service = None
	try:
		logged_in, permission, _ = _get_file_access_levels()
		audit_actor = _build_download_audit_actor()
		forwarded_uri = request.headers.get('X-Forwarded-Uri', '').strip()
		if not forwarded_uri:
			parsed = urlsplit(request.url)
			query_string = parsed.query or ''
			token = request.args.get('token')
			fallback_query = query_string
			if token and 'token=' not in fallback_query:
				fallback_query = f'token={token}'
			forwarded_uri = f"{current_app.config['FILE_DOWNLOAD_BASE_PATH'].rstrip('/')}/{request.args.get('path', '').lstrip('/')}"
			if fallback_query:
				forwarded_uri = f'{forwarded_uri}?{fallback_query}'

		service = FileCatalogService()
		result = service.authorize_download_request(
			forwarded_uri,
			logged_in=logged_in,
			permission=permission,
		)
		file_payload = result.get('file') or {}
		_record_download_audit(service,
			action='authorize',
			outcome='success',
			file_id=file_payload.get('id'),
			storage_key=file_payload.get('storage_key'),
			user_id=audit_actor['user_id'],
			username=audit_actor['username'],
			permission=audit_actor['permission'],
			remote_addr=audit_actor['remote_addr'],
			forwarded_uri=forwarded_uri,
		)
		return jsonify({"data": result, "error": None})
	except FileCatalogAuthorizationError as e:
		if service:
			audit_actor = _build_download_audit_actor()
			_record_download_audit(service,
				action='authorize',
				outcome='denied',
				user_id=audit_actor['user_id'],
				username=audit_actor['username'],
				permission=audit_actor['permission'],
				remote_addr=audit_actor['remote_addr'],
				forwarded_uri=request.headers.get('X-Forwarded-Uri', '').strip() or None,
				error_message=str(e),
			)
		return jsonify({"data": None, "error": str(e)}), e.status_code
	except FileCatalogError as e:
		_log_storage_error('Failed to authorize file download', e)
		return jsonify({"data": None, "error": '下载鉴权失败'}), 500
	finally:
		if service:
			service.close()


@api_bp.route('/mca-downloads/authorize', methods=['GET'])
def authorize_mca_download():
	try:
		logged_in, permission, _ = _get_file_access_levels()
		forwarded_uri = request.headers.get('X-Forwarded-Uri', '').strip()
		if not forwarded_uri:
			fallback_path = request.args.get('path', '').strip()
			if fallback_path:
				forwarded_uri = fallback_path if fallback_path.startswith('/') else f'/{fallback_path.lstrip("/")}'

		service = McaDownloadAuthorizationService()
		result = service.authorize_download_request(
			forwarded_uri,
			logged_in=logged_in,
			permission=permission,
		)
		return jsonify({"data": result, "error": None})
	except McaDownloadAuthorizationError as e:
		return jsonify({"data": None, "error": str(e)}), e.status_code


@api_bp.route('/file-downloads/audits', methods=['GET'])
@require_api_permission(1)
def get_file_download_audits():
	service = None
	try:
		query = file_download_audit_query_schema.load(request.args)
		service = FileCatalogService()
		return jsonify({
			"data": service.list_download_audits(
				file_id=query.get('file_id'),
				action=query.get('action'),
				outcome=query.get('outcome'),
				limit=query['limit'],
				offset=query['offset'],
			),
			"error": None,
		})
	except ValidationError as e:
		return jsonify({"data": None, "error": e.messages}), 400
	except FileCatalogError as e:
		_log_storage_error('Failed to query file download audits', e)
		return jsonify({"data": None, "error": '下载审计读取失败'}), 500
	finally:
		if service:
			service.close()


@api_bp.route('/rss-feeds/sync', methods=['POST'])
@require_api_permission(1)
def sync_rss_feed():
	service = None
	try:
		service = RssFeedService()
		result = service.sync_configured_feed()
		return jsonify({"data": result, "error": None})
	except FeedSyncConflictError as e:
		return jsonify({"data": None, "error": str(e)}), 409
	except FeedServiceError as e:
		_log_storage_error('Failed to sync RSS feed', e)
		return jsonify({"data": None, "error": 'RSS 同步失败'}), 500
	finally:
		if service:
			service.close()