from datetime import datetime

from psycopg2.extras import Json

from app import create_app
import app.routes.main as main_routes
from app.routes.auth import sanitize_user_info
from app.routes import api as api_routes
from app.services.Feed import FeedSyncConflictError, RssFeedRefreshManager, RssFeedService
from app.services.FileCatalog import FileCatalogAuthorizationError, FileCatalogService
from app.services.JobStatus import JobStatusService
from app.services.McaDownload import McaDownloadAuthorizationError, McaDownloadAuthorizationService
from app.services.ServerDataService import MCLocalStorage, normalize_status_payload
from app.utils.auth import load_oauth_providers_from_env
from app.utils.serverStatus import query_server_status


def _issue_csrf(client):
	response = client.get('/api/session/csrf-token')
	assert response.status_code == 200
	return response.get_json()['csrfToken']


def _login_session(client, permission=0):
	with client.session_transaction() as sess:
		sess['logged_in'] = True
		sess['permission'] = permission
		sess['oauth_provider'] = 'dev'
		sess['user_id'] = 'test-user'
		sess['username'] = 'tester'
		sess['email'] = 'tester@example.com'


def _client_with_app():
	app = create_app()
	app.config.update(
		TESTING=True,
		DEBUG=True,
		DEV_AUTH_ENABLED=True,
		DEV_AUTH_REQUIRE_DEBUG=True,
		DEV_AUTH_SHARED_SECRET='test-secret',
	)
	return app, app.test_client()


def test_dev_login_rejects_missing_shared_secret():
	app, client = _client_with_app()
	with app.app_context():
		csrf_token = _issue_csrf(client)
		response = client.post(
			'/auth/dev-login',
			json={'preset': 'admin'},
			headers={
				'Origin': 'https://app-dev.nand.cloud',
				'X-CSRFToken': csrf_token,
			},
			environ_base={'REMOTE_ADDR': '127.0.0.1'},
		)

		assert response.status_code == 403


def test_dev_login_accepts_valid_shared_secret():
	app, client = _client_with_app()
	with app.app_context():
		csrf_token = _issue_csrf(client)
		response = client.post(
			'/auth/dev-login',
			json={'preset': 'admin'},
			headers={
				'Origin': 'https://app-dev.nand.cloud',
				'X-CSRFToken': csrf_token,
				'X-Dev-Auth-Secret': 'test-secret',
			},
			environ_base={'REMOTE_ADDR': '127.0.0.1'},
		)

		assert response.status_code == 200
		payload = response.get_json()['data']['user']
		assert payload['permission'] == 2


def test_healthz_is_public():
	app, client = _client_with_app()
	with app.app_context():
		response = client.get('/healthz')

		assert response.status_code == 200
		assert response.get_json()['status'] == 'ok'


def test_cross_origin_isolation_diagnostics_json_is_public():
	app, client = _client_with_app()
	with app.app_context():
		response = client.get('/diagnostics/cross-origin-isolation.json')

		assert response.status_code == 200
		payload = response.get_json()
		assert payload['error'] is None
		assert payload['data']['path'] == '/diagnostics/cross-origin-isolation.json'
		assert 'expected_browser_signals' in payload['data']['notes']


def test_cross_origin_isolation_diagnostics_page_is_public():
	app, client = _client_with_app()
	with app.app_context():
		response = client.get('/diagnostics/cross-origin-isolation')

		assert response.status_code == 200
		assert 'text/html' in response.content_type
		assert b'crossOriginIsolated' in response.data
		assert b'SharedArrayBuffer' in response.data


def test_runtime_config_js_uses_startup_snapshot():
	app, client = _client_with_app()
	app.config.update(
		RUNTIME_CONFIG_API_BASE_URL='https://api.example.test/',
		RUNTIME_CONFIG_AUTH_BASE_URL='',
		RUNTIME_CONFIG_APP_BASE_URL='https://app.example.test/',
		RUNTIME_CONFIG_SKIN_API_BASE_URL='https://skin.example.test/skinapi/',
		RUNTIME_CONFIG_MCA_BASE_URL='/resource/mca/world/',
		RUNTIME_CONFIG_MODEL_BASE_URL='/model/',
		RUNTIME_CONFIG_MODEL_COMPILED_BASE_URL='/model/compiled/',
		RUNTIME_CONFIG_MODEL_ASSET_BASE_URL='/model/assets/',
		RUNTIME_CONFIG_BASIC_BASE_URL='/basic/',
		RUNTIME_CONFIG_BASIC_COMPILED_BASE_URL='/basic/compiled/',
		RUNTIME_CONFIG_BASIC_ASSET_BASE_URL='/basic/assets/',
		RUNTIME_CONFIG_SKIN_BASE_URL='/assets/skin/',
		RUNTIME_CONFIG_DEV_BACKEND_PROXY_ENABLED='true',
	)
	main_routes.prepare_runtime_app_config(app)

	response = client.get('/config.js', base_url='https://ignored-by-startup.example.test')

	assert response.status_code == 200
	assert 'application/javascript' in response.content_type
	assert response.headers['Cache-Control'] == 'no-store, no-cache, must-revalidate, max-age=0'
	assert response.headers['Pragma'] == 'no-cache'
	assert response.get_data(as_text=True) == (
		'window.APP_CONFIG = '
		'{"API_BASE_URL": "https://api.example.test", '
		'"AUTH_BASE_URL": "https://api.example.test", '
		'"APP_BASE_URL": "https://app.example.test", '
		'"SKIN_API_BASE_URL": "https://skin.example.test/skinapi", '
		'"MCA_BASE_URL": "/resource/mca/world", '
		'"MODEL_BASE_URL": "/model", '
		'"MODEL_COMPILED_BASE_URL": "/model/compiled", '
		'"MODEL_ASSET_BASE_URL": "/model/assets", '
		'"BASIC_BASE_URL": "/basic", '
		'"BASIC_COMPILED_BASE_URL": "/basic/compiled", '
		'"BASIC_ASSET_BASE_URL": "/basic/assets", '
		'"SKIN_BASE_URL": "/assets/skin", '
		'"DEV_BACKEND_PROXY_ENABLED": true};\n'
	)


def test_user_skin_requires_login():
	app, client = _client_with_app()
	with app.app_context():
		response = client.get('/api/users/me/skin')

		assert response.status_code == 401
		assert response.get_json() == {'data': None, 'error': 'Not logged in'}


