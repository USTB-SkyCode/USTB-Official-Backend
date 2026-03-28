"""
参考来源:https://github.com/mcmotd/mcmotdapi/tree/main/backend
"""

from __future__ import annotations

import json
import re
import socket
import struct
import sys
import threading
import time
import traceback
from datetime import datetime
from typing import Any, Dict, Optional, Tuple

from app.utils.timezone import get_app_timezone


# ---------- 配置 ----------
CFG = {
    "java_default_port": 25565,
    "bedrock_default_port": 19132,
    "timeout_ms": 2000,
}

# ---------- 日志 ----------
class Log:
    LV = {"ERROR": 0, "WARN": 1, "INFO": 2, "DEBUG": 3}
    COL = {"r": "\x1b[31m", "y": "\x1b[33m", "c": "\x1b[36m", "g": "\x1b[90m", "0": "\x1b[0m"}

    @staticmethod
    def _log(lvl: int, col: str, *args: Any) -> None:
        if lvl > Log.LV["DEBUG"]:
            return
        ts = datetime.now(get_app_timezone()).strftime("%H:%M:%S")
        print(f"{Log.COL['g']}[{ts}]{Log.COL['0']} {col}{list(Log.LV)[lvl]}{Log.COL['0']}", *args)

    error = lambda *a: Log._log(0, Log.COL["r"], *a)
    warn  = lambda *a: Log._log(1, Log.COL["y"], *a)
    info  = lambda *a: Log._log(2, Log.COL["c"], *a)
    debug = lambda *a: Log._log(3, Log.COL["g"], *a)

# ---------- 二进制工具 ----------
def _varint(b: bytes, off: int = 0) -> Tuple[int, int]:
    res = 0
    for i in range(5):
        if off + i >= len(b):
            raise IndexError
        byte = b[off + i]
        res |= (byte & 0x7F) << (7 * i)
        if not byte & 0x80:
            return res if res < (1 << 31) else res - (1 << 32), i + 1
    raise ValueError("VarInt too big")

def _pack_varint(v: int) -> bytes:
    v &= 0xFFFFFFFF
    out = bytearray()
    while True:
        b = v & 0x7F
        v >>= 7
        out.append(b | (0x80 if v else 0))
        if not v:
            return bytes(out)

def _pack_str(s: str) -> bytes:
    b = s.encode("utf-8")
    return _pack_varint(len(b)) + b

def _pkt(pid: int, data: bytes) -> bytes:
    body = _pack_varint(pid) + data
    return _pack_varint(len(body)) + body


def _strip_format_codes(value: str) -> str:
    return re.sub(r"§[0-9a-fk-or]", "", value, flags=re.IGNORECASE)


def _extract_plain_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return _strip_format_codes(value)
    if isinstance(value, list):
        return "".join(_extract_plain_text(item) for item in value)
    if isinstance(value, dict):
        parts: list[str] = []
        if 'text' in value:
            parts.append(_extract_plain_text(value.get('text')))
        if 'extra' in value:
            parts.append(_extract_plain_text(value.get('extra')))
        return ''.join(parts)
    return _strip_format_codes(str(value))

# ---------- Java ----------
def ping_java(host: str, port: int, timeout: int) -> Dict[str, Any]:
    start = time.time()
    with socket.create_connection((host, port), timeout=timeout / 1000) as s:
        t_conn = int((time.time() - start) * 1000)

        # Handshake
        hs = _pack_varint(-1) + _pack_str(host) + struct.pack(">H", port) + _pack_varint(1)
        s.sendall(_pkt(0x00, hs))
        t_hs = int((time.time() - start) * 1000)

        # Request
        s.sendall(_pkt(0x00, b""))
        t_req = int((time.time() - start) * 1000)

        # Read response
        raw = b""
        while True:
            raw += s.recv(4096)
            try:
                ln, o1 = _varint(raw, 0)
                if len(raw) - o1 >= ln:
                    pid, o2 = _varint(raw, o1)
                    sln, o3 = _varint(raw, o1 + o2)
                    if len(raw) >= o1 + o2 + o3 + sln:
                        data = json.loads(raw[o1 + o2 + o3 : o1 + o2 + o3 + sln])
                        data["_timings"] = {
                            "connect_ms": t_conn,
                            "send_hs_ms": t_hs - t_conn,
                            "send_req_ms": t_req - t_conn,
                            "recv_ms": int((time.time() - start) * 1000) - t_req,
                            "total_ms": int((time.time() - start) * 1000),
                        }
                        return data
            except (IndexError, ValueError):
                continue

# ---------- Bedrock ----------
def ping_bedrock(host: str, port: int, timeout: int) -> Dict[str, Any]:
    start = time.time()
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.settimeout(timeout / 1000)

        MAGIC = bytes.fromhex("00ffff00fefefefefdfdfdfd12345678")
        ping_id = int(time.time() * 1000) & 0xFFFFFFFFFFFFFFFF
        buf = (
            b"\x01"
            + struct.pack(">Q", ping_id)
            + MAGIC
            + bytes.fromhex("1234567800")
            + struct.pack(">Q", 0)
        )

        s.sendto(buf, (host, port))
        data, _ = s.recvfrom(2048)
        if not data or data[0] not in (0x1C, 0x1D):
            raise ValueError("Bad bedrock response")

        off = 9
        server_id = struct.unpack_from(">Q", data, off)[0]
        off += 24  # skip ping_id + server_id + 16 reserved
        name_len = struct.unpack_from(">H", data, off)[0]
        off += 2
        name = data[off : off + name_len].decode("utf-8", errors="ignore")

        parts = (name + ";" * 9).split(";")[:9]
        return {
            "advertise": name,
            "name": parts[1] or name,
            "cleanName": re.sub(r"§[0-9a-fk-or]", "", parts[1] or name),
            "version": parts[3] or None,
            "currentPlayers": parts[4] or None,
            "maxPlayers": parts[5] or None,
            "connected": True,
            "_timings": {
                "send_ms": int((time.time() - start) * 1000),
                "recv_ms": int((time.time() - start) * 1000),
                "total_ms": int((time.time() - start) * 1000),
            },
        }

