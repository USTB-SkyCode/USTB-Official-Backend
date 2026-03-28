from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from psycopg2.extras import Json

from app.services.LocalStorage import LocalStorage, LocalStorageError
from app.utils.timezone import serialize_datetime_for_api


class JobStatusError(Exception):
    """Raised when job status persistence fails."""


class JobStatusStorage(LocalStorage):
    def __init__(self, host=None, user=None, password=None, db=None, port=None):
        super().__init__(host=host, user=user, password=password, db=db, port=port)
        self._init_schema()

    def _init_schema(self) -> None:
        try:
            with self.conn.cursor() as cursor:
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS job_status (
                        job_name TEXT PRIMARY KEY,
                        interval_seconds INTEGER,
                        is_running BOOLEAN NOT NULL DEFAULT FALSE,
                        last_started_at TIMESTAMP,
                        last_finished_at TIMESTAMP,
                        last_success_at TIMESTAMP,
                        last_error_at TIMESTAMP,
                        last_error_message TEXT,
                        last_result JSONB,
                        updated_at TIMESTAMP NOT NULL DEFAULT NOW()
                    );
                    """
                )
            self.conn.commit()
        except Exception as exc:
            try:
                self.conn.rollback()
            except Exception:
                pass
            raise LocalStorageError(str(exc)) from exc


class JobStatusService:
    def __init__(self, storage: JobStatusStorage | None = None):
        self.storage = storage or JobStatusStorage()

    def close(self) -> None:
        self.storage.close()

    @staticmethod
    def _serialize_row(row: dict[str, Any] | None) -> dict[str, Any] | None:
        if row is None:
            return None

        interval_seconds = row.get('interval_seconds')
        last_finished_at = row.get('last_finished_at')
        next_run_due_at = None
        if interval_seconds and last_finished_at:
            try:
                next_run_due_at = last_finished_at + timedelta(seconds=int(interval_seconds))
            except Exception:
                next_run_due_at = None

        return {
            'job_name': row.get('job_name'),
            'interval_seconds': interval_seconds,
            'is_running': bool(row.get('is_running', False)),
            'last_started_at': serialize_datetime_for_api(row.get('last_started_at')),
            'last_finished_at': serialize_datetime_for_api(row.get('last_finished_at')),
            'last_success_at': serialize_datetime_for_api(row.get('last_success_at')),
            'last_error_at': serialize_datetime_for_api(row.get('last_error_at')),
            'last_error_message': row.get('last_error_message'),
            'last_result': row.get('last_result'),
            'updated_at': serialize_datetime_for_api(row.get('updated_at')),
            'next_run_due_at': serialize_datetime_for_api(next_run_due_at),
        }

    def mark_running(self, job_name: str, *, interval_seconds: int | None = None) -> None:
        self._upsert_status(
            job_name,
            interval_seconds=interval_seconds,
            is_running=True,
            set_started=True,
        )

    def mark_success(self, job_name: str, *, interval_seconds: int | None = None, result: dict[str, Any] | None = None) -> None:
        self._upsert_status(
            job_name,
            interval_seconds=interval_seconds,
            is_running=False,
            set_finished=True,
            set_success=True,
            clear_error=True,
            result=result,
        )

    def mark_failure(self, job_name: str, *, interval_seconds: int | None = None, error_message: str) -> None:
        self._upsert_status(
            job_name,
            interval_seconds=interval_seconds,
            is_running=False,
            set_finished=True,
            set_error=True,
            error_message=error_message,
        )

    def list_statuses(self) -> dict[str, Any]:
        try:
            with self.storage.conn.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT job_name, interval_seconds, is_running, last_started_at, last_finished_at,
                           last_success_at, last_error_at, last_error_message, last_result, updated_at
                    FROM job_status
                    ORDER BY job_name ASC;
                    """
                )
                rows = cursor.fetchall()
            items = [self._serialize_row(row) for row in rows]
            return {
                'items': items,
                'total': len(items),
            }
        except Exception as exc:
            raise JobStatusError(str(exc)) from exc

    def _upsert_status(
        self,
        job_name: str,
        *,
        interval_seconds: int | None,
        is_running: bool,
        set_started: bool = False,
        set_finished: bool = False,
        set_success: bool = False,
        set_error: bool = False,
        clear_error: bool = False,
        error_message: str | None = None,
        result: dict[str, Any] | None = None,
    ) -> None:
        try:
            with self.storage.conn.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO job_status (
                        job_name, interval_seconds, is_running,
                        last_started_at, last_finished_at, last_success_at, last_error_at,
                        last_error_message, last_result, updated_at
                    )
                    VALUES (
                        %s, %s, %s,
                        CASE WHEN %s THEN NOW() ELSE NULL END,
                        CASE WHEN %s THEN NOW() ELSE NULL END,
                        CASE WHEN %s THEN NOW() ELSE NULL END,
                        CASE WHEN %s THEN NOW() ELSE NULL END,
                        CASE WHEN %s THEN %s ELSE NULL END,
                        %s,
                        NOW()
                    )
                    ON CONFLICT (job_name) DO UPDATE SET
                        interval_seconds = COALESCE(EXCLUDED.interval_seconds, job_status.interval_seconds),
                        is_running = EXCLUDED.is_running,
                        last_started_at = CASE WHEN %s THEN NOW() ELSE job_status.last_started_at END,
                        last_finished_at = CASE WHEN %s THEN NOW() ELSE job_status.last_finished_at END,
                        last_success_at = CASE WHEN %s THEN NOW() ELSE job_status.last_success_at END,
                        last_error_at = CASE WHEN %s THEN NOW() ELSE job_status.last_error_at END,
                        last_error_message = CASE
                            WHEN %s THEN NULL
                            WHEN %s THEN %s
                            ELSE job_status.last_error_message
                        END,
                        last_result = COALESCE(EXCLUDED.last_result, job_status.last_result),
                        updated_at = NOW();
                    """,
                    (
                        job_name,
                        interval_seconds,
                        is_running,
                        set_started,
                        set_finished,
                        set_success,
                        set_error,
                        set_error,
                        error_message,
                        Json(result) if result is not None else None,
                        set_started,
                        set_finished,
                        set_success,
                        set_error,
                        clear_error,
                        set_error,
                        error_message,
                    ),
                )
            self.storage.conn.commit()
        except Exception as exc:
            try:
                self.storage.conn.rollback()
            except Exception:
                pass
            raise JobStatusError(str(exc)) from exc