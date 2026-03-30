import os
import secrets
from app.utils.auth import load_oauth_providers_from_env


def _parse_csv_env(name, default=''):
    value = os.environ.get(name, default)
    return [item.strip() for item in value.split(',') if item.strip()]


def _parse_int_env(name, default):
    value = os.environ.get(name)
    if value is None or value == '':
        return default

    try:
        return int(value)
    except ValueError:
        return default


def _parse_bool_env(name, default=False):
    value = os.environ.get(name)
    if value is None or value == '':
        return default

    return value.strip().lower() in ('1', 'true', 'yes', 'on')


def _first_non_empty_env(*names, default=''):
    for name in names:
        value = os.environ.get(name)
        if value is not None and value.strip() != '':
            return value.strip()

    return default


def _should_require_strict_env():
    explicit = os.environ.get('STRICT_ENV')
    if explicit is not None and explicit.strip() != '':
        return explicit.strip().lower() in ('1', 'true', 'yes', 'on')

    app_env = os.environ.get('APP_ENV', '').strip().lower()
    if app_env in ('prod', 'production'):
        return True

    return False


def _resolve_required_env(name, fallback=''):
    value = os.environ.get(name)
    if value is not None and value.strip() != '':
        return value.strip()

    if STRICT_ENV_REQUIRED:
        raise RuntimeError(f'Missing required environment variable: {name}')

    return fallback


STRICT_ENV_REQUIRED = _should_require_strict_env()
RESOLVED_SECRET_KEY = _resolve_required_env('SECRET_KEY', secrets.token_hex(32))
RESOLVED_FILE_DOWNLOAD_TOKEN_SECRET = _resolve_required_env(
    'FILE_DOWNLOAD_TOKEN_SECRET',
    RESOLVED_SECRET_KEY,
)
RESOLVED_PGSQL_PASSWORD = _resolve_required_env('PGSQL_PASSWORD')


