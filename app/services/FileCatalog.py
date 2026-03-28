from __future__ import annotations

import logging
import posixpath
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from psycopg2.extras import Json

from app.config import Config
from app.services.LocalStorage import LocalStorage, LocalStorageError
from app.utils.timezone import serialize_datetime_for_api


logger = logging.getLogger(__name__)

FILE_VISIBILITY_PUBLIC = 'public'
FILE_VISIBILITY_AUTHENTICATED = 'authenticated'
FILE_VISIBILITY_ADMIN = 'admin'
FILE_VISIBILITIES = (
    FILE_VISIBILITY_PUBLIC,
    FILE_VISIBILITY_AUTHENTICATED,
    FILE_VISIBILITY_ADMIN,
)


class FileCatalogError(Exception):
    """Raised when file catalog operations fail."""


class FileCatalogAuthorizationError(FileCatalogError):
    """Raised when file download authorization fails."""

    def __init__(self, message: str, status_code: int = 403):
        super().__init__(message)
        self.status_code = status_code


class FileCatalogStorage(LocalStorage):
    def __init__(self, host=None, user=None, password=None, db=None, port=None):
        super().__init__(host=host, user=user, password=password, db=db, port=port)
        self._init_schema()

    def _init_schema(self) -> None:
        try:
            with self.conn.cursor() as cursor:
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS file_catalog (
                        id BIGSERIAL PRIMARY KEY,
                        storage_key TEXT NOT NULL UNIQUE,
                        display_name TEXT NOT NULL,
                        download_name TEXT,
                        description TEXT,
                        mime_type TEXT,
                        size_bytes BIGINT,
                        visibility TEXT NOT NULL DEFAULT 'authenticated',
                        is_active BOOLEAN NOT NULL DEFAULT TRUE,
                        metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
                        created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                        updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
                        CONSTRAINT file_catalog_visibility_check CHECK (visibility IN ('public', 'authenticated', 'admin')),
                        CONSTRAINT file_catalog_size_bytes_check CHECK (size_bytes IS NULL OR size_bytes >= 0)
                    );
                    """
                )
                cursor.execute(
                    "CREATE INDEX IF NOT EXISTS idx_file_catalog_visibility_active ON file_catalog (visibility, is_active, id);"
                )
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS file_download_audit (
                        id BIGSERIAL PRIMARY KEY,
                        file_id BIGINT,
                        storage_key TEXT,
                        action TEXT NOT NULL,
                        outcome TEXT NOT NULL,
                        user_id TEXT,
                        username TEXT,
                        permission INTEGER,
                        remote_addr TEXT,
                        forwarded_uri TEXT,
                        error_message TEXT,
                        details JSONB NOT NULL DEFAULT '{}'::jsonb,
                        created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                        CONSTRAINT file_download_audit_action_check CHECK (action IN ('issue_token', 'authorize')),
                        CONSTRAINT file_download_audit_outcome_check CHECK (outcome IN ('success', 'denied'))
                    );
                    """
                )
                cursor.execute(
                    "CREATE INDEX IF NOT EXISTS idx_file_download_audit_created ON file_download_audit (created_at DESC, id DESC);"
                )
            self.conn.commit()
        except Exception as exc:
            try:
                self.conn.rollback()
            except Exception:
                pass
            raise LocalStorageError(str(exc)) from exc

    def get_file_row(self, file_id: int) -> dict[str, Any] | None:
        try:
            with self.conn.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT id, storage_key, display_name, download_name, description, mime_type,
                           size_bytes, visibility, is_active, metadata, created_at, updated_at
                    FROM file_catalog
                    WHERE id = %s;
                    """,
                    (file_id,),
                )
                return cursor.fetchone() or None
        except Exception as exc:
            raise LocalStorageError(str(exc)) from exc


class FileCatalogService:
    def __init__(self, storage: FileCatalogStorage | None = None):
        self.storage = storage or FileCatalogStorage()

    def close(self) -> None:
        self.storage.close()

    @staticmethod
    def _serialize_file_row(row: dict[str, Any] | None) -> dict[str, Any] | None:
        if row is None:
            return None
        return {
            'id': row.get('id'),
            'storage_key': row.get('storage_key'),
            'display_name': row.get('display_name'),
            'download_name': row.get('download_name'),
            'description': row.get('description'),
            'mime_type': row.get('mime_type'),
            'size_bytes': row.get('size_bytes'),
            'visibility': row.get('visibility'),
            'is_active': bool(row.get('is_active', True)),
            'metadata': row.get('metadata') or {},
            'created_at': serialize_datetime_for_api(row.get('created_at')),
            'updated_at': serialize_datetime_for_api(row.get('updated_at')),
        }

    @staticmethod
    def _serialize_audit_row(row: dict[str, Any] | None) -> dict[str, Any] | None:
        if row is None:
            return None
        return {
            'id': row.get('id'),
            'file_id': row.get('file_id'),
            'storage_key': row.get('storage_key'),
            'action': row.get('action'),
            'outcome': row.get('outcome'),
            'user_id': row.get('user_id'),
            'username': row.get('username'),
            'permission': row.get('permission'),
            'remote_addr': row.get('remote_addr'),
            'forwarded_uri': row.get('forwarded_uri'),
            'error_message': row.get('error_message'),
            'details': row.get('details') or {},
            'created_at': serialize_datetime_for_api(row.get('created_at')),
        }

    @staticmethod
    def _build_serializer() -> URLSafeTimedSerializer:
        return URLSafeTimedSerializer(
            Config.FILE_DOWNLOAD_TOKEN_SECRET,
            salt=Config.FILE_DOWNLOAD_TOKEN_SALT,
        )

    @staticmethod
    def normalize_storage_key(storage_key: str) -> str:
        value = str(storage_key or '').strip()
        if not value:
            raise FileCatalogError('storage_key 不能为空')
        if value.startswith('/') or value.startswith('\\'):
            raise FileCatalogError('storage_key 非法')
        if '\\' in value:
            raise FileCatalogError('storage_key 非法')

        normalized = posixpath.normpath(value)
        if normalized in ('', '.', '/'):
            raise FileCatalogError('storage_key 非法')
        if normalized == '..' or normalized.startswith('../'):
            raise FileCatalogError('storage_key 非法')
        return normalized

    @staticmethod
    def build_download_path(storage_key: str) -> str:
        base_path = '/' + (Config.FILE_DOWNLOAD_BASE_PATH or '/downloads').strip('/')
        return f"{base_path}/{FileCatalogService.normalize_storage_key(storage_key)}"

    @staticmethod
    def _ensure_file_access(file_row: dict[str, Any] | None, *, logged_in: bool, permission: int) -> dict[str, Any]:
        if not file_row or not file_row.get('is_active'):
            raise FileCatalogAuthorizationError('文件不存在', status_code=404)

        visibility = file_row.get('visibility')
        if visibility == FILE_VISIBILITY_PUBLIC:
            return file_row
        if visibility == FILE_VISIBILITY_AUTHENTICATED:
            if not logged_in:
                raise FileCatalogAuthorizationError('Not logged in', status_code=401)
            return file_row
        if visibility == FILE_VISIBILITY_ADMIN:
            if not logged_in:
                raise FileCatalogAuthorizationError('Not logged in', status_code=401)
            if permission < 1:
                raise FileCatalogAuthorizationError('Forbidden', status_code=403)
            return file_row
        raise FileCatalogAuthorizationError('文件不可访问', status_code=403)

    def list_files(
        self,
        *,
        access_levels: list[str],
        limit: int,
        offset: int,
        include_inactive: bool = False,
    ) -> dict[str, Any]:
        try:
            where_clauses = ['visibility = ANY(%s)']
            params: list[Any] = [access_levels]
            if not include_inactive:
                where_clauses.append('is_active = TRUE')

            where_sql = ' AND '.join(where_clauses)
            with self.storage.conn.cursor() as cursor:
                cursor.execute(
                    f'SELECT COUNT(*) AS total FROM file_catalog WHERE {where_sql};',
                    tuple(params),
                )
                total_row = cursor.fetchone() or {'total': 0}

                cursor.execute(
                    f"""
                    SELECT id, storage_key, display_name, download_name, description, mime_type,
                           size_bytes, visibility, is_active, metadata, created_at, updated_at
                    FROM file_catalog
                    WHERE {where_sql}
                    ORDER BY id ASC
                    LIMIT %s OFFSET %s;
                    """,
                    tuple(params + [limit, offset]),
                )
                rows = cursor.fetchall()

            return {
                'items': [self._serialize_file_row(row) for row in rows],
                'total': int(total_row.get('total', 0) or 0),
                'limit': limit,
                'offset': offset,
            }
        except Exception as exc:
            raise FileCatalogError(str(exc)) from exc

    def create_file(
        self,
        *,
        storage_key: str,
        display_name: str,
        download_name: str | None = None,
        description: str | None = None,
        mime_type: str | None = None,
        size_bytes: int | None = None,
        visibility: str = FILE_VISIBILITY_AUTHENTICATED,
        is_active: bool = True,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        normalized_storage_key = self.normalize_storage_key(storage_key)
        try:
            with self.storage.conn.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO file_catalog (
                        storage_key, display_name, download_name, description, mime_type,
                        size_bytes, visibility, is_active, metadata, updated_at
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
                    RETURNING id, storage_key, display_name, download_name, description, mime_type,
                              size_bytes, visibility, is_active, metadata, created_at, updated_at;
                    """,
                    (
                        normalized_storage_key,
                        display_name,
                        download_name,
                        description,
                        mime_type,
                        size_bytes,
                        visibility,
                        bool(is_active),
                        Json(metadata or {}),
                    ),
                )
                row = cursor.fetchone()
            self.storage.conn.commit()
            return self._serialize_file_row(row)
        except Exception as exc:
            try:
                self.storage.conn.rollback()
            except Exception:
                pass
            raise FileCatalogError(str(exc)) from exc

    def update_file(self, file_id: int, **updates: Any) -> dict[str, Any] | None:
        allowed_fields = {
            'storage_key',
            'display_name',
            'download_name',
            'description',
            'mime_type',
            'size_bytes',
            'visibility',
            'is_active',
            'metadata',
        }
        provided_updates = {key: value for key, value in updates.items() if key in allowed_fields}
        if not provided_updates:
            raise FileCatalogError('更新文件时至少提供一个字段')
        if 'storage_key' in provided_updates and provided_updates['storage_key'] is not None:
            provided_updates['storage_key'] = self.normalize_storage_key(provided_updates['storage_key'])

        try:
            assignments = []
            params: list[Any] = []
            for key, value in provided_updates.items():
                assignments.append(f'{key} = %s')
                if key == 'metadata':
                    params.append(Json(value or {}))
                else:
                    params.append(value)
            params.append(file_id)

            with self.storage.conn.cursor() as cursor:
                cursor.execute('SELECT 1 FROM file_catalog WHERE id = %s;', (file_id,))
                if cursor.fetchone() is None:
                    return None

                cursor.execute(
                    f"""
                    UPDATE file_catalog
                    SET {', '.join(assignments)}, updated_at = NOW()
                    WHERE id = %s
                    RETURNING id, storage_key, display_name, download_name, description, mime_type,
                              size_bytes, visibility, is_active, metadata, created_at, updated_at;
                    """,
                    tuple(params),
                )
                row = cursor.fetchone()
            self.storage.conn.commit()
            return self._serialize_file_row(row)
        except Exception as exc:
            try:
                self.storage.conn.rollback()
            except Exception:
                pass
            raise FileCatalogError(str(exc)) from exc

    def issue_download_token(self, file_id: int, *, logged_in: bool, permission: int) -> dict[str, Any]:
        file_row = self.storage.get_file_row(file_id)
        file_row = self._ensure_file_access(file_row, logged_in=logged_in, permission=permission)
        token = self._build_serializer().dumps(
            {
                'file_id': file_row['id'],
                'storage_key': file_row['storage_key'],
            }
        )
        return {
            'token': token,
            'token_type': 'file_download',
            'expires_in': Config.FILE_DOWNLOAD_TOKEN_TTL,
            'download_path': self.build_download_path(file_row['storage_key']),
            'download_url': f"{self.build_download_path(file_row['storage_key'])}?token={token}",
            'file': self._serialize_file_row(file_row),
        }

    def record_download_audit(
        self,
        *,
        action: str,
        outcome: str,
        file_id: int | None = None,
        storage_key: str | None = None,
        user_id: str | None = None,
        username: str | None = None,
        permission: int | None = None,
        remote_addr: str | None = None,
        forwarded_uri: str | None = None,
        error_message: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        try:
            with self.storage.conn.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO file_download_audit (
                        file_id, storage_key, action, outcome,
                        user_id, username, permission, remote_addr,
                        forwarded_uri, error_message, details
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING id, file_id, storage_key, action, outcome, user_id, username,
                              permission, remote_addr, forwarded_uri, error_message, details, created_at;
                    """,
                    (
                        file_id,
                        storage_key,
                        action,
                        outcome,
                        user_id,
                        username,
                        permission,
                        remote_addr,
                        forwarded_uri,
                        error_message,
                        Json(details or {}),
                    ),
                )
                row = cursor.fetchone()
            self.storage.conn.commit()
            return self._serialize_audit_row(row)
        except Exception as exc:
            try:
                self.storage.conn.rollback()
            except Exception:
                pass
            raise FileCatalogError(str(exc)) from exc

    def list_download_audits(
        self,
        *,
        file_id: int | None = None,
        action: str | None = None,
        outcome: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> dict[str, Any]:
        try:
            where_clauses = ['1=1']
            params: list[Any] = []
            if file_id is not None:
                where_clauses.append('file_id = %s')
                params.append(file_id)
            if action:
                where_clauses.append('action = %s')
                params.append(action)
            if outcome:
                where_clauses.append('outcome = %s')
                params.append(outcome)

            where_sql = ' AND '.join(where_clauses)
            with self.storage.conn.cursor() as cursor:
                cursor.execute(
                    f'SELECT COUNT(*) AS total FROM file_download_audit WHERE {where_sql};',
                    tuple(params),
                )
                total_row = cursor.fetchone() or {'total': 0}
                cursor.execute(
                    f"""
                    SELECT id, file_id, storage_key, action, outcome, user_id, username,
                           permission, remote_addr, forwarded_uri, error_message, details, created_at
                    FROM file_download_audit
                    WHERE {where_sql}
                    ORDER BY created_at DESC, id DESC
                    LIMIT %s OFFSET %s;
                    """,
                    tuple(params + [limit, offset]),
                )
                rows = cursor.fetchall()
            return {
                'items': [self._serialize_audit_row(row) for row in rows],
                'total': int(total_row.get('total', 0) or 0),
                'limit': limit,
                'offset': offset,
            }
        except Exception as exc:
            raise FileCatalogError(str(exc)) from exc

    def verify_download_token(self, token: str) -> dict[str, Any]:
        try:
            payload = self._build_serializer().loads(token, max_age=Config.FILE_DOWNLOAD_TOKEN_TTL)
        except SignatureExpired as exc:
            raise FileCatalogAuthorizationError('下载票据已过期', status_code=401) from exc
        except BadSignature as exc:
            raise FileCatalogAuthorizationError('下载票据无效', status_code=401) from exc

        file_id = payload.get('file_id')
        storage_key = payload.get('storage_key')
        file_row = self.storage.get_file_row(file_id)
        if not file_row or not file_row.get('is_active'):
            raise FileCatalogAuthorizationError('文件不存在', status_code=404)
        if file_row.get('storage_key') != storage_key:
            raise FileCatalogAuthorizationError('下载票据已失效', status_code=401)

        return {
            'file': self._serialize_file_row(file_row),
        }

    def authorize_download_request(self, forwarded_uri: str, *, logged_in: bool, permission: int) -> dict[str, Any]:
        if not forwarded_uri:
            raise FileCatalogAuthorizationError('缺少原始下载请求信息', status_code=400)

        parsed_uri = urlsplit(forwarded_uri)
        query = parse_qs(parsed_uri.query or '', keep_blank_values=False)
        token = (query.get('token') or [None])[0]
        if not token:
            raise FileCatalogAuthorizationError('缺少下载票据', status_code=401)

        verify_result = self.verify_download_token(token)
        file_row = verify_result['file']
        self._ensure_file_access(file_row, logged_in=logged_in, permission=permission)

        expected_path = self.build_download_path(file_row['storage_key'])
        request_path = unquote(parsed_uri.path or '')
        if request_path != expected_path:
            raise FileCatalogAuthorizationError('下载路径与票据不匹配', status_code=403)

        return {
            'authorized': True,
            'file': file_row,
        }