import os
import secrets
from app.utils.auth import load_oauth_providers_from_env
from app.utils.env import (
    first_non_empty_env,
    get_env_bool,
    get_env_csv,
    get_env_int,
    get_env_str,
    resolve_required_env,
)


def _should_require_strict_env():
    explicit = os.environ.get('STRICT_ENV')
    if explicit is not None and explicit.strip() != '':
        return get_env_bool('STRICT_ENV', False)

    app_env = get_env_str('APP_ENV', '').lower()
    if app_env in ('prod', 'production'):
        return True

    return False


def _resolve_required_env(name, fallback=''):
    return resolve_required_env(name, strict_required=STRICT_ENV_REQUIRED, fallback=fallback)


STRICT_ENV_REQUIRED = _should_require_strict_env()
RESOLVED_SECRET_KEY = _resolve_required_env('SECRET_KEY', secrets.token_hex(32))
RESOLVED_FILE_DOWNLOAD_TOKEN_SECRET = _resolve_required_env(
    'FILE_DOWNLOAD_TOKEN_SECRET',
    RESOLVED_SECRET_KEY,
)
RESOLVED_PGSQL_PASSWORD = _resolve_required_env('PGSQL_PASSWORD')


class Config:
    SECRET_KEY = RESOLVED_SECRET_KEY
    APP_TIMEZONE = get_env_str('APP_TIMEZONE', 'Asia/Shanghai')
    FILE_DOWNLOAD_TOKEN_SECRET = RESOLVED_FILE_DOWNLOAD_TOKEN_SECRET
    FILE_DOWNLOAD_TOKEN_SALT = get_env_str('FILE_DOWNLOAD_TOKEN_SALT', 'file-download-token')
    FILE_DOWNLOAD_TOKEN_TTL = get_env_int('FILE_DOWNLOAD_TOKEN_TTL', 300)
    FILE_DOWNLOAD_BASE_PATH = get_env_str('FILE_DOWNLOAD_BASE_PATH', '/downloads')
    FILE_STORAGE_ROOT = get_env_str('FILE_STORAGE_ROOT', '/data/file-data')
    MCA_ACCESS_LEVEL = get_env_str('MCA_ACCESS_LEVEL', 'public').lower()
    SAME_ORIGIN_ASSET_PROXY_PATH = get_env_str('SAME_ORIGIN_ASSET_PROXY_PATH', '/skin-origin-proxy')
    ASSET_PROXY_ALLOWED_HOSTS = get_env_csv(
        'ASSET_PROXY_ALLOWED_HOSTS',
        'skin.ustb.world,avatars.githubusercontent.com,skin.mualliance.ltd'
    )
    ASSET_PROXY_TIMEOUT = get_env_int('ASSET_PROXY_TIMEOUT', 10)
    ASSET_PROXY_MAX_BYTES = get_env_int('ASSET_PROXY_MAX_BYTES', 8 * 1024 * 1024)
    RUNTIME_CONFIG_API_BASE_URL = first_non_empty_env('API_BASE_URL', 'VITE_API_BASE_URL')
    RUNTIME_CONFIG_AUTH_BASE_URL = first_non_empty_env('AUTH_BASE_URL', 'VITE_AUTH_BASE_URL')
    RUNTIME_CONFIG_APP_BASE_URL = first_non_empty_env('APP_BASE_URL', 'VITE_APP_BASE_URL')
    RUNTIME_CONFIG_MCA_BASE_URL = first_non_empty_env('MCA_BASE_URL', 'VITE_MCA_BASE_URL')
    RUNTIME_CONFIG_SKIN_API_BASE_URL = first_non_empty_env('SKIN_API_BASE_URL', 'VITE_SKIN_API_BASE_URL')
    RUNTIME_CONFIG_SKIN_BASE_URL = first_non_empty_env('SKIN_BASE_URL', 'VITE_SKIN_BASE_URL', default='/assets/skin')
    RUNTIME_CONFIG_DEV_BACKEND_PROXY_ENABLED = get_env_bool(
        'DEV_BACKEND_PROXY_ENABLED',
        get_env_bool('VITE_DEV_BACKEND_PROXY_ENABLED', False),
    )

    # 安全配置
    SESSION_COOKIE_SECURE = get_env_bool('SECURE_COOKIES', True)
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = 'Lax'
    PERMANENT_SESSION_LIFETIME = get_env_int('SESSION_LIFETIME', 3600)
    SESSION_TYPE = 'redis'
    SESSION_REDIS = get_env_str('REDIS_URL', 'redis://localhost:6379/0')
    SESSION_USE_SIGNER = True
    SESSION_KEY_PREFIX = 'flask_sess:'
    DEBUG = get_env_bool('FLASK_DEBUG', False)
    HOST = get_env_str('FLASK_HOST', '127.0.0.1')
    PORT = get_env_int('FLASK_PORT', 5000)
    RSS_SOURCE_URL = get_env_str('RSS_SOURCE_URL', '')
    RSS_REFRESH_ENABLED = get_env_bool('RSS_REFRESH_ENABLED', True)
    RSS_REFRESH_INTERVAL = get_env_int('RSS_REFRESH_INTERVAL', 1800)
    MAX_CONTENT_LENGTH = get_env_int('MAX_CONTENT_LENGTH', 1024 * 1024)
    WTF_CSRF_SSL_STRICT = get_env_bool('WTF_CSRF_SSL_STRICT', False)
    SESSION_DEBUG = get_env_bool('SESSION_DEBUG', False)
    TRUSTED_HOSTS = get_env_csv(
        'TRUSTED_HOSTS',
        'localhost,127.0.0.1'
    )
    PROXY_FIX_X_FOR = get_env_int('PROXY_FIX_X_FOR', 1)
    PROXY_FIX_X_PROTO = get_env_int('PROXY_FIX_X_PROTO', 1)
    PROXY_FIX_X_HOST = get_env_int('PROXY_FIX_X_HOST', 1)
    CORS_ALLOWED_ORIGINS = get_env_csv(
        'CORS_ALLOWED_ORIGINS',
        'http://localhost,http://127.0.0.1,http://localhost:5175,http://127.0.0.1:5175'
    )
    OAUTH_ALLOWED_REDIRECT_HOSTS = get_env_csv(
        'OAUTH_ALLOWED_REDIRECT_HOSTS',
        'localhost,127.0.0.1'
    )
    OAUTH_ALLOW_HTTP_LOCALHOST = get_env_bool('OAUTH_ALLOW_HTTP_LOCALHOST', True)
    APP_ALLOWED_RETURN_HOSTS = get_env_csv(
        'APP_ALLOWED_RETURN_HOSTS',
        'localhost,127.0.0.1'
    )
    APP_ALLOW_HTTP_LOCALHOST = get_env_bool('APP_ALLOW_HTTP_LOCALHOST', True)
    DEFAULT_LOGIN_SUCCESS_URL = get_env_str('DEFAULT_LOGIN_SUCCESS_URL', '/home')
    DEV_AUTH_ENABLED = get_env_bool('DEV_AUTH_ENABLED', False)
    DEV_AUTH_ALLOWED_SOURCE_HOSTS = get_env_csv(
        'DEV_AUTH_ALLOWED_SOURCE_HOSTS',
        'localhost,127.0.0.1'
    )
    DEV_AUTH_ALLOWED_SOURCE_CIDRS = get_env_csv(
        'DEV_AUTH_ALLOWED_SOURCE_CIDRS',
        '127.0.0.1/32,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16'
    )
    DEV_AUTH_DEFAULT_PROVIDER = get_env_str('DEV_AUTH_DEFAULT_PROVIDER', 'dev')
    DEV_AUTH_REQUIRE_DEBUG = get_env_bool('DEV_AUTH_REQUIRE_DEBUG', True)
    DEV_AUTH_SHARED_SECRET = get_env_str('DEV_AUTH_SHARED_SECRET', '')
    OAUTH_PROVIDERS = load_oauth_providers_from_env()

    # PostgreSQL 数据库配置
    PGSQL_HOST = get_env_str('PGSQL_HOST', 'localhost')
    PGSQL_PORT = get_env_int('PGSQL_PORT', 5432)
    PGSQL_DB = get_env_str('PGSQL_DB', 'ustbhome')
    PGSQL_USER = get_env_str('PGSQL_USER', 'postgres')
    PGSQL_PASSWORD = RESOLVED_PGSQL_PASSWORD
    PGSQL_POOL_MIN_CONN = get_env_int('PGSQL_POOL_MIN_CONN', 1)
    PGSQL_POOL_MAX_CONN = get_env_int('PGSQL_POOL_MAX_CONN', 8)