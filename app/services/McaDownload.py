from __future__ import annotations

import posixpath
import re
from urllib.parse import unquote, urlsplit

from app.config import Config


MCA_VISIBILITY_PUBLIC = 'public'
MCA_VISIBILITY_AUTHENTICATED = 'authenticated'
MCA_VISIBILITY_ADMIN = 'admin'
MCA_VISIBILITIES = (
    MCA_VISIBILITY_PUBLIC,
    MCA_VISIBILITY_AUTHENTICATED,
    MCA_VISIBILITY_ADMIN,
)

_MCA_REGION_FILE_RE = re.compile(r'^r\.-?\d+\.-?\d+\.mca$')


class McaDownloadAuthorizationError(Exception):
    def __init__(self, message: str, status_code: int = 403):
        super().__init__(message)
        self.status_code = status_code


class McaDownloadAuthorizationService:
    @staticmethod
    def _normalize_path(value: str, *, field_name: str) -> str:
        raw = str(value or '').strip()
        if not raw:
            raise McaDownloadAuthorizationError(f'{field_name} 未配置', status_code=500)

        parsed = urlsplit(raw)
        path = unquote(parsed.path or raw)
        if not path.startswith('/'):
            path = '/' + path.lstrip('/')

        normalized = posixpath.normpath(path)
        if normalized in ('', '.', '/..', '..'):
            raise McaDownloadAuthorizationError(f'{field_name} 非法', status_code=500)
        if normalized == '/':
            return normalized
        return normalized.rstrip('/')

    @staticmethod
    def _path_has_prefix(path: str, prefix: str) -> bool:
        return path == prefix or path.startswith(prefix + '/')

    @staticmethod
    def _relative_path(path: str, prefix: str) -> str:
        if path == prefix:
            return ''
        return path[len(prefix):].lstrip('/')

    @staticmethod
    def _ensure_access(*, logged_in: bool, permission: int) -> str:
        visibility = str(Config.MCA_ACCESS_LEVEL or MCA_VISIBILITY_PUBLIC).strip().lower()
        if visibility not in MCA_VISIBILITIES:
            raise McaDownloadAuthorizationError('MCA_ACCESS_LEVEL 非法', status_code=500)
        if visibility == MCA_VISIBILITY_PUBLIC:
            return visibility
        if not logged_in:
            raise McaDownloadAuthorizationError('Not logged in', status_code=401)
        if visibility == MCA_VISIBILITY_AUTHENTICATED:
            return visibility
        if permission < 1:
            raise McaDownloadAuthorizationError('Forbidden', status_code=403)
        return visibility

    def authorize_download_request(self, forwarded_uri: str, *, logged_in: bool, permission: int) -> dict[str, str | bool]:
        request_path = self._normalize_path(forwarded_uri, field_name='X-Forwarded-Uri')
        allowed_base_path = self._normalize_path(Config.RUNTIME_CONFIG_MCA_BASE_URL, field_name='MCA_BASE_URL')

        if not self._path_has_prefix(request_path, allowed_base_path):
            raise McaDownloadAuthorizationError('MCA 请求路径超出允许前缀', status_code=404)

        relative_path = self._relative_path(request_path, allowed_base_path)
        if not relative_path:
            raise McaDownloadAuthorizationError('MCA 请求必须指向具体区域文件', status_code=404)

        file_name = posixpath.basename(relative_path)
        if not _MCA_REGION_FILE_RE.fullmatch(file_name):
            raise McaDownloadAuthorizationError('仅允许访问区域 mca 文件', status_code=404)

        visibility = self._ensure_access(logged_in=logged_in, permission=permission)
        return {
            'authorized': True,
            'path': request_path,
            'relative_path': relative_path,
            'base_path': allowed_base_path,
            'visibility': visibility,
        }