def test_user_skin_returns_vskin_url_from_headers(monkeypatch):
	app, client = _client_with_app()

	class DummyResponse:
		status_code = 200
		headers = {
			'X-VSkin-Skin-Hash': 'abc123hash',
			'X-VSkin-Profile-Id': '1',
		}

		def close(self):
			pass

	captured = {}

	def fake_get(url, headers=None, timeout=None, stream=None, allow_redirects=None):
		captured['url'] = url
		captured['headers'] = headers
		captured['timeout'] = timeout
		captured['stream'] = stream
		captured['allow_redirects'] = allow_redirects
		return DummyResponse()

	monkeypatch.setattr(api_routes.requests, 'get', fake_get)

	with app.app_context():
		app.config['OAUTH_PROVIDERS']['ustb']['base_url'] = 'https://skin.ustb.world'
		app.config['OAUTH_PROVIDERS']['ustb']['skin_url'] = 'https://skin.ustb.world/skinapi/oauth/skin'
		_login_session(client, permission=0)
		with client.session_transaction() as sess:
			sess['oauth_provider'] = 'ustb'
			sess['access_token'] = 'access-token-1'
			sess['last_refresh_time'] = datetime.now().isoformat()
		response = client.get('/api/users/me/skin')

		assert response.status_code == 200
		assert response.get_json() == {
			'data': {
				'skin_url': '/skin-origin-proxy/static/textures/abc123hash.png',
				'skin_version': 'abc123hash',
			},
			'error': None,
		}
		assert captured['url'] == 'https://skin.ustb.world/skinapi/oauth/skin'
		assert captured['headers']['Authorization'] == 'Bearer access-token-1'
		assert captured['stream'] is True
		assert captured['allow_redirects'] is False


def test_user_skin_returns_empty_on_not_found(monkeypatch):
	app, client = _client_with_app()

	class DummyResponse:
		status_code = 404
		headers = {}

		def close(self):
			pass

	monkeypatch.setattr(api_routes.requests, 'get', lambda *args, **kwargs: DummyResponse())

	with app.app_context():
		_login_session(client, permission=0)
		with client.session_transaction() as sess:
			sess['oauth_provider'] = 'ustb'
			sess['access_token'] = 'access-token-1'
			sess['last_refresh_time'] = datetime.now().isoformat()
		response = client.get('/api/users/me/skin')

		assert response.status_code == 200
		assert response.get_json() == {
			'data': {
				'skin_url': '',
				'skin_version': '',
			},
			'error': None,
		}


def test_user_skin_returns_empty_for_non_ustb_provider(monkeypatch):
	app, client = _client_with_app()

	def fail_get(*args, **kwargs):
		raise AssertionError('skin API should not be called for non-ustb providers')

	monkeypatch.setattr(api_routes.requests, 'get', fail_get)

	with app.app_context():
		_login_session(client, permission=0)
		with client.session_transaction() as sess:
			sess['oauth_provider'] = 'github'
			sess['access_token'] = 'access-token-1'
			sess['last_refresh_time'] = datetime.now().isoformat()
		response = client.get('/api/users/me/skin')

		assert response.status_code == 200
		assert response.get_json() == {
			'data': {
				'skin_url': '',
				'skin_version': '',
			},
			'error': None,
		}


def test_sanitize_user_info_rewrites_ustb_avatar_to_same_origin_proxy():
	app, _ = _client_with_app()
	with app.app_context():
		payload = sanitize_user_info({'sub': '42', 'username': 'tester', 'email': 'tester@example.com'}, 'ustb')
		assert payload['avatar_url'] == '/skin-origin-proxy/oauth/avatar'


def test_sanitize_user_info_rewrites_allowed_external_avatar_to_same_origin_proxy():
	app, _ = _client_with_app()
	with app.app_context():
		payload = sanitize_user_info({
			'id': '7',
			'login': 'octocat',
			'avatar_url': 'https://avatars.githubusercontent.com/u/7?v=4',
		}, 'github')
		assert payload['avatar_url'].startswith('/skin-origin-proxy/external?url=https%3A%2F%2Favatars.githubusercontent.com%2Fu%2F7%3Fv%3D4')


def test_same_origin_asset_proxy_rejects_disallowed_external_host():
	app, client = _client_with_app()
	with app.app_context():
		_login_session(client, permission=0)
		response = client.get('/skin-origin-proxy/external?url=https%3A%2F%2Fevil.example%2Favatar.png')
		assert response.status_code == 404


def test_same_origin_asset_proxy_streams_allowed_external_host(monkeypatch):
	app, client = _client_with_app()

	class DummyResponse:
		status_code = 200
		content = b'png-bytes'
		headers = {
			'Content-Type': 'image/png',
			'Cache-Control': 'public, max-age=60',
		}

	def fake_request(method, url, headers=None, timeout=None, allow_redirects=None):
		assert method == 'GET'
		assert url == 'https://avatars.githubusercontent.com/u/1'
		return DummyResponse()

	monkeypatch.setattr(main_routes.requests, 'request', fake_request)

	with app.app_context():
		_login_session(client, permission=0)
		response = client.get('/skin-origin-proxy/external?url=https%3A%2F%2Favatars.githubusercontent.com%2Fu%2F1')
		assert response.status_code == 200
		assert response.data == b'png-bytes'
		assert response.headers['Content-Type'] == 'image/png'


def test_mc_server_write_requires_admin_permission():
	app, client = _client_with_app()
	with app.app_context():
		_login_session(client, permission=0)
		csrf_token = _issue_csrf(client)
		response = client.post(
			'/api/mc-servers',
			json={'ip': 'mc.example.com'},
			headers={'X-CSRFToken': csrf_token},
		)

		assert response.status_code == 403


def test_job_status_requires_admin_permission():
	app, client = _client_with_app()
	with app.app_context():
		_login_session(client, permission=0)
		response = client.get('/api/jobs/statuses')

		assert response.status_code == 403


def test_job_status_returns_persisted_items(monkeypatch):
	app, client = _client_with_app()

	class DummyJobStatusService:
		def list_statuses(self):
			return {
				'items': [
					{
						'job_name': 'mc_status_refresh',
						'is_running': False,
						'last_success_at': '2026-03-16T15:30:00+08:00',
						'last_error_message': None,
					}
				],
				'total': 1,
			}

		def close(self):
			pass

	monkeypatch.setattr(api_routes, 'JobStatusService', DummyJobStatusService)

	with app.app_context():
		_login_session(client, permission=2)
		response = client.get('/api/jobs/statuses')

		assert response.status_code == 200
		payload = response.get_json()['data']
		assert payload['total'] == 1
		assert payload['items'][0]['job_name'] == 'mc_status_refresh'
