"""
server_data_service.py
Flask 应用后台 Service 层：Minecraft 服务器状态持久化与定时更新。
"""

from __future__ import annotations

import ast
import json
import logging
import threading
import time
from typing import Any, Iterable, Optional, Tuple

import psycopg2
from psycopg2.extras import Json
from psycopg2.extensions import connection as PgConnection

from app.services.JobStatus import JobStatusService
from app.services.LocalStorage import LocalStorage, LocalStorageError, LocalStorageLockError
from app.utils.serverStatus import query_server_status

logger: logging.Logger = logging.getLogger(__name__)

MC_STATUS_REFRESH_LOCK_KEY = 91002
MC_STATUS_JOB_NAME = 'mc_status_refresh'

# ---------------------------------------------------------------------------#
# Exceptions
# ---------------------------------------------------------------------------#


class ServerStatusError(Exception):
    """ServerStatusManager 内部统一异常基类。"""


# ---------------------------------------------------------------------------#
# Storage Layer
# ---------------------------------------------------------------------------#


class MCLocalStorage(LocalStorage):
    """
    基于 PostgreSQL 的 Minecraft 服务器列表与状态存储。
    """

    # -------------------- 构造 & 资源管理 -------------------- #

    def __init__(
        self,
        host: Optional[str] = None,
        user: Optional[str] = None,
        password: Optional[str] = None,
        db: Optional[str] = None,
        port: Optional[int] = None,
    ) -> None:
        super().__init__(host=host, user=user, password=password, db=db, port=port)
        self._init_schema()

    def _init_schema(self) -> None:
        """初始化数据表结构。"""
        try:
            with self.conn.cursor() as cursor:
                self._ensure_server_status_schema(cursor)
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS servers (
                        id INTEGER PRIMARY KEY,
                        ip TEXT NOT NULL,
                        name TEXT,
                        expose_ip BOOLEAN NOT NULL DEFAULT FALSE
                    );
                    """
                )
                cursor.execute(
                    "ALTER TABLE servers ADD COLUMN IF NOT EXISTS expose_ip BOOLEAN NOT NULL DEFAULT FALSE;"
                )
            self.conn.commit()
        except psycopg2.Error as exc:
            self._safe_rollback()
            raise LocalStorageError(str(exc)) from exc

    def _ensure_server_status_schema(self, cursor) -> None:
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS server_status (
                ip TEXT PRIMARY KEY,
                status JSONB,
                last_update TIMESTAMP,
                name TEXT
            );
            """
        )
        cursor.execute(
            """
            SELECT column_name, data_type
            FROM information_schema.columns
            WHERE table_schema = current_schema()
              AND table_name = 'server_status'
              AND column_name IN ('status', 'status_jsonb');
            """
        )
        column_types = {
            row['column_name']: row['data_type'] for row in cursor.fetchall()
        }

        if column_types.get('status') == 'jsonb':
            if 'status_jsonb' in column_types:
                cursor.execute("ALTER TABLE server_status DROP COLUMN status_jsonb;")
            return

        if 'status' not in column_types and column_types.get('status_jsonb') == 'jsonb':
            cursor.execute("ALTER TABLE server_status RENAME COLUMN status_jsonb TO status;")
            return

        if 'status' not in column_types:
            cursor.execute("ALTER TABLE server_status ADD COLUMN status JSONB;")
            return

        cursor.execute("ALTER TABLE server_status ADD COLUMN IF NOT EXISTS status_jsonb JSONB;")
        cursor.execute("SELECT ip, status FROM server_status;")
        for row in cursor.fetchall():
            normalized_status = normalize_status_payload(row.get('status'))
            cursor.execute(
                "UPDATE server_status SET status_jsonb = %s WHERE ip = %s;",
                (Json(normalized_status) if normalized_status is not None else None, row['ip']),
            )
        cursor.execute("ALTER TABLE server_status DROP COLUMN status;")
        cursor.execute("ALTER TABLE server_status RENAME COLUMN status_jsonb TO status;")

    # -------------------- CRUD: servers -------------------- #

    def query_mc_server(self) -> list[Tuple[int, str, Optional[str], bool]]:
        """
        返回所有服务器记录，格式：(id, ip, name)。
        """
        try:
            with self.conn.cursor() as cursor:
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS servers (
                        id INTEGER PRIMARY KEY,
                        ip TEXT NOT NULL,
                        name TEXT,
                        expose_ip BOOLEAN NOT NULL DEFAULT FALSE
                    );
                    """
                )
                cursor.execute(
                    "ALTER TABLE servers ADD COLUMN IF NOT EXISTS expose_ip BOOLEAN NOT NULL DEFAULT FALSE;"
                )
                self.conn.commit()
                cursor.execute("SELECT id, ip, name, expose_ip FROM servers ORDER BY id")
                return cursor.fetchall()
        except psycopg2.Error as exc:
            self._safe_rollback()
            raise LocalStorageError(str(exc)) from exc

    def get_mc_server_by_id(self, server_id: int) -> Tuple[int, str, Optional[str], bool] | None:
        try:
            with self.conn.cursor() as cursor:
                cursor.execute(
                    "SELECT id, ip, name, expose_ip FROM servers WHERE id = %s",
                    (server_id,),
                )
                return cursor.fetchone() or None
        except psycopg2.Error as exc:
            self._safe_rollback()
            raise LocalStorageError(str(exc)) from exc

    def create_mc_server(
        self,
        *,
        ip: str,
        name: Optional[str] = None,
        expose_ip: Optional[bool] = None,
    ) -> Tuple[int, str, Optional[str], bool]:
        return self.insert_mc_server(ip=ip, name=name, expose_ip=expose_ip)

    def update_mc_server(
        self,
        *,
        server_id: int,
        ip: Optional[str] = None,
        name: Optional[str] = None,
        expose_ip: Optional[bool] = None,
    ) -> Tuple[int, str, Optional[str], bool] | None:
        try:
            with self.conn.cursor() as cursor:
                cursor.execute(
                    "SELECT id, ip, name, expose_ip FROM servers WHERE id = %s",
                    (server_id,),
                )
                existing = cursor.fetchone()
                if not existing:
                    return None

                cursor.execute(
                    """
                    UPDATE servers
                    SET ip = COALESCE(%s, ip),
                        name = COALESCE(%s, name),
                        expose_ip = COALESCE(%s, expose_ip)
                    WHERE id = %s
                    RETURNING id, ip, name, expose_ip;
                    """,
                    (ip, name, expose_ip, server_id),
                )
                row = cursor.fetchone()
                self.conn.commit()
                return row
        except psycopg2.Error as exc:
            self._safe_rollback()
            raise LocalStorageError(str(exc)) from exc

    def insert_mc_server(
        self,
        *,
        id: Optional[int] = None,
        ip: Optional[str] = None,
        name: Optional[str] = None,
        expose_ip: Optional[bool] = None,
    ) -> Tuple[int, str, Optional[str], bool]:
        """
        插入或更新服务器记录。
        当 id 为 None 时视为新增，返回 (id, ip, name)。
        """
        if not ip:
            raise LocalStorageError("参数 ip 不能为空")

        try:
            with self.conn.cursor() as cursor:
                if id is None:
                    cursor.execute("LOCK TABLE servers IN EXCLUSIVE MODE;")
                    cursor.execute(
                        "SELECT COALESCE(MAX(id), -1) + 1 AS next_id FROM servers;"
                    )
                    next_id_row = cursor.fetchone()
                    next_id = next_id_row['next_id'] if next_id_row else 0
                    cursor.execute(
                        "INSERT INTO servers (id, ip, name, expose_ip) VALUES (%s, %s, %s, %s) RETURNING id, ip, name, expose_ip;",
                        (next_id, ip, name, bool(expose_ip) if expose_ip is not None else False),
                    )
                    self.conn.commit()
                    return cursor.fetchone()

                # id 提供的情况：允许对已有记录做部分更新
                # 1) 如果既未提供 ip 也未提供 name，则只返回现有记录（如果存在）
                if ip is None and name is None:
                    cursor.execute(
                        "SELECT id, ip, name, expose_ip FROM servers WHERE id = %s", (id,)
                    )
                    row = cursor.fetchone()
                    if row:
                        return row
                    raise LocalStorageError(
                        f"id {id} 不存在，且未提供 ip/name 用于插入"
                    )

                # 2) 如果 id 不存在且未提供 ip，则无法插入（ip 为 NOT NULL）
                cursor.execute("SELECT 1 FROM servers WHERE id = %s", (id,))
                exists = cursor.fetchone() is not None
                if not exists and ip is None:
                    raise LocalStorageError("id 不存在时必须提供 ip")

                # 使用 UPSERT，利用 COALESCE 保持已有值不被 None 覆盖
                cursor.execute(
                    """
                    INSERT INTO servers (id, ip, name, expose_ip) VALUES (%s, %s, %s, %s)
                    ON CONFLICT (id) DO UPDATE SET
                        ip = COALESCE(EXCLUDED.ip, servers.ip),
                        name = COALESCE(EXCLUDED.name, servers.name),
                        expose_ip = COALESCE(EXCLUDED.expose_ip, servers.expose_ip)
                    RETURNING id, ip, name, expose_ip;
                    """,
                    (id, ip, name, expose_ip),
                )
                self.conn.commit()
                return cursor.fetchone()
        except psycopg2.Error as exc:
            self._safe_rollback()
            raise LocalStorageError(str(exc)) from exc

    def delete_mc_server(self, *, id: int) -> int:
        """
        删除服务器记录，按 id 或 ip。
        返回删除的行数。
        """
        try:
            with self.conn.cursor() as cursor:
                cursor.execute("DELETE FROM servers WHERE id = %s;", (id,))
                self.conn.commit()
                return cursor.rowcount
        except psycopg2.Error as exc:
            self._safe_rollback()
            raise LocalStorageError(str(exc)) from exc

    def sort_mc_servers(self, id_list: Optional[Iterable[int]] = None) -> int:
        """
        重新编号 servers 表：
        - 若传入 id_list，则按该顺序重排；
        - 否则按当前 id 升序重排。
        返回重排后的条目数量。
        """
        if id_list is not None and not isinstance(id_list, (list, tuple)):
            raise LocalStorageError("id_list 必须为非空列表或 None")

        try:
            with self.conn.cursor() as cursor:
                # 读取待排序数据
                if id_list is None:
                    cursor.execute("SELECT id, ip, name, expose_ip FROM servers ORDER BY id")
                    rows = cursor.fetchall()
                    new_items = [(row["ip"], row["name"], row.get("expose_ip", False)) for row in rows]
                else:
                    cursor.execute(
                        "SELECT id, ip, name, expose_ip FROM servers WHERE id = ANY(%s) ORDER BY id",
                        (list(id_list),),
                    )
                    rows = cursor.fetchall()
                    id_map = {row["id"]: (row["ip"], row["name"], row.get("expose_ip", False)) for row in rows}
                    new_items = []
                    for old_id in id_list:
                        if old_id not in id_map:
                            raise LocalStorageError(f"id {old_id} 不存在于表中")
                        new_items.append(id_map[old_id])

                # 清空并重写
                cursor.execute("DELETE FROM servers;")
                for new_id, (ip, name, expose_ip) in enumerate(new_items):
                    cursor.execute(
                        "INSERT INTO servers (id, ip, name, expose_ip) VALUES (%s, %s, %s, %s);",
                        (new_id, ip, name, expose_ip),
                    )
                self.conn.commit()
                return len(new_items)
        except psycopg2.Error as exc:
            self._safe_rollback()
            raise LocalStorageError(str(exc)) from exc

    # -------------------- 工具方法 -------------------- #

    def _safe_rollback(self) -> None:
        """安全地回滚事务，忽略关闭连接时的报错。"""
        try:
            self.conn.rollback()
        except Exception:
            pass


def normalize_status_payload(raw_status: Any) -> dict[str, Any] | None:
    """Normalize stored status payloads from legacy Python repr or JSON text into a dict."""
    if raw_status is None:
        return None

    if isinstance(raw_status, dict):
        return raw_status

    if isinstance(raw_status, str):
        text = raw_status.strip()
        if not text:
            return None

        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            try:
                parsed = ast.literal_eval(text)
            except (ValueError, SyntaxError):
                return None

        if isinstance(parsed, dict):
            return parsed

    return None


def extract_plain_text_from_motd(value: Any) -> str:
    if value is None:
        return ''
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return ''.join(extract_plain_text_from_motd(item) for item in value)
    if isinstance(value, dict):
        text_parts = []
        if 'text' in value:
            text_parts.append(extract_plain_text_from_motd(value.get('text')))
        if 'extra' in value:
            text_parts.append(extract_plain_text_from_motd(value.get('extra')))
        return ''.join(text_parts)
    return str(value)


# ---------------------------------------------------------------------------#
# Service Layer
# ---------------------------------------------------------------------------#


class ServerStatusManager:
    """
    定时更新所有 Minecraft 服务器状态的守护服务。
    """

    def __init__(self, app=None, *, interval: int = 120) -> None:
        self.app = app
        self.interval: int = interval  # 秒
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

        if app:
            self.init_app(app)
            logger.info("[ServerStatusManager] initialized")

    # -------------------- 生命周期 -------------------- #

    def init_app(self, app) -> None:
        """Flask 工厂模式支持。"""
        self.app = app
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """停止后台线程。"""
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join()

    def _run(self) -> None:
        """后台循环：按 interval 周期性更新状态。"""
        while not self._stop_event.is_set():
            start = time.time()
            try:
                self.update_all_status()
            except Exception as exc:
                logger.exception("update_all_status 报错：%s", exc)
            elapsed = time.time() - start
            sleep_time = max(0, self.interval - elapsed)
            if sleep_time > 0:
                time.sleep(sleep_time)

    # -------------------- 业务逻辑 -------------------- #

    def update_all_status(self) -> None:
        """
        批量更新所有唯一 ip 的状态。
        逻辑：拉取 -> 清理 -> 查询 -> 写入。
        """
        storage = MCLocalStorage()
        status_service = JobStatusService()
        lock_acquired = False
        try:
            status_service.mark_running(MC_STATUS_JOB_NAME, interval_seconds=self.interval)
            lock_acquired = storage.try_advisory_lock(MC_STATUS_REFRESH_LOCK_KEY)
            if not lock_acquired:
                logger.info("skip update_all_status because another refresh is in progress")
                status_service.mark_success(
                    MC_STATUS_JOB_NAME,
                    interval_seconds=self.interval,
                    result={'skipped': True, 'reason': 'lock-held'},
                )
                return

            # 1. 需要监控的 ip 列表
            rows = storage.query_mc_server()
            ip_map: dict[str, dict[str, Any]] = {
                row["ip"]: {
                    'name': row.get('name'),
                    'expose_ip': bool(row.get('expose_ip', False)),
                }
                for row in rows
            }
            ip_set = set(ip_map.keys())

            # 2. 初始化 server_status 表
            with storage.conn.cursor() as cur:
                storage._ensure_server_status_schema(cur)
                cur.execute("SELECT ip, status, name FROM server_status")
                status_map: dict[str, Tuple[Any, Optional[str]]] = {
                    r["ip"]: (r["status"], r.get("name")) for r in cur.fetchall()
                }

            # 3. 移除已不在监控列表的 ip
            to_delete = set(status_map) - ip_set
            if to_delete:
                with storage.conn.cursor() as cur:
                    cur.execute(
                        "DELETE FROM server_status WHERE ip = ANY(%s)",
                        (list(to_delete),),
                    )
                    storage.conn.commit()

            # 4. 查询 & 更新
            for ip in ip_set:
                server_meta = ip_map.get(ip) or {}
                server_name = server_meta.get('name')
                try:
                    host, port = self._parse_host_port(ip)
                    status = (
                        query_server_status(host, port=port)
                        if port
                        else query_server_status(host)
                    )
                    if not status or status.get("status") in {"offline", "unknown"}:
                        continue
                except Exception as exc:
                    logger.exception("查询 %s 状态失败：%s", ip, exc)
                    continue

                with storage.conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO server_status (ip, status, last_update, name)
                        VALUES (%s, %s, NOW(), %s)
                        ON CONFLICT (ip) DO UPDATE
                            SET status = EXCLUDED.status,
                                last_update = NOW(),
                                name = EXCLUDED.name;
                        """,
                        (ip, Json(status), server_name),
                    )
                    storage.conn.commit()
            status_service.mark_success(
                MC_STATUS_JOB_NAME,
                interval_seconds=self.interval,
                result={
                    'server_count': len(ip_set),
                    'updated_servers': len(ip_set),
                    'skipped': False,
                },
            )
        except Exception as exc:
            try:
                status_service.mark_failure(
                    MC_STATUS_JOB_NAME,
                    interval_seconds=self.interval,
                    error_message=str(exc),
                )
            except Exception:
                logger.exception("failed to persist MC status job failure state")
            logger.exception("update_all_status 全局异常：%s", exc)
            raise ServerStatusError(str(exc)) from exc
        finally:
            if lock_acquired:
                try:
                    storage.advisory_unlock(MC_STATUS_REFRESH_LOCK_KEY)
                except LocalStorageLockError:
                    logger.exception("failed to release MC status advisory lock")
                    status_service.close()
            storage.close()

    # -------------------- 查询接口 -------------------- #

    def get_status(self, ip: str):
        """
        获取某个 ip 的最新状态记录。
        返回 dict-like 行或 None。
        """
        storage = MCLocalStorage()
        try:
            with storage.conn.cursor() as cursor:
                cursor.execute(
                    "SELECT status, last_update, name FROM server_status WHERE ip = %s;",
                    (ip,),
                )
                return cursor.fetchone() or None
        except Exception as exc:
            logger.exception("查询 %s 状态时发生异常：%s", ip, exc)
            raise ServerStatusError(str(exc)) from exc
        finally:
            storage.close()

    # -------------------- 工具 -------------------- #

    @staticmethod
    def _parse_host_port(ip_str: str) -> Tuple[str, Optional[int]]:
        """
        解析形如 host:port、[ipv6]:port 的地址字符串。
        返回 (host, port_or_None)。
        """
        ip = ip_str.strip()
        if ip.startswith("["):
            try:
                end = ip.index("]")
                host = ip[1:end]
                rest = ip[end + 1 :]
                if rest.startswith(":") and rest[1:]:
                    try:
                        return host, int(rest[1:])
                    except ValueError:
                        return host, None
                return host, None
            except ValueError:
                return ip, None

        if ip.count(":") == 1:
            host, port_s = ip.split(":", 1)
            try:
                return host, int(port_s)
            except ValueError:
                return ip, None

        # IPv6 无端口或普通 IPv4
        return ip, None
