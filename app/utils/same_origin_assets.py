"""Helpers for turning third-party asset references into same-origin URLs.

The frontend runs under COEP/COOP in production, so image URLs must already be
same-origin by the time they reach the browser. These helpers keep that trust
boundary on the backend side.
"""

from __future__ import annotations

from urllib.parse import quote, urlencode, urlsplit

from app.config import Config


def _normalize_proxy_prefix(value: str) -> str:
    prefix = str(value or '').strip()
    if not prefix:
        return '/skin-origin-proxy'
    if not prefix.startswith('/'):
        prefix = '/' + prefix
    return prefix.rstrip('/') or '/skin-origin-proxy'


def get_asset_proxy_prefix() -> str:
    return _normalize_proxy_prefix(Config.SAME_ORIGIN_ASSET_PROXY_PATH)


def build_ustb_texture_proxy_url(skin_version: str) -> str:
    """Build the same-origin URL used to fetch a resolved USTB skin texture."""
    version = str(skin_version or '').strip()
    if not version:
        return ''
    return f"{get_asset_proxy_prefix()}/static/textures/{quote(version, safe='')}" + '.png'


def build_ustb_oauth_avatar_proxy_url() -> str:
    """Build the same-origin URL for the documented vSkin OAuth avatar API."""
    return f"{get_asset_proxy_prefix()}/oauth/avatar"

def build_external_asset_proxy_url(raw_url: str) -> str:
    value = str(raw_url or '').strip()
    if not value:
        return ''
    return f"{get_asset_proxy_prefix()}/external?{urlencode({'url': value})}"


def is_allowed_external_asset_url(raw_url: str) -> bool:
    """Allow only explicit HTTPS hosts through the backend asset proxy."""
    value = str(raw_url or '').strip()
    if not value:
        return False

    parsed = urlsplit(value)
    if parsed.scheme != 'https' or not parsed.hostname:
        return False

    allowed_hosts = {host.strip().lower() for host in (Config.ASSET_PROXY_ALLOWED_HOSTS or []) if host.strip()}
    return parsed.hostname.lower() in allowed_hosts


def rewrite_avatar_url_for_same_origin(raw_url: str, *, provider: str) -> str:
    """Rewrite provider avatar URLs into browser-safe same-origin URLs.

    USTB avatars follow the documented OAuth avatar endpoint. Other providers
    are only accepted when their source host is explicitly allowlisted.
    """
    value = str(raw_url or '').strip()
    if provider == 'ustb':
        if not value:
            return build_ustb_oauth_avatar_proxy_url()
        if value.startswith('/'):
            return value
        if is_allowed_external_asset_url(value):
            return build_external_asset_proxy_url(value)
        return build_ustb_oauth_avatar_proxy_url()
    if not value:
        return ''
    if value.startswith('/'):
        return value
    if is_allowed_external_asset_url(value):
        return build_external_asset_proxy_url(value)
    return ''