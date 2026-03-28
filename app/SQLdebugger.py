"""Minimal SQL operation logger used by a few legacy service paths."""

import logging
from functools import wraps
from datetime import datetime

from app.utils.timezone import get_app_timezone


class AppTimezoneFormatter(logging.Formatter):
    """Render SQL debug timestamps in the configured application timezone."""

    def formatTime(self, record, datefmt=None):
        dt = datetime.fromtimestamp(record.created, get_app_timezone())
        if datefmt:
            return dt.strftime(datefmt)
        return dt.isoformat(timespec='milliseconds')


class SQLDebugger:
    """Emit compact lifecycle logs around SQL-oriented helper functions."""

    def __init__(self, logger_name='sql_debugger', debug_enabled=True):
        self.logger = logging.getLogger(logger_name)
        self.debug_enabled = debug_enabled
        if not self.logger.handlers:
            handler = logging.StreamHandler()
            formatter = AppTimezoneFormatter('[%(asctime)s] [SQL-DEBUG] %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
            handler.setFormatter(formatter)
            self.logger.addHandler(handler)
        self.logger.setLevel(logging.DEBUG if debug_enabled else logging.INFO)
        self.logger.propagate = False

    def log(self, message):
        """Write verbose SQL debug output when the helper is enabled."""
        if self.debug_enabled:
            self.logger.debug(message)

    def info(self, message):
        """Write an informational SQL lifecycle event."""
        self.logger.info(message)

    def error(self, message):
        """Write an error SQL lifecycle event."""
        self.logger.error(message)


def debug_sql_decorator(operation_name):
    """Wrap a function with start/end/error logs for SQL-heavy operations."""
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            debugger = SQLDebugger(debug_enabled=True)
            debugger.info(f"SQL FUNCTION START: {operation_name} - {func.__name__}")
            if args or kwargs:
                debugger.log(f"Function args: {args}, kwargs: {kwargs}")
            try:
                result = func(*args, **kwargs)
                debugger.info(f"SQL FUNCTION END: {operation_name} - {func.__name__} (SUCCESS)")
                return result
            except Exception as e:
                debugger.error(f"SQL FUNCTION ERROR: {operation_name} - {func.__name__}: {type(e).__name__}: {str(e)}")
                raise
        return wrapper
    return decorator