# ---------- SRV ----------
def resolve_srv(host: str) -> Optional[Tuple[str, int]]:
    try:
        import dns.resolver

        for r in dns.resolver.resolve(f"_minecraft._tcp.{host}", "SRV"):
            return str(r.target).rstrip("."), int(r.port)
    except Exception as e:
        Log.debug("SRV resolve failed:", e)
    return None

# ---------- 主查询 ----------
def query_server_status(
    ip: str,
    port: Optional[int] = None,
    icon_url: Optional[str] = None,
    server_type: str = "auto",
    is_srv: bool = False,
    timeout_ms: Optional[int] = None,
) -> Dict[str, Any]:
    timeout = timeout_ms or CFG["timeout_ms"]
    start = int(time.time() * 1000)

    # 目标
    bed_target = {"host": ip, "port": port or CFG["bedrock_default_port"]}
    java_target = {"host": ip, "port": port or CFG["java_default_port"]}

    # DNS/SRV (skip SRV if a port was explicitly provided)
    dns_time = None
    if port is None and (is_srv or server_type == "auto"):
        t0 = int(time.time() * 1000)
        srv = resolve_srv(ip)
        dns_time = int(time.time() * 1000) - t0
        if srv:
            java_target["host"], java_target["port"] = srv
            Log.info("SRV resolved ->", f"{java_target['host']}:{java_target['port']}")
            if is_srv:
                server_type = "je"

    # 并发
    results: Dict[str, Any] = {}
    done = threading.Event()

    errors: list[Dict[str, Any]] = []

    def run_concurrent(name: str, target: Dict[str, Any], func):
        if server_type not in (name.lower(), "auto"):
            return
        try:
            data = func(target["host"], target["port"], timeout)
            if done.is_set():
                # another thread already succeeded, ignore
                return
            results.update(type=name, data=data, host=target["host"], port=target["port"])
            Log.info(f"[{name}] OK {target['host']}:{target['port']}")
            done.set()
        except Exception as e:
            # If another thread already succeeded, suppress this error
            if done.is_set():
                return
            tb = traceback.format_exc()
            errors.append({"name": name, "host": target.get("host"), "port": target.get("port"), "error": str(e), "trace": tb})
            Log.debug(f"[{name}] fail {target['host']}:{target['port']} ->", e)

    threads = [
        threading.Thread(target=run_concurrent, args=("Java", java_target, ping_java)),
        threading.Thread(target=run_concurrent, args=("Bedrock", bed_target, ping_bedrock)),
    ]
    [t.start() for t in threads]

    done.wait(timeout / 1000 + 0.1)
    [t.join(0.01) for t in threads if t.is_alive()]

    # 无结果
    if not results:
        return {
            "status": "offline",
            "error": "all queries failed",
            "type": "unknown",
            "host": f"{ip}:{port or ''}",
            "delay": int(time.time() * 1000) - start,
        }

    # 成功包装
    if results["type"] == "Java":
        data = results["data"]
        timings = data.pop("_timings", None)
        return {
            "type": "Java",
            "status": "online",
            "host": f"{results['host']}:{results['port']}",
            "motd": data.get("description"),
            "pureMotd": _extract_plain_text(data.get("description")),
            "version": data.get("version", {}).get("name"),
            "protocol": data.get("version", {}).get("protocol"),
            "players": {
                "online": data.get("players", {}).get("online"),
                "max": data.get("players", {}).get("max"),
                "sample": ", ".join(
                    p["name"] for p in data.get("players", {}).get("sample", []) if p.get("name")
                )
                or "无",
            },
            "icon": data.get("favicon") or icon_url,
            "delay": int(time.time() * 1000) - start,
            "timings": {"dns_ms": dns_time, "protocol": timings},
        }
    else:  # Bedrock
        data = results["data"]
        parts = (data.get("advertise", "") + ";" * 9).split(";")[:9]
        return {
            "type": "Bedrock",
            "status": "online",
            "host": f"{results['host']}:{results['port']}",
            "motd": data.get("name"),
            "pureMotd": _extract_plain_text(data.get("cleanName") or data.get("name")),
            "version": data.get("version"),
            "players": {"online": data.get("currentPlayers"), "max": data.get("maxPlayers")},
            "gamemode": parts[8] or None,
            "icon": icon_url,
            "delay": int(time.time() * 1000) - start,
            "timings": {"dns_ms": dns_time, "protocol": data.pop("_timings", None)},
        }

# ---------- CLI ----------
if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python serverStatus.py host [port] [je|be|auto]")
        sys.exit(1)

    host = sys.argv[1]
    port = int(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[2].isdigit() else None
    stype = sys.argv[3] if len(sys.argv) > 3 else "auto"

    res = query_server_status(host, port, server_type=stype)
    print(json.dumps(res, ensure_ascii=False, indent=2))