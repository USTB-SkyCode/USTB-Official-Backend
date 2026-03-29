import logging

import psycopg2
from psycopg2.extras import RealDictCursor
from app.config import Config


logger = logging.getLogger(__name__)


class LocalStorageError(Exception):
    """Custom exception for LocalStorage errors to be returned by API."""
    pass


class LocalStorageLockError(LocalStorageError):
    """Raised when an advisory lock cannot be acquired."""


class LocalStorage:
    def __init__(self, host=None, user=None, password=None, db=None, port=None):
        try:
            self.conn = psycopg2.connect(
                host=host or Config.PGSQL_HOST,
                user=user or Config.PGSQL_USER,
                password=password or Config.PGSQL_PASSWORD,
                dbname=db or Config.PGSQL_DB,
                port=port or Config.PGSQL_PORT,
                cursor_factory=RealDictCursor
            )
        except psycopg2.Error as e:
            # Wrap DB connection errors
            raise LocalStorageError(str(e))

    def _safe_rollback(self) -> None:
        try:
            self.conn.rollback()
        except Exception:
            logger.debug('Rollback skipped because the database connection is unavailable', exc_info=True)

    def _safe_close(self) -> None:
        try:
            if hasattr(self, 'conn') and self.conn:
                self.conn.close()
        except Exception:
            logger.debug('Connection close skipped because the database connection is unavailable', exc_info=True)

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
                return bool(row.get('locked'))
        except psycopg2.Error as e:
            self._safe_rollback()
            raise LocalStorageLockError(str(e))

    def advisory_unlock(self, key: int) -> None:
        try:
            with self.conn.cursor() as cursor:
                cursor.execute('SELECT pg_advisory_unlock(%s);', (key,))
        except psycopg2.Error as e:
            self._safe_rollback()
            raise LocalStorageLockError(str(e))

