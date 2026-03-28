"""Typed helpers for reading and updating authenticated user session state."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, MutableMapping


def utc_now_iso() -> str:
	"""Return a stable UTC timestamp for values stored in Redis-backed sessions."""
	return datetime.now(timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')


def parse_session_timestamp(value: str | None) -> datetime | None:
	"""Parse an ISO-like session timestamp into a timezone-aware UTC datetime."""
	if not value:
		return None

	try:
		parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
	except ValueError:
		return None

	if parsed.tzinfo is None:
		return parsed.replace(tzinfo=timezone.utc)
	return parsed.astimezone(timezone.utc)


@dataclass
class UserSession:
	"""Normalize auth-provider payloads into the shape stored in Flask session."""
	user_id: str = ''
	username: str = ''
	email: str = ''
	nickname: str = ''
	avatar_url: str = ''
	oauth_provider: str = ''
	logged_in: bool = False
	login_time: str = ''
	last_refresh_time: str = ''
	permission: int = 0
	access_token: str | None = None
	refresh_token: str | None = None

	@classmethod
	def from_oauth_user(
		cls,
		user: Mapping[str, Any],
		provider: str,
		*,
		access_token: str | None = None,
		refresh_token: str | None = None,
	) -> 'UserSession':
		"""Build a fresh authenticated session snapshot from provider user data."""
		timestamp = utc_now_iso()
		permission = int(user.get('permission', 0) or 0)
		return cls(
			user_id=str(user.get('id', '')),
			username=str(user.get('username', '')),
			email=str(user.get('email', '')),
			nickname=str(user.get('nickname') or user.get('username') or ''),
			avatar_url=str(user.get('avatar_url', '')),
			oauth_provider=provider,
			logged_in=True,
			login_time=timestamp,
			last_refresh_time=timestamp,
			permission=permission,
			access_token=access_token,
			refresh_token=refresh_token if provider == 'ustb' else None,
		)

	@classmethod
	def from_session(cls, session_store: Mapping[str, Any]) -> 'UserSession':
		"""Hydrate a typed view from the existing session mapping."""
		return cls(
			user_id=str(session_store.get('user_id', '')),
			username=str(session_store.get('username', '')),
			email=str(session_store.get('email', '')),
			nickname=str(session_store.get('nickname', '')),
			avatar_url=str(session_store.get('avatar_url', '')),
			oauth_provider=str(session_store.get('oauth_provider', '')),
			logged_in=bool(session_store.get('logged_in', False)),
			login_time=str(session_store.get('login_time', '')),
			last_refresh_time=str(session_store.get('last_refresh_time', '')),
			permission=int(session_store.get('permission', 0) or 0),
			access_token=session_store.get('access_token'),
			refresh_token=session_store.get('refresh_token'),
		)

	def update_profile(self, user: Mapping[str, Any]) -> None:
		"""Refresh mutable profile fields without rebuilding the whole session object."""
		self.user_id = str(user.get('id', self.user_id))
		self.username = str(user.get('username', self.username))
		self.email = str(user.get('email', self.email))
		self.nickname = str(user.get('nickname') or user.get('username') or self.nickname)
		self.avatar_url = str(user.get('avatar_url', self.avatar_url))
		if 'permission' in user:
			self.permission = int(user.get('permission', self.permission) or 0)

	def mark_refreshed(self) -> None:
		"""Update the last-refresh marker after a successful token refresh."""
		self.last_refresh_time = utc_now_iso()

	def apply_to_session(self, session_store: MutableMapping[str, Any]) -> None:
		"""Write the normalized model back into the Flask session store."""
		session_store['user_id'] = self.user_id
		session_store['username'] = self.username
		session_store['email'] = self.email
		session_store['nickname'] = self.nickname
		session_store['avatar_url'] = self.avatar_url
		session_store['oauth_provider'] = self.oauth_provider
		session_store['logged_in'] = self.logged_in
		session_store['login_time'] = self.login_time
		session_store['last_refresh_time'] = self.last_refresh_time
		session_store['permission'] = self.permission

		if self.access_token:
			session_store['access_token'] = self.access_token
		else:
			session_store.pop('access_token', None)

		if self.oauth_provider == 'ustb' and self.refresh_token:
			session_store['refresh_token'] = self.refresh_token
		else:
			session_store.pop('refresh_token', None)
