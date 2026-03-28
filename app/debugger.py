"""Structured request/session debugging helpers used by auth and session flows."""

import json
import datetime
import logging
from functools import wraps
from flask import session, request, g
from app.config import Config
from app.utils.timezone import get_app_timezone


def _get_app_timezone():
    return get_app_timezone()


def _now_in_app_timezone():
    return datetime.datetime.now(_get_app_timezone())


class AppTimezoneFormatter(logging.Formatter):
    """Render log timestamps in the application timezone instead of system UTC."""

    def formatTime(self, record, datefmt=None):
        dt = datetime.datetime.fromtimestamp(record.created, _get_app_timezone())
        if datefmt:
            return dt.strftime(datefmt)
        return dt.isoformat(timespec='milliseconds')


class SessionDebugger:
    """Attach request lifecycle logging around Flask session mutations."""
    
    def __init__(self, app=None, logger_name='session_debug'):
        self.app = app
        self.logger_name = logger_name
        self.logger = None
        self.debug_enabled = Config.DEBUG
        self.debug_level = Config.SESSION_DEBUG
        if app:
            self.init_app(app)
    
    def init_app(self, app):
        """Register request hooks and expose the debugger on the Flask app."""
        self.app = app
        self.logger = logging.getLogger(self.logger_name)
        
        if not self.logger.handlers:
            self._setup_logger(app)
        
        app.before_request(self.before_request_debug)
        app.after_request(self.after_request_debug)
        app.teardown_request(self.teardown_request_debug)
        app.session_debugger = self
    
    def _setup_logger(self, app):
        """Configure console and optional file handlers once per logger name."""
        level = logging.DEBUG if self.debug_enabled else logging.INFO
        self.logger.setLevel(level)
        self.logger.handlers.clear()
        console_handler = logging.StreamHandler()
        log_file = app.config.get('SESSION_DEBUG_LOG_FILE')
        if log_file:
            file_handler = logging.FileHandler(log_file)
            file_formatter = AppTimezoneFormatter(
                '%(asctime)s.%(msecs)03d [%(name)s] [%(levelname)s] %(message)s',
                datefmt='%Y-%m-%d %H:%M:%S'
            )
            file_handler.setFormatter(file_formatter)
            self.logger.addHandler(file_handler)

        console_formatter = SessionLogFormatter()
        console_handler.setFormatter(console_formatter)
        self.logger.addHandler(console_handler)

        for handler in self.logger.handlers:
            handler.setLevel(level)

        self.logger.propagate = False
    
    def log(self, message, level="INFO"):
        """Emit a message only when session debugging is enabled."""
        if not self.debug_enabled:
            return
        level_map = {
            "DEBUG": logging.DEBUG,
            "INFO": logging.INFO,
            "WARNING": logging.WARNING,
            "ERROR": logging.ERROR,
            "CRITICAL": logging.CRITICAL
        }
        log_level = level_map.get(level.upper(), logging.INFO)
        if log_level == logging.DEBUG:
            if self.debug_level:
                self.logger.debug(message)
        elif log_level == logging.INFO:
            self.logger.info(message)
        elif log_level == logging.WARNING:
            self.logger.warning(message)
        elif log_level == logging.ERROR:
            self.logger.error(message)
        elif log_level == logging.CRITICAL:
            self.logger.critical(message)

    def debug(self, message):
        """Log a debug message when verbose session tracing is enabled."""
        if self.debug_enabled and self.debug_level:
            self.logger.debug(message)

    def info(self, message):
        """Log an informational session-debug message."""
        if self.debug_enabled:
            self.logger.info(message)
    
    def warning(self, message):
        """Log a warning even if verbose debugging is disabled."""
        self.logger.warning(message)
    
    def error(self, message):
        """Log an error even if verbose debugging is disabled."""
        self.logger.error(message)
    
    def critical(self, message):
        """Log a critical error even if verbose debugging is disabled."""
        self.logger.critical(message)
    
    def format_session_data(self, session_data):
        """Redact non-primitive session values into a readable debug payload."""
        if not session_data:
            return "Empty"
        
        formatted = {}
        for key, value in session_data.items():
            if isinstance(value, (str, int, float, bool)):
                formatted[key] = value
            elif isinstance(value, bytes):
                formatted[key] = f"<bytes: {len(value)} bytes>"
            else:
                formatted[key] = str(value)[:100] + "..." if len(str(value)) > 100 else str(value)
        
        return json.dumps(formatted, indent=2, ensure_ascii=False)
    
    def log_session_id_change(self, before_id, after_id, operation):
        """Highlight SID rotation events, which are security-sensitive changes."""
        if before_id != after_id:
            self.warning(f"[SESSION-ID-CHANGE] {operation}: {before_id} -> {after_id}")
        else:
            self.info(f"[SESSION-ID-PRESERVED] {operation}: {before_id}")
    
    def before_request_debug(self):
        """Capture inbound request/session state before application handlers run."""
        if not self.debug_enabled:
            return
            
        self.info("=" * 80)
        self.info(f"REQUEST START: {request.method} {request.url}")
        self.debug(f"Remote IP: {request.remote_addr}")
        self.debug(f"User Agent: {request.headers.get('User-Agent', 'Unknown')}")
        
        session_data = dict(session) if session else {}
        self.debug(f"Session before request:")
        self.debug(f"Session ID: {getattr(session, 'sid', 'No SID')}")
        self.debug(f"Session data:\n{self.format_session_data(session_data)}")
        
        cookies = dict(request.cookies)
        if cookies:
            self.debug(f"Request cookies: {json.dumps(cookies, indent=2)}")
        else:
            self.debug("No cookies in request")
        
        g.debug_start_time = _now_in_app_timezone()

    def after_request_debug(self, response):
        """Capture outbound response/session state after handlers finish."""
        if not self.debug_enabled:
            return response
            
        session_data = dict(session) if session else {}
        self.debug(f"Session after request:")
        self.debug(f"Session ID: {getattr(session, 'sid', 'No SID')}")
        self.debug(f"Session data:\n{self.format_session_data(session_data)}")
        
        response_cookies = []
        for cookie in response.headers.getlist('Set-Cookie'):
            response_cookies.append(cookie)
        
        if response_cookies:
            self.debug(f"Response cookies:")
            for cookie in response_cookies:
                self.debug(f"  {cookie}")
        else:
            self.debug("No cookies in response")
        
        self.info(f"Response status: {response.status_code}")
        user = session.get('username', 'anonymous')
        self.info(f"request from: {user}")
        
        if hasattr(g, 'debug_start_time'):
            duration = _now_in_app_timezone() - g.debug_start_time
            self.info(f"Request duration: {duration.total_seconds():.3f}s")
        
        self.info("REQUEST END")
        self.info("=" * 80)
        
        return response
    
    def teardown_request_debug(self, exception):
        """Log uncaught exceptions observed during request teardown."""
        if exception:
            self.error(f"REQUEST EXCEPTION: {type(exception).__name__}: {str(exception)}")