def test_mc_server_payload_validates_before_storage(monkeypatch):
	app, client = _client_with_app()
	storage_called = {'value': False}

	class DummyStorage:
		def __init__(self):
			storage_called['value'] = True

	monkeypatch.setattr(api_routes, 'MCLocalStorage', DummyStorage)

	with app.app_context():
		_login_session(client, permission=2)
		csrf_token = _issue_csrf(client)
		response = client.post(
			'/api/mc-servers',
			json={},
			headers={'X-CSRFToken': csrf_token},
		)

		assert response.status_code == 400
		assert storage_called['value'] is False


def test_mc_server_create_rejects_id_field(monkeypatch):
	app, client = _client_with_app()

	class DummyStorage:
		def __init__(self):
			raise AssertionError('storage should not be initialized for invalid create payload')

	monkeypatch.setattr(api_routes, 'MCLocalStorage', DummyStorage)

	with app.app_context():
		_login_session(client, permission=2)
		csrf_token = _issue_csrf(client)
		response = client.post(
			'/api/mc-servers',
			json={'id': 1, 'ip': 'mc.example.com'},
			headers={'X-CSRFToken': csrf_token},
		)

		assert response.status_code == 400


def test_mc_storage_assigns_next_id_when_missing(monkeypatch):
	class FakeCursor:
		def __init__(self):
			self.fetchone_results = [
				{'next_id': 3},
				{'id': 3, 'ip': 'mc.example.com', 'name': 'Example', 'expose_ip': True},
			]
			self.executed = []
			self.last_sql = ''

		def execute(self, sql, params=None):
			self.last_sql = sql
			self.executed.append((sql, params))

		def fetchall(self):
			if 'FROM information_schema.columns' in self.last_sql:
				return [{'column_name': 'status', 'data_type': 'jsonb'}]
			return []

		def fetchone(self):
			return self.fetchone_results.pop(0)

		def __enter__(self):
			return self

		def __exit__(self, exc_type, exc, tb):
			return False

	class FakeConn:
		def __init__(self):
			self.cursor_instance = FakeCursor()
			self.commit_count = 0

		def cursor(self):
			return self.cursor_instance

		def commit(self):
			self.commit_count += 1

		def rollback(self):
			pass

	fake_conn = FakeConn()

	def fake_local_storage_init(self, host=None, user=None, password=None, db=None, port=None):
		self.conn = fake_conn

	monkeypatch.setattr('app.services.LocalStorage.LocalStorage.__init__', fake_local_storage_init)

	storage = MCLocalStorage()
	row = storage.insert_mc_server(ip='mc.example.com', name='Example', expose_ip=True)

	assert row == {'id': 3, 'ip': 'mc.example.com', 'name': 'Example', 'expose_ip': True}
	assert any('LOCK TABLE servers IN EXCLUSIVE MODE' in sql for sql, _ in fake_conn.cursor_instance.executed)
	assert any(params == (3, 'mc.example.com', 'Example', True) for _, params in fake_conn.cursor_instance.executed)


def test_mc_server_update_uses_patch_and_returns_404_when_missing(monkeypatch):
	app, client = _client_with_app()

	class DummyStorage:
		def update_mc_server(self, *, server_id, ip=None, name=None, expose_ip=None):
			assert server_id == 7
			assert name == 'Renamed'
			assert ip is None
			assert expose_ip is None
			return None

		def close(self):
			pass

	monkeypatch.setattr(api_routes, 'MCLocalStorage', DummyStorage)

	with app.app_context():
		_login_session(client, permission=2)
		csrf_token = _issue_csrf(client)
		response = client.patch(
			'/api/mc-servers/7',
			json={'name': 'Renamed'},
			headers={'X-CSRFToken': csrf_token},
		)

		assert response.status_code == 404


def test_mc_server_delete_by_id_returns_404_when_missing(monkeypatch):
	app, client = _client_with_app()

	class DummyStorage:
		def delete_mc_server(self, *, id):
			assert id == 4
			return 0

		def close(self):
			pass

	monkeypatch.setattr(api_routes, 'MCLocalStorage', DummyStorage)

	with app.app_context():
		_login_session(client, permission=2)
		csrf_token = _issue_csrf(client)
		response = client.delete(
			'/api/mc-servers/4',
			headers={'X-CSRFToken': csrf_token},
		)

		assert response.status_code == 404


def test_mc_storage_migrates_legacy_server_status_text_to_jsonb(monkeypatch):
	class FakeCursor:
		def __init__(self):
			self.executed = []
			self.last_sql = ''

		def execute(self, sql, params=None):
			self.last_sql = sql
			self.executed.append((sql, params))

		def fetchall(self):
			if 'FROM information_schema.columns' in self.last_sql:
				return [{'column_name': 'status', 'data_type': 'text'}]
			if 'SELECT ip, status FROM server_status' in self.last_sql:
				return [{'ip': 'mc.example.com:25565', 'status': "{'status': 'online', 'type': 'Java'}"}]
			return []

		def __enter__(self):
			return self

		def __exit__(self, exc_type, exc, tb):
			return False

	class FakeConn:
		def __init__(self):
			self.cursor_instance = FakeCursor()
			self.commit_count = 0

		def cursor(self):
			return self.cursor_instance

		def commit(self):
			self.commit_count += 1

		def rollback(self):
			pass

	fake_conn = FakeConn()

	def fake_local_storage_init(self, host=None, user=None, password=None, db=None, port=None):
		self.conn = fake_conn

	monkeypatch.setattr('app.services.LocalStorage.LocalStorage.__init__', fake_local_storage_init)

	MCLocalStorage()

	assert any('ADD COLUMN IF NOT EXISTS status_jsonb JSONB' in sql for sql, _ in fake_conn.cursor_instance.executed)
	update_calls = [params for sql, params in fake_conn.cursor_instance.executed if 'UPDATE server_status SET status_jsonb = %s WHERE ip = %s' in sql]
	assert len(update_calls) == 1
	assert isinstance(update_calls[0][0], Json)
	assert update_calls[0][1] == 'mc.example.com:25565'
	assert any('DROP COLUMN status' in sql for sql, _ in fake_conn.cursor_instance.executed)
	assert any('RENAME COLUMN status_jsonb TO status' in sql for sql, _ in fake_conn.cursor_instance.executed)


