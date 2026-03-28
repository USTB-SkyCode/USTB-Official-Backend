from __future__ import annotations

import hashlib
import logging
import threading
import time
from datetime import datetime, timezone
from typing import Any, Optional

from psycopg2.extras import Json

from app.config import Config
from app.services.JobStatus import JobStatusService
from app.services.LocalStorage import LocalStorage, LocalStorageError, LocalStorageLockError
from app.utils.timezone import serialize_datetime_for_api


logger = logging.getLogger(__name__)

RSS_SYNC_LOCK_KEY = 91001
RSS_REFRESH_JOB_NAME = 'rss_feed_refresh'


class FeedServiceError(Exception):
	"""Raised when RSS sync or query operations fail."""


class FeedSyncConflictError(FeedServiceError):
	"""Raised when another RSS sync is already in progress."""


def _safe_json_value(value: Any) -> Any:
	if value is None or isinstance(value, (str, int, float, bool)):
		return value
	if isinstance(value, datetime):
		return value.isoformat()
	if isinstance(value, time.struct_time):
		return time.strftime('%Y-%m-%dT%H:%M:%SZ', value)
	if isinstance(value, dict):
		return {str(key): _safe_json_value(item) for key, item in value.items()}
	if isinstance(value, (list, tuple, set)):
		return [_safe_json_value(item) for item in value]
	if hasattr(value, 'items'):
		return {str(key): _safe_json_value(item) for key, item in value.items()}
	return str(value)


def _extract_entry_content(entry: dict[str, Any]) -> str | None:
	content_blocks = entry.get('content')
	if isinstance(content_blocks, list) and content_blocks:
		first_block = content_blocks[0]
		if isinstance(first_block, dict):
			content_value = first_block.get('value')
			if content_value:
				return str(content_value)
	summary = entry.get('summary') or entry.get('description')
	return str(summary) if summary else None


def _extract_published_at(entry: dict[str, Any]) -> tuple[Optional[datetime], Optional[str]]:
	parsed_time = entry.get('published_parsed') or entry.get('updated_parsed')
	text_value = entry.get('published') or entry.get('updated')

	published_at = None
	if isinstance(parsed_time, time.struct_time):
		published_at = datetime.fromtimestamp(time.mktime(parsed_time), tz=timezone.utc).replace(tzinfo=None)

	published_text = str(text_value) if text_value else None
	return published_at, published_text


def _build_entry_guid(entry: dict[str, Any], feed_url: str) -> str:
	guid = entry.get('id') or entry.get('guid') or entry.get('link')
	if guid:
		return str(guid)

	fallback_source = '|'.join(
		[
			feed_url,
			str(entry.get('title') or ''),
			str(entry.get('published') or entry.get('updated') or ''),
			str(_extract_entry_content(entry) or ''),
		]
	)
	return hashlib.sha256(fallback_source.encode('utf-8')).hexdigest()


def parse_feed_url(url: str):
	import feedparser

	return feedparser.parse(url)


class FeedStorage(LocalStorage):
	def __init__(self, host=None, user=None, password=None, db=None, port=None):
		super().__init__(host=host, user=user, password=password, db=db, port=port)
		self._init_schema()

	def _init_schema(self) -> None:
		try:
			with self.conn.cursor() as cursor:
				cursor.execute(
					"""
					CREATE TABLE IF NOT EXISTS rss_feeds (
						id SERIAL PRIMARY KEY,
						source_url TEXT NOT NULL UNIQUE,
						title TEXT,
						description TEXT,
						site_url TEXT,
						etag TEXT,
						modified TEXT,
						last_fetched_at TIMESTAMP,
						created_at TIMESTAMP NOT NULL DEFAULT NOW(),
						updated_at TIMESTAMP NOT NULL DEFAULT NOW()
					);
					"""
				)
				cursor.execute(
					"""
					CREATE TABLE IF NOT EXISTS rss_entries (
						id BIGSERIAL PRIMARY KEY,
						feed_id INTEGER NOT NULL REFERENCES rss_feeds(id) ON DELETE CASCADE,
						guid TEXT NOT NULL,
						title TEXT,
						link TEXT,
						author TEXT,
						summary TEXT,
						content TEXT,
						published_at TIMESTAMP,
						published_text TEXT,
						raw JSONB,
						created_at TIMESTAMP NOT NULL DEFAULT NOW(),
						updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
						UNIQUE(feed_id, guid)
					);
					"""
				)
				cursor.execute(
					"CREATE INDEX IF NOT EXISTS idx_rss_entries_feed_published ON rss_entries (feed_id, published_at DESC, id DESC);"
				)
			self.conn.commit()
		except Exception as exc:
			try:
				self.conn.rollback()
			except Exception:
				pass
			raise LocalStorageError(str(exc)) from exc


