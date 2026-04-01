import os

def _env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None or value == '':
        return default
    return value.strip().lower() == 'true'

bind = f"{os.environ.get('FLASK_HOST', '0.0.0.0')}:{os.environ.get('FLASK_PORT', '5000')}"
workers = int(os.environ.get('GUNICORN_WORKERS', '1'))  # Optimized for server memory (1 worker, 4 threads)
threads = int(os.environ.get('GUNICORN_THREADS', '4'))
timeout = int(os.environ.get('GUNICORN_TIMEOUT', '60'))
graceful_timeout = int(os.environ.get('GUNICORN_GRACEFUL_TIMEOUT', '30'))
worker_class = os.environ.get('GUNICORN_WORKER_CLASS', 'gthread')
reload = _env_bool('GUNICORN_RELOAD', False)
accesslog = '-'
errorlog = '-'
capture_output = True
loglevel = os.environ.get('GUNICORN_LOG_LEVEL', 'info')
preload_app = False