def test_mc_server_status_masks_ip_and_parses_legacy_status(monkeypatch):
	app, client = _client_with_app()

	class DummyCursor:
		def execute(self, sql, params=None):
			self.sql = sql

		def fetchall(self):
			return [{
				'ip': 'mc.example.com:25565',
				'status': "{'type': 'Java', 'status': 'online', 'host': 'mc.example.com:25565', 'favicon': 'data:image/png;base64,AAA', 'motd': {'text': 'Hi'}, 'pureMotd': 'Hi', 'timings': {'dns_ms': 1}, 'errors': [{'host': 'mc.example.com', 'port': 25565, 'error': 'x'}]}",
				'last_update': None,
				'name': 'Example',
				'expose_ip': False,
			}]

		def __enter__(self):
			return self

		def __exit__(self, exc_type, exc, tb):
			return False

	class DummyStorage:
		def __init__(self):
			self.conn = self

		def cursor(self):
			return DummyCursor()

		def close(self):
			pass

	monkeypatch.setattr(api_routes, 'MCLocalStorage', DummyStorage)

	with app.app_context():
		_login_session(client, permission=2)
		response = client.get('/api/mc-servers/statuses')

		assert response.status_code == 200
		row = response.get_json()['data'][0]
		assert row['ip'] is None
		assert row['expose_ip'] is False
		assert row['status']['type'] == 'Java'
		assert row['status']['pureMotd'] == 'Hi'
		assert 'host' not in row['status']
		assert row['status']['favicon'] == 'data:image/png;base64,AAA'
		assert row['status']['errors'][0]['error'] == 'x'
		assert 'host' not in row['status']['errors'][0]
		assert 'port' not in row['status']['errors'][0]


def test_mc_server_status_can_exclude_icon(monkeypatch):
	app, client = _client_with_app()

	class DummyCursor:
		def execute(self, sql, params=None):
			self.sql = sql

		def fetchall(self):
			return [{
				'ip': 'mc.example.com:25565',
				'status': {
					'type': 'Java',
					'status': 'online',
					'favicon': 'data:image/png;base64,AAA',
					'motd': {'text': 'Hi'},
				},
				'last_update': None,
				'name': 'Example',
				'expose_ip': True,
			}]

		def __enter__(self):
			return self

		def __exit__(self, exc_type, exc, tb):
			return False

	class DummyStorage:
		def __init__(self):
			self.conn = self

		def cursor(self):
			return DummyCursor()

		def close(self):
			pass

	monkeypatch.setattr(api_routes, 'MCLocalStorage', DummyStorage)

	with app.app_context():
		response = client.get('/api/mc-servers/statuses?include_icon=false')

		assert response.status_code == 200
		row = response.get_json()['data'][0]
		assert row['ip'] == 'mc.example.com:25565'
		assert row['status']['type'] == 'Java'
		assert 'favicon' not in row['status']


def test_mc_server_status_rejects_invalid_include_icon(monkeypatch):
	app, client = _client_with_app()

	class DummyStorage:
		def __init__(self):
			raise AssertionError('storage should not be created for invalid query')

	monkeypatch.setattr(api_routes, 'MCLocalStorage', DummyStorage)

	with app.app_context():
		response = client.get('/api/mc-servers/statuses?include_icon=not-a-bool')

		assert response.status_code == 400
		assert 'include_icon' in response.get_json()['error']


def test_mc_server_status_is_public(monkeypatch):
	app, client = _client_with_app()

	class DummyCursor:
		def execute(self, sql, params=None):
			self.sql = sql

		def fetchall(self):
			return [{
				'ip': 'mc.example.com:25565',
				'status': {'type': 'Java', 'status': 'online', 'host': 'mc.example.com:25565'},
				'last_update': None,
				'name': 'Example',
				'expose_ip': False,
			}]

		def __enter__(self):
			return self

		def __exit__(self, exc_type, exc, tb):
			return False

	class DummyStorage:
		def __init__(self):
			self.conn = self

		def cursor(self):
			return DummyCursor()

		def close(self):
			pass

	monkeypatch.setattr(api_routes, 'MCLocalStorage', DummyStorage)

	with app.app_context():
		response = client.get('/api/mc-servers/statuses')

		assert response.status_code == 200
		row = response.get_json()['data'][0]
		assert row['name'] == 'Example'
		assert row['ip'] is None
		assert 'host' not in row['status']


def test_mc_server_status_queries_in_servers_id_order(monkeypatch):
	app, client = _client_with_app()
	observed = {'sql': None}

	class DummyCursor:
		def execute(self, sql, params=None):
			observed['sql'] = sql

		def fetchall(self):
			return [
				{
					'ip': 'z.example.com:25565',
					'status': {'type': 'Java', 'status': 'online'},
					'last_update': None,
					'name': 'Second',
					'expose_ip': True,
				},
				{
					'ip': 'a.example.com:25565',
					'status': {'type': 'Java', 'status': 'online'},
					'last_update': None,
					'name': 'First',
					'expose_ip': True,
				},
			]

		def __enter__(self):
			return self

		def __exit__(self, exc_type, exc, tb):
			return False

	class DummyStorage:
		def __init__(self):
			self.conn = self

		def cursor(self):
			return DummyCursor()

		def close(self):
			pass

	monkeypatch.setattr(api_routes, 'MCLocalStorage', DummyStorage)

	with app.app_context():
		response = client.get('/api/mc-servers/statuses')

		assert response.status_code == 200
		assert 'FROM servers s' in observed['sql']
		assert 'LEFT JOIN server_status ss ON ss.ip = s.ip' in observed['sql']
		assert 'ORDER BY s.id ASC' in observed['sql']
def test_mc_server_status_serializes_last_update_in_app_timezone(monkeypatch):
	app, client = _client_with_app()

	class DummyCursor:
		def execute(self, sql, params=None):
			self.sql = sql

		def fetchall(self):
			return [{
				'ip': 'mc.example.com:25565',
				'status': {'type': 'Java', 'status': 'online'},
				'last_update': datetime(2026, 3, 15, 10, 19, 20),
				'name': 'Example',
				'expose_ip': True,
			}]

		def __enter__(self):
			return self

		def __exit__(self, exc_type, exc, tb):
			return False

	class DummyStorage:
		def __init__(self):
			self.conn = self

		def cursor(self):
			return DummyCursor()

		def close(self):
			pass

	monkeypatch.setattr(api_routes, 'MCLocalStorage', DummyStorage)

	with app.app_context():
		_login_session(client, permission=2)
		response = client.get('/api/mc-servers/statuses')

		assert response.status_code == 200
		row = response.get_json()['data'][0]
		assert row['last_update'] == '2026-03-15T18:19:20+08:00'