class SessionLogFormatter(logging.Formatter):
    """Keep session-debug output aligned with the existing log format."""
    
    def format(self, record):
        timestamp = _now_in_app_timezone().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        return f"[{timestamp}] [SESSION-DEBUG] [{record.levelname}] {record.getMessage()}"


def debug_session_decorator(operation_name):
    """Wrap a function with before/after session snapshots for one operation."""
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            from flask import current_app
            debugger = getattr(current_app, 'session_debugger', None)
            
            if debugger and debugger.debug_enabled:
                debugger.info(f"FUNCTION START: {operation_name} - {func.__name__}")
                
                session_before = dict(session) if session else {}
                session_id_before = getattr(session, 'sid', 'No SID')
                debugger.debug(f"Session before {operation_name}:\n{debugger.format_session_data(session_before)}")
                debugger.debug(f"Session ID before {operation_name}: {session_id_before}")
                
                if args or kwargs:
                    debugger.debug(f"Function args: {args}, kwargs: {kwargs}")
            
            try:
                result = func(*args, **kwargs)
                
                if debugger and debugger.debug_enabled:
                    session_after = dict(session) if session else {}
                    session_id_after = getattr(session, 'sid', 'No SID')
                    debugger.debug(f"Session after {operation_name}:\n{debugger.format_session_data(session_after)}")
                    debugger.debug(f"Session ID after {operation_name}: {session_id_after}")
                    
                    debugger.log_session_id_change(session_id_before, session_id_after, operation_name)
                    
                    debugger.info(f"FUNCTION END: {operation_name} - {func.__name__} (SUCCESS)")
                
                return result
                
            except Exception as e:
                if debugger:
                    debugger.error(f"FUNCTION ERROR: {operation_name} - {func.__name__}: {type(e).__name__}: {str(e)}")
                raise
        
        return wrapper
    return decorator


session_debugger = SessionDebugger()

def debug_oauth_login(func):
    """Preserve the historical auth-login decorator entry point."""
    return debug_session_decorator("OAuth Login")(func)


def debug_oauth_callback(func):
    """Preserve the historical auth-callback decorator entry point."""
    return debug_session_decorator("OAuth Callback")(func)


def debug_session_create(func):
    """Preserve the historical session-create decorator entry point."""
    return debug_session_decorator("Session Create")(func)


def debug_session_destroy(func):
    """Preserve the historical session-destroy decorator entry point."""
    return debug_session_decorator("Session Destroy")(func)


def debug_session_refresh(func):
    """Preserve the historical session-refresh decorator entry point."""
    return debug_session_decorator("Session Refresh")(func)
