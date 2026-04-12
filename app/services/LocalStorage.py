import logging
import threading

import psycopg2
from psycopg2.extras import RealDictCursor
from psycopg2.pool import PoolError, ThreadedConnectionPool
from app.config import Config


logger = logging.getLogger(__name__)

_DEFAULT_CONNECTION_POOL = None
_DEFAULT_CONNECTION_POOL_LOCK = threading.Lock()


def _build_connection_kwargs(host=None, user=None, password=None, db=None, port=None):
    return {
        'host': host or Config.PGSQL_HOST,
        'user': user or Config.PGSQL_USER,
        'password': password or Config.PGSQL_PASSWORD,
        'dbname': db or Config.PGSQL_DB,
        'port': port or Config.PGSQL_PORT,
        'cursor_factory': RealDictCursor,
    }


def _resolve_pool_bounds():
    min_conn = max(1, int(Config.PGSQL_POOL_MIN_CONN or 1))
    max_conn = max(min_conn, int(Config.PGSQL_POOL_MAX_CONN or min_conn))
    return min_conn, max_conn


def _get_default_connection_pool():
    global _DEFAULT_CONNECTION_POOL

    if _DEFAULT_CONNECTION_POOL is not None:
        return _DEFAULT_CONNECTION_POOL

    with _DEFAULT_CONNECTION_POOL_LOCK:
        if _DEFAULT_CONNECTION_POOL is None:
            min_conn, max_conn = _resolve_pool_bounds()
            _DEFAULT_CONNECTION_POOL = ThreadedConnectionPool(
                minconn=min_conn,
                maxconn=max_conn,
                **_build_connection_kwargs(),
            )

    return _DEFAULT_CONNECTION_POOL


class LocalStorageError(Exception):
    """Custom exception for LocalStorage errors to be returned by API."""
    pass


class LocalStorageLockError(LocalStorageError):
    """Raised when an advisory lock cannot be acquired."""


class LocalStorage:
    def __init__(self, host=None, user=None, password=None, db=None, port=None):
        self.conn = None
        self._pool = None
        self._held_advisory_lock_keys = set()

        connection_kwargs = _build_connection_kwargs(host=host, user=user, password=password, db=db, port=port)
        try:
            if self._should_use_default_pool(connection_kwargs):
                self._pool = _get_default_connection_pool()
                self.conn = self._pool.getconn()
            else:
                self.conn = psycopg2.connect(**connection_kwargs)
        except (psycopg2.Error, PoolError) as e:
            # Wrap DB connection errors
            raise LocalStorageError(str(e))

    @staticmethod
    def _should_use_default_pool(connection_kwargs) -> bool:
        default_kwargs = _build_connection_kwargs()
        for key in ('host', 'user', 'password', 'dbname', 'port'):
            if connection_kwargs.get(key) != default_kwargs.get(key):
                return False
        return True

    def _release_held_advisory_locks(self) -> bool:
        if not self._held_advisory_lock_keys or not self.conn:
            return True

        try:
            with self.conn.cursor() as cursor:
                for key in tuple(self._held_advisory_lock_keys):
                    cursor.execute('SELECT pg_advisory_unlock(%s);', (key,))
            self._held_advisory_lock_keys.clear()
            return True
        except psycopg2.Error:
            self._safe_rollback()
            logger.warning('Failed to release advisory locks before returning pooled connection', exc_info=True)
            return False

    def _safe_rollback(self) -> None:
        try:
            self.conn.rollback()
        except Exception:
            logger.debug('Rollback skipped because the database connection is unavailable', exc_info=True)

    def _safe_close(self) -> None:
        try:
            if hasattr(self, 'conn') and self.conn:
                if self._pool is not None:
                    should_discard = not self._release_held_advisory_locks()
                    self._safe_rollback()
                    self._pool.putconn(self.conn, close=should_discard)
                else:
                    self.conn.close()
        except Exception:
            logger.debug('Connection close skipped because the database connection is unavailable', exc_info=True)
        finally:
            self.conn = None
            self._pool = None
            self._held_advisory_lock_keys.clear()

    def __query(self, sql, params=None):
        try:
            with self.conn.cursor() as cursor:
                cursor.execute(sql, params or ())
                return cursor.fetchall()
        except psycopg2.Error as e:
            self._safe_rollback()
            raise LocalStorageError(str(e))
        
    def __execute(self, sql, params=None):
        try:
            with self.conn.cursor() as cursor:
                cursor.execute(sql, params or ())
                self.conn.commit()
                try:
                    return cursor.fetchone()
                except psycopg2.ProgrammingError:
                    return None
        except psycopg2.Error as e:
            self._safe_rollback()
            raise LocalStorageError(str(e))
        
    def close(self):
        self._safe_close()

    def try_advisory_lock(self, key: int) -> bool:
        try:
            with self.conn.cursor() as cursor:
                cursor.execute('SELECT pg_try_advisory_lock(%s) AS locked;', (key,))
                row = cursor.fetchone() or {}
                locked = bool(row.get('locked'))
                if locked:
                    self._held_advisory_lock_keys.add(key)
                return locked
        except psycopg2.Error as e:
            self._safe_rollback()
            raise LocalStorageLockError(str(e))

    def advisory_unlock(self, key: int) -> None:
        try:
            with self.conn.cursor() as cursor:
                cursor.execute('SELECT pg_advisory_unlock(%s);', (key,))
            self._held_advisory_lock_keys.discard(key)
        except psycopg2.Error as e:
            self._safe_rollback()
            raise LocalStorageLockError(str(e))