def test_rss_feeds_query_is_public(monkeypatch):
	app, client = _client_with_app()

	class DummyFeedService:
		def list_feeds(self, *, limit, offset):
			assert limit == 5
			assert offset == 0
			return {
				'items': [{'id': 1, 'source_url': 'https://example.com/rss.xml', 'title': 'Example Feed'}],
				'total': 1,
				'limit': limit,
				'offset': offset,
			}

		def close(self):
			pass

	monkeypatch.setattr(api_routes, 'RssFeedService', DummyFeedService)

	with app.app_context():
		response = client.get('/api/rss-feeds?limit=5')

		assert response.status_code == 200
		payload = response.get_json()['data']
		assert payload['total'] == 1
		assert payload['items'][0]['title'] == 'Example Feed'


def test_rss_entries_query_is_public(monkeypatch):
	app, client = _client_with_app()

	class DummyFeedService:
		def list_entries(self, *, feed_id, limit, offset):
			assert feed_id == 3
			assert limit == 10
			assert offset == 0
			return {
				'items': [{'id': 9, 'feed_id': 3, 'title': 'Entry Title', 'content': 'Body'}],
				'total': 1,
				'limit': limit,
				'offset': offset,
			}

		def close(self):
			pass

	monkeypatch.setattr(api_routes, 'RssFeedService', DummyFeedService)

	with app.app_context():
		response = client.get('/api/rss-entries?feed_id=3&limit=10')

		assert response.status_code == 200
		payload = response.get_json()['data']
		assert payload['items'][0]['feed_id'] == 3
		assert payload['items'][0]['title'] == 'Entry Title'


def test_file_catalog_query_is_public(monkeypatch):
	app, client = _client_with_app()

	class DummyFileCatalogService:
		def list_files(self, *, access_levels, limit, offset, include_inactive):
			assert access_levels == ['public']
			assert limit == 5
			assert offset == 0
			assert include_inactive is False
			return {
				'items': [{'id': 1, 'display_name': 'Manual.zip', 'visibility': 'public'}],
				'total': 1,
				'limit': limit,
				'offset': offset,
			}

		def close(self):
			pass

	monkeypatch.setattr(api_routes, 'FileCatalogService', DummyFileCatalogService)

	with app.app_context():
		response = client.get('/api/files?limit=5')

		assert response.status_code == 200
		payload = response.get_json()['data']
		assert payload['items'][0]['display_name'] == 'Manual.zip'


def test_file_create_requires_admin_permission():
	app, client = _client_with_app()
	with app.app_context():
		_login_session(client, permission=0)
		csrf_token = _issue_csrf(client)
		response = client.post(
			'/api/files',
			json={'storage_key': 'objects/manual.zip', 'display_name': 'Manual.zip'},
			headers={'X-CSRFToken': csrf_token},
		)

		assert response.status_code == 403


def test_file_download_audit_query_requires_admin_permission():
	app, client = _client_with_app()
	with app.app_context():
		_login_session(client, permission=0)
		response = client.get('/api/file-downloads/audits')

		assert response.status_code == 403


def test_file_create_validates_before_storage(monkeypatch):
	app, client = _client_with_app()
	storage_called = {'value': False}

	class DummyFileCatalogService:
		def __init__(self):
			storage_called['value'] = True

	monkeypatch.setattr(api_routes, 'FileCatalogService', DummyFileCatalogService)

	with app.app_context():
		_login_session(client, permission=2)
		csrf_token = _issue_csrf(client)
		response = client.post(
			'/api/files',
			json={},
			headers={'X-CSRFToken': csrf_token},
		)

		assert response.status_code == 400
		assert storage_called['value'] is False


def test_file_download_token_requires_login_for_authenticated_file(monkeypatch):
	app, client = _client_with_app()

	class DummyFileCatalogService:
		def issue_download_token(self, file_id, *, logged_in, permission):
			assert file_id == 9
			assert logged_in is False
			assert permission == 0
			raise FileCatalogAuthorizationError('Not logged in', status_code=401)

		def close(self):
			pass

	monkeypatch.setattr(api_routes, 'FileCatalogService', DummyFileCatalogService)

	with app.app_context():
		response = client.get('/api/files/9/download-token')

		assert response.status_code == 401


def test_file_download_token_returns_token_for_public_file(monkeypatch):
	app, client = _client_with_app()
	audit_calls = []

	class DummyFileCatalogService:
		def issue_download_token(self, file_id, *, logged_in, permission):
			assert file_id == 3
			assert logged_in is False
			assert permission == 0
			return {
				'token': 'signed-token',
				'token_type': 'file_download',
				'expires_in': 300,
				'download_path': '/downloads/objects/manual.zip',
				'download_url': '/downloads/objects/manual.zip?token=signed-token',
				'file': {'id': 3, 'visibility': 'public'},
			}

		def record_download_audit(self, **kwargs):
			audit_calls.append(kwargs)

		def close(self):
			pass

	monkeypatch.setattr(api_routes, 'FileCatalogService', DummyFileCatalogService)

	with app.app_context():
		response = client.get('/api/files/3/download-token')

		assert response.status_code == 200
		payload = response.get_json()['data']
		assert payload['token'] == 'signed-token'
		assert payload['download_url'].endswith('token=signed-token')
		assert audit_calls[0]['action'] == 'issue_token'
		assert audit_calls[0]['outcome'] == 'success'


def test_file_download_verify_is_public(monkeypatch):
	app, client = _client_with_app()

	class DummyFileCatalogService:
		def verify_download_token(self, token):
			assert token == 'signed-token'
			return {
				'file': {'id': 2, 'storage_key': 'objects/manual.zip'}
			}

		def close(self):
			pass

	monkeypatch.setattr(api_routes, 'FileCatalogService', DummyFileCatalogService)

	with app.app_context():
		response = client.get('/api/file-downloads/verify?token=signed-token')

		assert response.status_code == 200
		payload = response.get_json()['data']
		assert payload['file']['storage_key'] == 'objects/manual.zip'