class RssFeedService:
	def __init__(self, storage: FeedStorage | None = None):
		self.storage = storage or FeedStorage()

	def close(self) -> None:
		self.storage.close()

	def list_feed_sources(self) -> list[dict[str, Any]]:
		try:
			with self.storage.conn.cursor() as cursor:
				cursor.execute(
					"SELECT id, source_url, title FROM rss_feeds ORDER BY id ASC;"
				)
				return cursor.fetchall()
		except Exception as exc:
			raise FeedServiceError(str(exc)) from exc

	@staticmethod
	def get_configured_source_url() -> str:
		source_url = (Config.RSS_SOURCE_URL or '').strip()
		if not source_url:
			raise FeedServiceError('RSS_SOURCE_URL 未配置')
		return source_url

	def sync_configured_feed(self) -> dict[str, Any]:
		return self.sync_feed(self.get_configured_source_url())

	def sync_feed(self, url: str) -> dict[str, Any]:
		lock_acquired = False
		parsed = parse_feed_url(url)
		if getattr(parsed, 'bozo', False) and not getattr(parsed, 'entries', None) and not getattr(parsed, 'feed', None):
			raise FeedServiceError('RSS 解析失败')

		feed_info = parsed.get('feed') or {}
		entries = list(parsed.get('entries') or [])

		try:
			lock_acquired = self.storage.try_advisory_lock(RSS_SYNC_LOCK_KEY)
			if not lock_acquired:
				raise FeedSyncConflictError('RSS sync already in progress')

			with self.storage.conn.cursor() as cursor:
				cursor.execute(
					"""
					INSERT INTO rss_feeds (source_url, title, description, site_url, etag, modified, last_fetched_at, updated_at)
					VALUES (%s, %s, %s, %s, %s, %s, NOW(), NOW())
					ON CONFLICT (source_url) DO UPDATE SET
						title = EXCLUDED.title,
						description = EXCLUDED.description,
						site_url = EXCLUDED.site_url,
						etag = EXCLUDED.etag,
						modified = EXCLUDED.modified,
						last_fetched_at = NOW(),
						updated_at = NOW()
					RETURNING id, source_url, title, description, site_url, last_fetched_at, created_at, updated_at;
					""",
					(
						url,
						feed_info.get('title'),
						feed_info.get('subtitle') or feed_info.get('description'),
						feed_info.get('link'),
						getattr(parsed, 'etag', None),
						getattr(parsed, 'modified', None),
					),
				)
				feed_row = cursor.fetchone()

				inserted_count = 0
				updated_count = 0
				feed_id = feed_row['id']
				for entry in entries:
					entry_guid = _build_entry_guid(entry, url)
					published_at, published_text = _extract_published_at(entry)
					raw_payload = Json(_safe_json_value(dict(entry)))
					cursor.execute(
						"""
						INSERT INTO rss_entries (
							feed_id, guid, title, link, author, summary, content,
							published_at, published_text, raw, updated_at
						)
						VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
						ON CONFLICT (feed_id, guid) DO UPDATE SET
							title = EXCLUDED.title,
							link = EXCLUDED.link,
							author = EXCLUDED.author,
							summary = EXCLUDED.summary,
							content = EXCLUDED.content,
							published_at = EXCLUDED.published_at,
							published_text = EXCLUDED.published_text,
							raw = EXCLUDED.raw,
							updated_at = NOW()
						RETURNING (xmax = 0) AS inserted;
						""",
						(
							feed_id,
							entry_guid,
							entry.get('title'),
							entry.get('link'),
							entry.get('author'),
							entry.get('summary') or entry.get('description'),
							_extract_entry_content(entry),
							published_at,
							published_text,
							raw_payload,
						),
					)
					result = cursor.fetchone()
					if result and result.get('inserted'):
						inserted_count += 1
					else:
						updated_count += 1

			self.storage.conn.commit()
		except LocalStorageError:
			raise
		except Exception as exc:
			try:
				self.storage.conn.rollback()
			except Exception:
				pass
			raise FeedServiceError(str(exc)) from exc
		finally:
			if lock_acquired:
				try:
					self.storage.advisory_unlock(RSS_SYNC_LOCK_KEY)
				except LocalStorageLockError:
					logger.exception('Failed to release RSS advisory lock')

		return {
			'feed': self._serialize_feed_row(feed_row),
			'entry_count': len(entries),
			'inserted': inserted_count,
			'updated': updated_count,
		}

	def list_feeds(self, *, limit: int = 20, offset: int = 0) -> dict[str, Any]:
		try:
			with self.storage.conn.cursor() as cursor:
				cursor.execute("SELECT COUNT(*) AS count FROM rss_feeds;")
				total = int(cursor.fetchone()['count'])
				cursor.execute(
					"""
					SELECT f.id, f.source_url, f.title, f.description, f.site_url, f.last_fetched_at,
						   f.created_at, f.updated_at, COUNT(e.id) AS entry_count
					FROM rss_feeds f
					LEFT JOIN rss_entries e ON e.feed_id = f.id
					GROUP BY f.id
					ORDER BY f.updated_at DESC, f.id DESC
					LIMIT %s OFFSET %s;
					""",
					(limit, offset),
				)
				rows = cursor.fetchall()
		except Exception as exc:
			raise FeedServiceError(str(exc)) from exc

		return {
			'items': [self._serialize_feed_row(row, include_entry_count=True) for row in rows],
			'total': total,
			'limit': limit,
			'offset': offset,
		}

	def list_entries(self, *, feed_id: int | None = None, limit: int = 20, offset: int = 0) -> dict[str, Any]:
		try:
			with self.storage.conn.cursor() as cursor:
				where_clause = ''
				params: list[Any] = []
				if feed_id is not None:
					where_clause = 'WHERE e.feed_id = %s'
					params.append(feed_id)

				cursor.execute(
					f"""
					SELECT COUNT(*) AS count
					FROM rss_entries e
					{where_clause};
					""",
					tuple(params),
				)
				total = int(cursor.fetchone()['count'])

				cursor.execute(
					f"""
					SELECT e.id, e.feed_id, e.guid, e.title, e.link, e.author, e.summary, e.content,
						   e.published_at, e.published_text, e.created_at, e.updated_at,
						   f.title AS feed_title, f.source_url AS feed_url
					FROM rss_entries e
					JOIN rss_feeds f ON f.id = e.feed_id
					{where_clause}
					ORDER BY e.published_at DESC NULLS LAST, e.id DESC
					LIMIT %s OFFSET %s;
					""",
					tuple([*params, limit, offset]),
				)
				rows = cursor.fetchall()
		except Exception as exc:
			raise FeedServiceError(str(exc)) from exc

		return {
			'items': [self._serialize_entry_row(row) for row in rows],
			'total': total,
			'limit': limit,
			'offset': offset,
		}

	def sync_all_feeds(self) -> dict[str, Any]:
		configured_source_url = self.get_configured_source_url()
		summary = {
			'total_feeds': 1,
			'synced_feeds': 0,
			'failed_feeds': 0,
			'entry_count': 0,
			'inserted': 0,
			'updated': 0,
			'failures': [],
		}

		try:
			result = self.sync_feed(configured_source_url)
		except Exception as exc:
			logger.warning('RSS refresh failed for %s: %s', configured_source_url, exc)
			summary['failed_feeds'] = 1
			summary['failures'].append({'url': configured_source_url, 'error': str(exc)})
			return summary

		summary['synced_feeds'] = 1
		summary['entry_count'] = int(result.get('entry_count') or 0)
		summary['inserted'] = int(result.get('inserted') or 0)
		summary['updated'] = int(result.get('updated') or 0)

		return summary

	@staticmethod
	def _serialize_feed_row(row: dict[str, Any], *, include_entry_count: bool = False) -> dict[str, Any]:
		payload = {
			'id': row.get('id'),
			'source_url': row.get('source_url'),
			'title': row.get('title'),
			'description': row.get('description'),
			'site_url': row.get('site_url'),
			'last_fetched_at': serialize_datetime_for_api(row.get('last_fetched_at')),
			'created_at': serialize_datetime_for_api(row.get('created_at')),
			'updated_at': serialize_datetime_for_api(row.get('updated_at')),
		}
		if include_entry_count:
			payload['entry_count'] = int(row.get('entry_count') or 0)
		return payload

	@staticmethod
	def _serialize_entry_row(row: dict[str, Any]) -> dict[str, Any]:
		return {
			'id': row.get('id'),
			'feed_id': row.get('feed_id'),
			'guid': row.get('guid'),
			'title': row.get('title'),
			'link': row.get('link'),
			'author': row.get('author'),
			'summary': row.get('summary'),
			'content': row.get('content'),
			'published_at': serialize_datetime_for_api(row.get('published_at')),
			'published_text': row.get('published_text'),
			'created_at': serialize_datetime_for_api(row.get('created_at')),
			'updated_at': serialize_datetime_for_api(row.get('updated_at')),
			'feed_title': row.get('feed_title'),
			'feed_url': row.get('feed_url'),
		}