class Config:
    SECRET_KEY = RESOLVED_SECRET_KEY
    APP_TIMEZONE = os.environ.get('APP_TIMEZONE', 'Asia/Shanghai').strip() or 'Asia/Shanghai'
    FILE_DOWNLOAD_TOKEN_SECRET = RESOLVED_FILE_DOWNLOAD_TOKEN_SECRET
    FILE_DOWNLOAD_TOKEN_SALT = os.environ.get('FILE_DOWNLOAD_TOKEN_SALT', 'file-download-token')
    FILE_DOWNLOAD_TOKEN_TTL = _parse_int_env('FILE_DOWNLOAD_TOKEN_TTL', 300)
    FILE_DOWNLOAD_BASE_PATH = os.environ.get('FILE_DOWNLOAD_BASE_PATH', '/downloads').strip() or '/downloads'
    FILE_STORAGE_ROOT = os.environ.get('FILE_STORAGE_ROOT', '/data/file-data').strip() or '/data/file-data'
    MCA_ACCESS_LEVEL = os.environ.get('MCA_ACCESS_LEVEL', 'public').strip().lower() or 'public'
    SAME_ORIGIN_ASSET_PROXY_PATH = os.environ.get('SAME_ORIGIN_ASSET_PROXY_PATH', '/skin-origin-proxy').strip() or '/skin-origin-proxy'
    ASSET_PROXY_ALLOWED_HOSTS = _parse_csv_env(
        'ASSET_PROXY_ALLOWED_HOSTS',
        'skin.ustb.world,avatars.githubusercontent.com,skin.mualliance.ltd'
    )
    ASSET_PROXY_TIMEOUT = _parse_int_env('ASSET_PROXY_TIMEOUT', 10)
    RUNTIME_CONFIG_API_BASE_URL = _first_non_empty_env('API_BASE_URL', 'VITE_API_BASE_URL')
    RUNTIME_CONFIG_AUTH_BASE_URL = _first_non_empty_env('AUTH_BASE_URL', 'VITE_AUTH_BASE_URL')
    RUNTIME_CONFIG_APP_BASE_URL = _first_non_empty_env('APP_BASE_URL', 'VITE_APP_BASE_URL')
    RUNTIME_CONFIG_MCA_BASE_URL = _first_non_empty_env('MCA_BASE_URL', 'VITE_MCA_BASE_URL')
    RUNTIME_CONFIG_SKIN_API_BASE_URL = _first_non_empty_env('SKIN_API_BASE_URL', 'VITE_SKIN_API_BASE_URL')
    RUNTIME_CONFIG_SKIN_BASE_URL = _first_non_empty_env('SKIN_BASE_URL', 'VITE_SKIN_BASE_URL', default='/assets/skin')
    RUNTIME_CONFIG_DEV_BACKEND_PROXY_ENABLED = _parse_bool_env(
        'DEV_BACKEND_PROXY_ENABLED',
        _parse_bool_env('VITE_DEV_BACKEND_PROXY_ENABLED', False),
    )

    # 安全配置
    SESSION_COOKIE_SECURE = os.environ.get('SECURE_COOKIES', 'True').lower() == 'true'
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = 'Lax'
    PERMANENT_SESSION_LIFETIME = int(os.environ.get('SESSION_LIFETIME', 3600))
    SESSION_TYPE = 'redis'
    SESSION_REDIS = os.environ.get('REDIS_URL', 'redis://localhost:6379/0')
    SESSION_USE_SIGNER = True
    SESSION_KEY_PREFIX = 'flask_sess:'
    DEBUG = os.environ.get('FLASK_DEBUG', 'False').lower() == 'true'
    HOST = os.environ.get('FLASK_HOST', '127.0.0.1')
    PORT = int(os.environ.get('FLASK_PORT', 5000))
    RSS_SOURCE_URL = os.environ.get('RSS_SOURCE_URL', '').strip()
    RSS_REFRESH_ENABLED = os.environ.get('RSS_REFRESH_ENABLED', 'True').lower() == 'true'
    RSS_REFRESH_INTERVAL = _parse_int_env('RSS_REFRESH_INTERVAL', 1800)
    MAX_CONTENT_LENGTH = _parse_int_env('MAX_CONTENT_LENGTH', 1024 * 1024)
    WTF_CSRF_SSL_STRICT = os.environ.get('WTF_CSRF_SSL_STRICT', 'False').lower() == 'true'
    SESSION_DEBUG = os.environ.get('SESSION_DEBUG', 'False').lower() == 'true'
    TRUSTED_HOSTS = _parse_csv_env(
        'TRUSTED_HOSTS',
        'localhost,127.0.0.1'
    )
    PROXY_FIX_X_FOR = _parse_int_env('PROXY_FIX_X_FOR', 1)
    PROXY_FIX_X_PROTO = _parse_int_env('PROXY_FIX_X_PROTO', 1)
    PROXY_FIX_X_HOST = _parse_int_env('PROXY_FIX_X_HOST', 1)
    CORS_ALLOWED_ORIGINS = _parse_csv_env(
        'CORS_ALLOWED_ORIGINS',
        'http://localhost,http://127.0.0.1,http://localhost:5175,http://127.0.0.1:5175'
    )
    OAUTH_ALLOWED_REDIRECT_HOSTS = _parse_csv_env(
        'OAUTH_ALLOWED_REDIRECT_HOSTS',
        'localhost,127.0.0.1'
    )
    OAUTH_ALLOW_HTTP_LOCALHOST = os.environ.get('OAUTH_ALLOW_HTTP_LOCALHOST', 'True').lower() == 'true'
    APP_ALLOWED_RETURN_HOSTS = _parse_csv_env(
        'APP_ALLOWED_RETURN_HOSTS',
        'localhost,127.0.0.1'
    )
    APP_ALLOW_HTTP_LOCALHOST = os.environ.get('APP_ALLOW_HTTP_LOCALHOST', 'True').lower() == 'true'
    DEFAULT_LOGIN_SUCCESS_URL = os.environ.get('DEFAULT_LOGIN_SUCCESS_URL', '/home')
    DEV_AUTH_ENABLED = os.environ.get('DEV_AUTH_ENABLED', 'False').lower() == 'true'
    DEV_AUTH_ALLOWED_SOURCE_HOSTS = _parse_csv_env(
        'DEV_AUTH_ALLOWED_SOURCE_HOSTS',
        'localhost,127.0.0.1'
    )
    DEV_AUTH_ALLOWED_SOURCE_CIDRS = _parse_csv_env(
        'DEV_AUTH_ALLOWED_SOURCE_CIDRS',
        '127.0.0.1/32,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16'
    )
    DEV_AUTH_DEFAULT_PROVIDER = os.environ.get('DEV_AUTH_DEFAULT_PROVIDER', 'dev')
    DEV_AUTH_REQUIRE_DEBUG = os.environ.get('DEV_AUTH_REQUIRE_DEBUG', 'True').lower() == 'true'
    DEV_AUTH_SHARED_SECRET = os.environ.get('DEV_AUTH_SHARED_SECRET', '').strip()
    OAUTH_PROVIDERS = load_oauth_providers_from_env()

    # PostgreSQL 数据库配置
    PGSQL_HOST = os.environ.get('PGSQL_HOST', 'localhost')
    PGSQL_PORT = int(os.environ.get('PGSQL_PORT', 5432))
    PGSQL_DB = os.environ.get('PGSQL_DB', 'ustbhome')
    PGSQL_USER = os.environ.get('PGSQL_USER', 'postgres')
    PGSQL_PASSWORD = RESOLVED_PGSQL_PASSWORD