def test_file_download_authorize_uses_forwarded_uri(monkeypatch):
	app, client = _client_with_app()
	audit_calls = []

	class DummyFileCatalogService:
		def authorize_download_request(self, forwarded_uri, *, logged_in, permission):
			assert forwarded_uri == '/downloads/objects/manual.zip?token=signed-token'
			assert logged_in is True
			assert permission == 2
			return {
				'authorized': True,
				'file': {'id': 2, 'storage_key': 'objects/manual.zip'},
			}

		def record_download_audit(self, **kwargs):
			audit_calls.append(kwargs)

		def close(self):
			pass

	monkeypatch.setattr(api_routes, 'FileCatalogService', DummyFileCatalogService)

	with app.app_context():
		_login_session(client, permission=2)
		response = client.get(
			'/api/file-downloads/authorize',
			headers={'X-Forwarded-Uri': '/downloads/objects/manual.zip?token=signed-token'},
		)

		assert response.status_code == 200
		assert response.get_json()['data']['authorized'] is True
		assert audit_calls[0]['action'] == 'authorize'
		assert audit_calls[0]['outcome'] == 'success'


def test_file_download_audit_query_returns_items(monkeypatch):
	app, client = _client_with_app()

	class DummyFileCatalogService:
		def list_download_audits(self, *, file_id, action, outcome, limit, offset):
			assert file_id == 3
			assert action == 'authorize'
			assert outcome == 'success'
			assert limit == 5
			assert offset == 0
			return {
				'items': [{'id': 1, 'action': 'authorize', 'outcome': 'success'}],
				'total': 1,
				'limit': limit,
				'offset': offset,
			}

		def close(self):
			pass

	monkeypatch.setattr(api_routes, 'FileCatalogService', DummyFileCatalogService)

	with app.app_context():
		_login_session(client, permission=2)
		response = client.get('/api/file-downloads/audits?file_id=3&action=authorize&outcome=success&limit=5')

		assert response.status_code == 200
		payload = response.get_json()['data']
		assert payload['total'] == 1
		assert payload['items'][0]['action'] == 'authorize'


def test_file_download_authorize_rejects_path_mismatch():
	class DummyStorage:
		def __init__(self):
			self.row = {
				'id': 7,
				'storage_key': 'objects/manual.zip',
				'display_name': 'Manual.zip',
				'download_name': 'Manual.zip',
				'description': None,
				'mime_type': 'application/zip',
				'size_bytes': 1024,
				'visibility': 'public',
				'is_active': True,
				'metadata': {},
				'created_at': None,
				'updated_at': None,
			}

		def get_file_row(self, file_id):
			assert file_id == 7
			return dict(self.row)

		def close(self):
			pass

	service = FileCatalogService(storage=DummyStorage())
	token_payload = service.issue_download_token(7, logged_in=False, permission=0)

	try:
		service.authorize_download_request('/downloads/objects/other.zip?token=' + token_payload['token'], logged_in=False, permission=0)
	except FileCatalogAuthorizationError as exc:
		assert exc.status_code == 403
		assert '不匹配' in str(exc)
	else:
		raise AssertionError('path mismatch should be rejected')


def test_mca_download_authorize_uses_forwarded_uri(monkeypatch):
	app, client = _client_with_app()

	class DummyMcaDownloadAuthorizationService:
		def authorize_download_request(self, forwarded_uri, *, logged_in, permission):
			assert forwarded_uri == '/resource/mca/world-main/r.0.-1.mca'
			assert logged_in is True
			assert permission == 2
			return {
				'authorized': True,
				'path': forwarded_uri,
				'relative_path': 'r.0.-1.mca',
				'base_path': '/resource/mca/world-main',
				'visibility': 'authenticated',
			}

	monkeypatch.setattr(api_routes, 'McaDownloadAuthorizationService', DummyMcaDownloadAuthorizationService)

	with app.app_context():
		_login_session(client, permission=2)
		app.config['RUNTIME_CONFIG_MCA_BASE_URL'] = '/resource/mca/world-main'
		app.config['MCA_ACCESS_LEVEL'] = 'authenticated'
		response = client.get(
			'/api/mca-downloads/authorize',
			headers={'X-Forwarded-Uri': '/resource/mca/world-main/r.0.-1.mca'},
		)

		assert response.status_code == 200
		payload = response.get_json()['data']
		assert payload['authorized'] is True
		assert payload['relative_path'] == 'r.0.-1.mca'


def test_mca_download_service_rejects_path_outside_runtime_prefix(monkeypatch):
	app, _ = _client_with_app()
	with app.app_context():
		app.config['RUNTIME_CONFIG_MCA_BASE_URL'] = '/resource/mca/world-main'
		app.config['MCA_ACCESS_LEVEL'] = 'public'
		monkeypatch.setattr('app.services.McaDownload.Config.RUNTIME_CONFIG_MCA_BASE_URL', '/resource/mca/world-main')
		monkeypatch.setattr('app.services.McaDownload.Config.MCA_ACCESS_LEVEL', 'public')
		service = McaDownloadAuthorizationService()

		try:
			service.authorize_download_request('/resource/mca/world-other/r.0.0.mca', logged_in=False, permission=0)
		except McaDownloadAuthorizationError as exc:
			assert exc.status_code == 404
			assert '允许前缀' in str(exc)
		else:
			raise AssertionError('path outside configured MCA prefix should be rejected')


def test_mca_download_service_requires_login_for_authenticated_access(monkeypatch):
	monkeypatch.setattr('app.services.McaDownload.Config.RUNTIME_CONFIG_MCA_BASE_URL', '/resource/mca/world-main')
	monkeypatch.setattr('app.services.McaDownload.Config.MCA_ACCESS_LEVEL', 'authenticated')
	service = McaDownloadAuthorizationService()

	try:
		service.authorize_download_request('/resource/mca/world-main/r.1.2.mca', logged_in=False, permission=0)
	except McaDownloadAuthorizationError as exc:
		assert exc.status_code == 401
		assert str(exc) == 'Not logged in'
	else:
		raise AssertionError('authenticated MCA access should require login')


def test_mca_download_service_rejects_non_region_file(monkeypatch):
	monkeypatch.setattr('app.services.McaDownload.Config.RUNTIME_CONFIG_MCA_BASE_URL', '/resource/mca/world-main')
	monkeypatch.setattr('app.services.McaDownload.Config.MCA_ACCESS_LEVEL', 'public')
	service = McaDownloadAuthorizationService()

	try:
		service.authorize_download_request('/resource/mca/world-main/level.dat', logged_in=False, permission=0)
	except McaDownloadAuthorizationError as exc:
		assert exc.status_code == 404
		assert '区域 mca 文件' in str(exc)
	else:
		raise AssertionError('non-region files should be rejected')


