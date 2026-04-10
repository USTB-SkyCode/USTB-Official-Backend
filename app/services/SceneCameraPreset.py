from __future__ import annotations

import logging
from typing import Any

import psycopg2
from psycopg2.extras import Json

from app.services.LocalStorage import LocalStorage, LocalStorageError
from app.utils.timezone import serialize_datetime_for_api

logger = logging.getLogger(__name__)

SCENE_CAMERA_PRESET_KEYS = (
    'login',
    'explore',
    'schedule',
    'history',
    'latest',
    'servers',
    'self',
)

SCENE_CAMERA_PERSPECTIVE_MODES = (
    'first-person',
    'spectator',
    'third-person-back',
    'third-person-front',
)


class SceneCameraPresetStorage(LocalStorage):
    def __init__(self, host=None, user=None, password=None, db=None, port=None):
        super().__init__(host=host, user=user, password=password, db=db, port=port)
        self._init_schema()

    def _init_schema(self) -> None:
        try:
            with self.conn.cursor() as cursor:
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS scene_camera_presets (
                        preset_key TEXT PRIMARY KEY,
                        position JSONB NOT NULL,
                        look_target JSONB NOT NULL,
                        perspective_mode TEXT,
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    );
                    """
                )
            self.conn.commit()
        except psycopg2.Error as exc:
            self._safe_rollback()
            raise LocalStorageError(str(exc)) from exc

    def query_overrides(self) -> list[dict[str, Any]]:
        try:
            with self.conn.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT preset_key, position, look_target, perspective_mode, updated_at
                    FROM scene_camera_presets
                    ORDER BY preset_key ASC;
                    """
                )
                return cursor.fetchall()
        except psycopg2.Error as exc:
            self._safe_rollback()
            raise LocalStorageError(str(exc)) from exc

    def upsert_override(
        self,
        *,
        preset_key: str,
        position: list[float],
        look_target: list[float],
        perspective_mode: str | None,
    ) -> dict[str, Any]:
        try:
            with self.conn.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO scene_camera_presets (
                        preset_key,
                        position,
                        look_target,
                        perspective_mode,
                        updated_at
                    )
                    VALUES (%s, %s, %s, %s, NOW())
                    ON CONFLICT (preset_key)
                    DO UPDATE SET
                        position = EXCLUDED.position,
                        look_target = EXCLUDED.look_target,
                        perspective_mode = EXCLUDED.perspective_mode,
                        updated_at = NOW()
                    RETURNING preset_key, position, look_target, perspective_mode, updated_at;
                    """,
                    (preset_key, Json(position), Json(look_target), perspective_mode),
                )
                row = cursor.fetchone()
            self.conn.commit()
            return row
        except psycopg2.Error as exc:
            self._safe_rollback()
            raise LocalStorageError(str(exc)) from exc

    def delete_override(self, preset_key: str) -> bool:
        try:
            with self.conn.cursor() as cursor:
                cursor.execute(
                    "DELETE FROM scene_camera_presets WHERE preset_key = %s RETURNING preset_key;",
                    (preset_key,),
                )
                row = cursor.fetchone()
            self.conn.commit()
            return row is not None
        except psycopg2.Error as exc:
            self._safe_rollback()
            raise LocalStorageError(str(exc)) from exc


def serialize_scene_camera_preset_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        'presetKey': row.get('preset_key'),
        'position': list(row.get('position') or []),
        'lookTarget': list(row.get('look_target') or []),
        'perspectiveMode': row.get('perspective_mode'),
        'updatedAt': serialize_datetime_for_api(row.get('updated_at')),
    }


def load_scene_camera_preset_override_map() -> dict[str, dict[str, Any]]:
    storage = None
    try:
        storage = SceneCameraPresetStorage()
        rows = storage.query_overrides()
        return {
            row.get('preset_key'): serialize_scene_camera_preset_row(row)
            for row in rows
            if row.get('preset_key')
        }
    except LocalStorageError as exc:
        logger.warning('Failed to load scene camera preset overrides: %s', exc)
        return {}
    finally:
        if storage:
            storage.close()