class RssFeedRefreshManager:
	"""Periodically sync all stored RSS feeds."""

	def __init__(self, app=None, *, interval: int = 1800) -> None:
		self.app = app
		self.interval = interval
		self._stop_event = threading.Event()
		self._thread: Optional[threading.Thread] = None

		if app:
			self.init_app(app)
			logger.info('[RssFeedRefreshManager] initialized')

	def init_app(self, app) -> None:
		self.app = app
		self._thread = threading.Thread(target=self._run, daemon=True)
		self._thread.start()

	def stop(self) -> None:
		self._stop_event.set()
		if self._thread and self._thread.is_alive():
			self._thread.join()

	def _run(self) -> None:
		while not self._stop_event.is_set():
			start = time.time()
			try:
				self.refresh_all_feeds()
			except Exception as exc:
				logger.exception('refresh_all_feeds failed: %s', exc)
			elapsed = time.time() - start
			sleep_time = max(0, self.interval - elapsed)
			if sleep_time > 0:
				time.sleep(sleep_time)

	def refresh_all_feeds(self) -> dict[str, Any]:
		service = RssFeedService()
		status_service = JobStatusService()
		try:
			status_service.mark_running(RSS_REFRESH_JOB_NAME, interval_seconds=self.interval)
			result = service.sync_all_feeds()
			status_service.mark_success(
				RSS_REFRESH_JOB_NAME,
				interval_seconds=self.interval,
				result=result,
			)
			logger.info(
				'RSS refresh completed: total=%s synced=%s failed=%s inserted=%s updated=%s',
				result.get('total_feeds', 0),
				result.get('synced_feeds', 0),
				result.get('failed_feeds', 0),
				result.get('inserted', 0),
				result.get('updated', 0),
			)
			return result
		except Exception as exc:
			try:
				status_service.mark_failure(
					RSS_REFRESH_JOB_NAME,
					interval_seconds=self.interval,
					error_message=str(exc),
				)
			except Exception:
				logger.exception('failed to persist RSS refresh failure state')
			raise
		finally:
			status_service.close()
			service.close()