def test_file_storage_key_validation_rejects_traversal(monkeypatch):
	app, client = _client_with_app()

	class DummyFileCatalogService:
		def __init__(self):
			raise AssertionError('service should not initialize for invalid storage_key')

	monkeypatch.setattr(api_routes, 'FileCatalogService', DummyFileCatalogService)

	with app.app_context():
		_login_session(client, permission=2)
		csrf_token = _issue_csrf(client)
		response = client.post(
			'/api/files',
			json={'storage_key': '../secrets.zip', 'display_name': 'Bad'},
			headers={'X-CSRFToken': csrf_token},
		)

		assert response.status_code == 400


def test_file_download_token_becomes_invalid_after_storage_key_change():
	class DummyStorage:
		def __init__(self):
			self.row = {
				'id': 5,
				'storage_key': 'objects/manual-v1.zip',
				'display_name': 'Manual.zip',
				'download_name': 'Manual.zip',
				'description': None,
				'mime_type': 'application/zip',
				'size_bytes': 1024,
				'visibility': 'authenticated',
				'is_active': True,
				'metadata': {},
				'created_at': None,
				'updated_at': None,
			}

		def get_file_row(self, file_id):
			assert file_id == 5
			return dict(self.row)

		def close(self):
			pass

	service = FileCatalogService(storage=DummyStorage())
	token_payload = service.issue_download_token(5, logged_in=True, permission=0)
	service.storage.row['storage_key'] = 'objects/manual-v2.zip'

	try:
		service.verify_download_token(token_payload['token'])
	except FileCatalogAuthorizationError as exc:
		assert exc.status_code == 401
		assert '失效' in str(exc)
	else:
		raise AssertionError('storage_key change should invalidate existing token')


def test_file_download_token_rejects_inactive_file_at_verify():
	class DummyStorage:
		def __init__(self):
			self.row = {
				'id': 6,
				'storage_key': 'objects/manual.zip',
				'display_name': 'Manual.zip',
				'download_name': 'Manual.zip',
				'description': None,
				'mime_type': 'application/zip',
				'size_bytes': 1024,
				'visibility': 'public',
				'is_active': True,
				'metadata': {},
				'created_at': None,
				'updated_at': None,
			}

		def get_file_row(self, file_id):
			assert file_id == 6
			return dict(self.row)

		def close(self):
			pass

	service = FileCatalogService(storage=DummyStorage())
	token_payload = service.issue_download_token(6, logged_in=False, permission=0)
	service.storage.row['is_active'] = False

	try:
		service.verify_download_token(token_payload['token'])
	except FileCatalogAuthorizationError as exc:
		assert exc.status_code == 404
	else:
		raise AssertionError('inactive file should be treated as unavailable')


def test_rss_sync_requires_admin_permission():
	app, client = _client_with_app()
	with app.app_context():
		_login_session(client, permission=0)
		csrf_token = _issue_csrf(client)
		response = client.post(
			'/api/rss-feeds/sync',
			json={},
			headers={'X-CSRFToken': csrf_token},
		)

		assert response.status_code == 403


def test_rss_sync_fails_when_configured_source_missing(monkeypatch):
	app, client = _client_with_app()

	class DummyFeedService:
		def sync_configured_feed(self):
			raise api_routes.FeedServiceError('RSS_SOURCE_URL 未配置')

		def close(self):
			pass

	monkeypatch.setattr(api_routes, 'RssFeedService', DummyFeedService)

	with app.app_context():
		_login_session(client, permission=2)
		csrf_token = _issue_csrf(client)
		response = client.post(
			'/api/rss-feeds/sync',
			json={},
			headers={'X-CSRFToken': csrf_token},
		)

		assert response.status_code == 500


def test_rss_sync_returns_saved_result(monkeypatch):
	app, client = _client_with_app()

	class DummyFeedService:
		def sync_configured_feed(self):
			return {
				'feed': {'id': 1, 'source_url': 'https://docs.ustb.world/api/rss?lang=zh', 'title': 'Example Feed'},
				'entry_count': 2,
				'inserted': 2,
				'updated': 0,
			}

		def close(self):
			pass

	monkeypatch.setattr(api_routes, 'RssFeedService', DummyFeedService)

	with app.app_context():
		_login_session(client, permission=2)
		csrf_token = _issue_csrf(client)
		response = client.post(
			'/api/rss-feeds/sync',
			json={},
			headers={'X-CSRFToken': csrf_token},
		)

		assert response.status_code == 200
		payload = response.get_json()['data']
		assert payload['entry_count'] == 2
		assert payload['feed']['title'] == 'Example Feed'
def test_rss_sync_returns_conflict_when_sync_already_running(monkeypatch):
	app, client = _client_with_app()

	class DummyFeedService:
		def sync_configured_feed(self):
			raise FeedSyncConflictError('RSS sync already in progress')

		def close(self):
			pass

	monkeypatch.setattr(api_routes, 'RssFeedService', DummyFeedService)

	with app.app_context():
		_login_session(client, permission=2)
		csrf_token = _issue_csrf(client)
		response = client.post(
			'/api/rss-feeds/sync',
			json={},
			headers={'X-CSRFToken': csrf_token},
		)

		assert response.status_code == 409


def test_mc_status_refresh_skips_when_advisory_lock_is_held(monkeypatch):
	class DummyStorage:
		def __init__(self):
			self.closed = False

		def try_advisory_lock(self, key):
			assert key == 91002
			return False

		def close(self):
			self.closed = True

	storage = DummyStorage()
	monkeypatch.setattr('app.services.ServerDataService.MCLocalStorage', lambda: storage)

	from app.services.ServerDataService import ServerStatusManager

	job_events = []

	class DummyJobStatusService:
		def mark_running(self, job_name, *, interval_seconds=None):
			job_events.append(('running', job_name, interval_seconds))

		def mark_success(self, job_name, *, interval_seconds=None, result=None):
			job_events.append(('success', job_name, result))

		def close(self):
			job_events.append(('close', None, None))

	monkeypatch.setattr('app.services.ServerDataService.JobStatusService', DummyJobStatusService)

	result = ServerStatusManager(interval=60).update_all_status()

	assert result is None
	assert storage.closed is True
	assert job_events[0] == ('running', 'mc_status_refresh', 60)
	assert job_events[1][0] == 'success'
	assert job_events[1][1] == 'mc_status_refresh'


def test_rss_sync_all_feeds_aggregates_success_and_failure():
	service = object.__new__(RssFeedService)

	def fake_get_configured_source_url():
		return 'https://docs.ustb.world/api/rss?lang=zh'

	def fake_sync_feed(url):
		assert url == 'https://docs.ustb.world/api/rss?lang=zh'
		return {'entry_count': 3, 'inserted': 2, 'updated': 1}

	service.get_configured_source_url = fake_get_configured_source_url
	service.sync_feed = fake_sync_feed

	result = RssFeedService.sync_all_feeds(service)

	assert result['total_feeds'] == 1
	assert result['synced_feeds'] == 1
	assert result['failed_feeds'] == 0
	assert result['entry_count'] == 3
	assert result['inserted'] == 2
	assert result['updated'] == 1
	assert result['failures'] == []


def test_rss_feed_service_requires_configured_source_url(monkeypatch):
	monkeypatch.setattr('app.services.Feed.Config.RSS_SOURCE_URL', '')

	try:
		RssFeedService.get_configured_source_url()
	except Exception as exc:
		assert 'RSS_SOURCE_URL' in str(exc)
	else:
		raise AssertionError('missing RSS_SOURCE_URL should fail')


def test_rss_refresh_manager_runs_service_and_closes(monkeypatch):
	called = {'sync': 0, 'close': 0}
	job_events = []

	class DummyFeedService:
		def sync_all_feeds(self):
			called['sync'] += 1
			return {'total_feeds': 1, 'synced_feeds': 1, 'failed_feeds': 0, 'inserted': 1, 'updated': 0}

		def close(self):
			called['close'] += 1

	class DummyJobStatusService:
		def mark_running(self, job_name, *, interval_seconds=None):
			job_events.append(('running', job_name, interval_seconds))

		def mark_success(self, job_name, *, interval_seconds=None, result=None):
			job_events.append(('success', job_name, result))

		def close(self):
			job_events.append(('close', None, None))

	monkeypatch.setattr('app.services.Feed.RssFeedService', DummyFeedService)
	monkeypatch.setattr('app.services.Feed.JobStatusService', DummyJobStatusService)

	manager = RssFeedRefreshManager(interval=60)
	result = manager.refresh_all_feeds()

	assert result['synced_feeds'] == 1
	assert called['sync'] == 1
	assert called['close'] == 1
	assert job_events[0] == ('running', 'rss_feed_refresh', 60)
	assert job_events[1][0] == 'success'


def test_job_status_service_serializes_next_run_due_at(monkeypatch):
	class DummyCursor:
		def execute(self, sql, params=None):
			self.sql = sql

		def fetchall(self):
			return [{
				'job_name': 'rss_feed_refresh',
				'interval_seconds': 60,
				'is_running': False,
				'last_started_at': datetime(2026, 3, 16, 7, 0, 0),
				'last_finished_at': datetime(2026, 3, 16, 7, 0, 10),
				'last_success_at': datetime(2026, 3, 16, 7, 0, 10),
				'last_error_at': None,
				'last_error_message': None,
				'last_result': {'synced_feeds': 1},
				'updated_at': datetime(2026, 3, 16, 7, 0, 10),
			}]

		def __enter__(self):
			return self

		def __exit__(self, exc_type, exc, tb):
			return False

	class DummyStorage:
		def __init__(self):
			self.conn = self

		def cursor(self):
			return DummyCursor()

		def close(self):
			pass

	service = JobStatusService(storage=DummyStorage())
	result = service.list_statuses()

	assert result['total'] == 1
	assert result['items'][0]['job_name'] == 'rss_feed_refresh'
	assert result['items'][0]['next_run_due_at'] == '2026-03-16T15:01:10+08:00'


def test_normalize_status_payload_supports_json_and_python_repr():
	assert normalize_status_payload('{"status": "online"}') == {'status': 'online'}
	assert normalize_status_payload("{'status': 'online'}") == {'status': 'online'}


def test_query_server_status_keeps_java_chat_component_and_plain_text(monkeypatch):
	java_payload = {
		'description': {
			'text': 'Hello ',
			'extra': [
				{'text': 'World', 'color': 'green'},
			],
		},
		'version': {'name': '1.20.4', 'protocol': 765},
		'players': {'online': 1, 'max': 20, 'sample': []},
		'favicon': None,
		'_timings': {'connect_ms': 1},
	}

	def fake_ping_java(host, port, timeout):
		return dict(java_payload)

	def fake_ping_bedrock(host, port, timeout):
		raise RuntimeError('ignore bedrock')

	monkeypatch.setattr('app.utils.serverStatus.ping_java', fake_ping_java)
	monkeypatch.setattr('app.utils.serverStatus.ping_bedrock', fake_ping_bedrock)

	result = query_server_status('mc.example.com', port=25565, server_type='java')

	assert isinstance(result['motd'], dict)
	assert result['motd']['extra'][0]['text'] == 'World'
	assert result['pureMotd'] == 'Hello World'


def test_ustb_userinfo_maps_user_group_to_permission():
	app, _ = _client_with_app()
	with app.app_context():
		app.config['OAUTH_PROVIDERS']['ustb']['base_url'] = 'https://skin.ustb.world'
		user_info = {
			'sub': '123',
			'username': 'alice',
			'avatar_url': '',
			'email': 'alice@example.com',
			'user_group': 'super_admin',
		}

		result = sanitize_user_info(user_info, 'ustb')

		assert result['id'] == '123'
		assert result['username'] == 'alice'
		assert result['permission'] == 2
		assert result['avatar_url'] == '/skin-origin-proxy/oauth/avatar'


def test_ustb_userinfo_requires_documented_claims():
	app, _ = _client_with_app()
	with app.app_context():
		user_info = {
			'sub': '123',
			'username': '   ',
		}

		try:
			sanitize_user_info(user_info, 'ustb')
		except ValueError as exc:
			assert 'username' in str(exc)
		else:
			raise AssertionError('sanitize_user_info should reject blank required username')


def test_load_oauth_providers_adds_skin_scope_to_ustb(monkeypatch):
	monkeypatch.setenv('USTB_SCOPE', 'userinfo permission')

	providers = load_oauth_providers_from_env()

	assert providers['ustb']['scope'].split() == ['userinfo', 'permission', 'skin']
	assert providers['ustb']['skin_url'] == 'https://skin.ustb.world/skinapi/oauth/skin'