"""Admin API router — unified panel endpoints for AWG + VLESS + Bot data."""
import asyncio
import json
import logging
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Query, Request, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, HTMLResponse, FileResponse
from sse_starlette.sse import EventSourceResponse

# Add project root for imports
sys.path.insert(0, "/home/alvik/vpn-service")

from awg_api import db as awg_db
from admin import db as admin_db
from awg_api.config import (
    MYSQL_HOST, MYSQL_PORT, MYSQL_USER, MYSQL_PASSWORD, MYSQL_DATABASE,
)

logger = logging.getLogger("admin")

SESSION_MAX_AGE = 604800  # 7 days


def _require_admin_session(request: Request):
    """Verify connect.sid session cookie — reuses AWG API session store."""
    from awg_api.main import _sessions
    token = request.cookies.get("connect.sid")
    if not token or token not in _sessions:
        raise HTTPException(status_code=401, detail="Unauthorized")
    from datetime import timezone
    created = _sessions[token]
    now = datetime.now(timezone.utc).timestamp()
    if now - created > SESSION_MAX_AGE:
        del _sessions[token]
        raise HTTPException(status_code=401, detail="Session expired")
    # Sliding window: refresh session on each request
    _sessions[token] = now


router = APIRouter(prefix="/api/admin", dependencies=[Depends(_require_admin_session)])

# ── WebSocket connections ──────────────────────────────────────────────────────
_admin_ws_connections: set[WebSocket] = set()


async def _ws_authenticate(websocket: WebSocket) -> bool:
    """Verify session cookie for WebSocket handshake."""
    from awg_api.main import _sessions
    cookie = websocket.cookies.get("connect.sid")
    if not cookie or cookie not in _sessions:
        return False
    created = _sessions[cookie]
    now = datetime.now(timezone.utc).timestamp()
    if now - created > SESSION_MAX_AGE:
        del _sessions[cookie]
        return False
    _sessions[cookie] = now
    return True


async def _broadcast_ws(msg: dict):
    """Send JSON message to all connected WebSocket clients."""
    if not _admin_ws_connections:
        return
    data = json.dumps(msg, default=_serialize)
    disconnected = set()
    for ws in _admin_ws_connections:
        try:
            await ws.send_text(data)
        except Exception:
            disconnected.add(ws)
    for ws in disconnected:
        _admin_ws_connections.discard(ws)


# XUI client (lazy init)
_xui = None

# Speed tracking: {(name, type): {"bytes": int, "ts": float, "speed": float}}
_prev_traffic: dict[tuple[str, str], dict] = {}


def _get_xui():
    global _xui
    if _xui is None:
        from bot_xui.utils import XUIClient
        from config import XUI_HOST, XUI_USERNAME, XUI_PASSWORD
        _xui = XUIClient(XUI_HOST, XUI_USERNAME, XUI_PASSWORD)
    return _xui


def _ping_telegram_dc() -> Optional[int]:
    """Ping a Telegram DC server, return RTT in ms or None."""
    import subprocess, re
    try:
        result = subprocess.run(
            ["ping", "-c", "1", "-W", "2", "telegram.org"],
            capture_output=True, text=True, timeout=5
        )
        m = re.search(r"time=(\d+\.?\d*)", result.stdout)
        if m:
            return round(float(m.group(1)))
    except Exception:
        pass
    return None


def _fmt_bytes(b: int) -> str:
    if not b:
        return "0 B"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if b < 1024:
            return f"{b:.1f} {unit}"
        b /= 1024
    return f"{b:.1f} PB"


def _serialize(obj):
    """Make datetime/Decimal/bytes JSON-serializable."""
    from decimal import Decimal
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, Decimal):
        return int(obj) if obj == int(obj) else float(obj)
    if isinstance(obj, bytes):
        return obj.decode("utf-8", errors="replace")
    return str(obj)


def _clean(obj):
    from decimal import Decimal
    if isinstance(obj, list):
        return [_clean(item) for item in obj]
    if isinstance(obj, dict):
        return {k: _clean(v) for k, v in obj.items()}
    if isinstance(obj, (datetime, Decimal, bytes)):
        return _serialize(obj)
    return obj


def _calc_speed(name: str, proto: str, total_bytes: int) -> float:
    """Calculate speed in bytes/sec from delta between snapshots."""
    key = (name, proto)
    now = time.time()
    prev = _prev_traffic.get(key)
    speed = 0.0
    if prev and now - prev["ts"] > 1:
        delta_bytes = total_bytes - prev["bytes"]
        delta_time = now - prev["ts"]
        if delta_bytes > 0:
            speed = delta_bytes / delta_time
    _prev_traffic[key] = {"bytes": total_bytes, "ts": now, "speed": speed}
    return speed


def _speed_mbps(speed_bps: float) -> float:
    """Convert bytes/sec to Mbit/s, rounded to 1 decimal."""
    return round(speed_bps * 8 / 1_000_000, 1)


def _resolve_names_to_users(names: list[str]) -> dict[str, dict]:
    """Resolve client names to user info via vpn_keys table.
    Returns {client_name: {tg_id, user_id, first_name, web_token, expires, is_test}}.
    For hysteria _h entries, also tries base vless name as fallback."""
    if not names:
        return {}
    conn = awg_db._get_conn()
    cur = conn.cursor(dictionary=True)
    placeholders = ",".join(["%s"] * len(names))
    cur.execute(f"""
        SELECT k.client_name, k.tg_id, k.user_id, k.expires_at AS key_expires_at,
               COALESCE(NULLIF(u.first_name,''), u.old_first_name) AS first_name,
               u.web_token, u.subscription_until
        FROM vpn_keys k
        LEFT JOIN users u ON (k.tg_id != 0 AND k.tg_id = u.tg_id) OR (k.user_id IS NOT NULL AND k.user_id = u.id)
        WHERE k.client_name IN ({placeholders})
    """, tuple(names))
    rows = cur.fetchall()
    cur.close()
    conn.close()
    result = {}
    for r in rows:
        sub = r.get("subscription_until")
        key_exp = r.get("key_expires_at")
        expires = None
        is_test = False
        if sub and hasattr(sub, "strftime"):
            expires = sub.strftime("%Y-%m-%d")
        elif sub:
            expires = str(sub)
        elif key_exp and hasattr(key_exp, "strftime"):
            expires = key_exp.strftime("%Y-%m-%d")
            is_test = True
        elif key_exp:
            expires = str(key_exp)
            is_test = True
        result[r["client_name"]] = {
            "tg_id": r.get("tg_id"),
            "user_id": r.get("user_id"),
            "first_name": r.get("first_name") or "",
            "web_token": r.get("web_token") or "",
            "expires": expires,
            "is_test": is_test,
        }
    # For hysteria _h entries without user info, fall back to base vless name
    missing_bases = set()
    for name in names:
        if name.endswith("_h"):
            base = name[:-2]
            entry = result.get(name)
            base_entry = result.get(base)
            if entry and base_entry:
                if not entry.get("tg_id") or entry["tg_id"] == 0:
                    entry["tg_id"] = base_entry.get("tg_id")
                if not entry.get("user_id"):
                    entry["user_id"] = base_entry.get("user_id")
                if not entry.get("first_name"):
                    entry["first_name"] = base_entry.get("first_name", "")
                if not entry.get("web_token"):
                    entry["web_token"] = base_entry.get("web_token", "")
                if not entry.get("expires"):
                    entry["expires"] = base_entry.get("expires")
                if not entry.get("is_test"):
                    entry["is_test"] = base_entry.get("is_test", False)
            elif not entry:
                if base_entry:
                    result[name] = dict(base_entry)
                else:
                    missing_bases.add(base)
    # Batch-lookup missing base names from DB
    if missing_bases:
        base_results = _resolve_names_to_users(list(missing_bases))
        for name in names:
            if name.endswith("_h") and name not in result:
                base = name[:-2]
                base_entry = base_results.get(base)
                if base_entry:
                    result[name] = dict(base_entry)
    return result


def _get_online_users() -> tuple[list[dict], set]:
    """Parse xray access log + awg handshakes to find who's online now.
    Returns merged list of users (all protocols united by tg_id/user_id) and identity set."""
    # Collect raw protocol entries: list of {name, type, ip_count, last_seen, speed_mbps}
    raw_entries = []
    vless_ips: dict[str, set[str]] = {}   # email -> set of IPs
    vless_ts: dict[str, str] = {}         # email -> latest timestamp

    # VLESS: parse access.log for activity in last 5 minutes
    access_log = "/var/log/x-ui/access.log"
    try:
        cutoff = time.time() - 300  # 5 min ago
        result = subprocess.run(
            ["tail", "-500", access_log], capture_output=True, text=True, timeout=5
        )
        for line in result.stdout.strip().split("\n"):
            if "email:" not in line or "127.0.0.1" in line.split("from ")[1][:15] if "from " in line else True:
                continue
            ts_match = re.match(r"(\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2})", line)
            if not ts_match:
                continue
            try:
                ts = datetime.strptime(ts_match.group(1), "%Y/%m/%d %H:%M:%S").replace(tzinfo=timezone.utc)
                if ts.timestamp() < cutoff:
                    continue
            except Exception:
                continue

            ip_match = re.search(r"from (?:tcp:)?(\d+\.\d+\.\d+\.\d+)", line)
            ip = ip_match.group(1) if ip_match else "?"

            email_match = re.search(r"email: (.+)$", line)
            if not email_match:
                continue
            email = email_match.group(1).strip()
            vless_ips.setdefault(email, set()).add(ip)
            vless_ts[email] = ts.strftime("%H:%M:%S")

        for email, ips in vless_ips.items():
            is_hysteria = email.endswith("_h")
            raw_entries.append({
                "name": email,
                "ip_count": len(ips),
                "type": "hysteria" if is_hysteria else "vless",
                "last_seen": vless_ts[email],
            })
    except Exception as e:
        logger.warning(f"Access log parse error: {e}")

    # VLESS/Hysteria: get per-client traffic from x-ui for speed calc
    try:
        xui = _get_xui()
        inbounds = xui.get_inbounds()
        traffic: dict[str, int] = {}  # email -> total bytes
        for ib in inbounds:
            for cs in ib.get("clientStats", []):
                email = cs.get("email", "")
                traffic[email] = traffic.get(email, 0) + cs.get("up", 0) + cs.get("down", 0)
        for e in raw_entries:
            if e["name"] in traffic:
                proto = "hysteria" if e["name"].endswith("_h") else "vless"
                speed = _calc_speed(e["name"], proto, traffic[e["name"]])
                e["speed_mbps"] = _speed_mbps(speed)
    except Exception as e:
        logger.warning(f"Traffic fetch error: {e}")

    # AWG: check last handshake from awg show dump
    try:
        awg_clients = awg_db.list_clients()
        pub_to_name = {c["public_key"]: c["name"] for c in awg_clients}
        result = subprocess.run(
            ["awg", "show", "awg0", "dump"], capture_output=True, text=True, timeout=5
        )
        for line in result.stdout.strip().split("\n")[1:]:
            parts = line.split("\t")
            if len(parts) < 7:
                continue
            pub_key = parts[0]
            last_handshake = int(parts[4]) if parts[4] != "0" else 0
            if last_handshake > 0 and (time.time() - last_handshake) < 300:
                name = pub_to_name.get(pub_key, pub_key[:12])
                rx = int(parts[5]) if parts[5].isdigit() else 0
                tx = int(parts[6]) if parts[6].isdigit() else 0
                speed = _calc_speed(name, "awg", rx + tx)
                raw_entries.append({
                    "name": name,
                    "ip_count": 1,
                    "type": "awg",
                    "last_seen": datetime.fromtimestamp(last_handshake, tz=timezone.utc).strftime("%H:%M:%S"),
                    "speed_mbps": _speed_mbps(speed),
                })
    except Exception as e:
        logger.warning(f"AWG online parse error: {e}")

    # Resolve all client names to user info
    all_names = [e["name"] for e in raw_entries]
    user_info = _resolve_names_to_users(all_names)

    # Merge entries by user identity (tg_id or user_id)
    # Key: (tg_id, user_id) — tg_id takes priority
    merged: dict[tuple, dict] = {}
    online_identities = set()

    for e in raw_entries:
        info = user_info.get(e["name"], {})
        tg_id = info.get("tg_id")
        user_id = info.get("user_id")
        # Build identity key: prefer tg_id, fall back to user_id, last resort use name
        if tg_id and tg_id != 0:
            key = ("tg", tg_id)
        elif user_id:
            key = ("uid", user_id)
        else:
            key = ("name", e["name"])

        if key not in merged:
            merged[key] = {
                "tg_id": tg_id,
                "user_id": user_id,
                "first_name": info.get("first_name", ""),
                "web_token": info.get("web_token", ""),
                "expires": info.get("expires"),
                "is_test": info.get("is_test", False),
                "protocols": [],
                "names": [],
                "ip_count": 0,
                "speed_mbps": 0,
                "last_seen": "",
            }

        entry = merged[key]
        proto = e["type"]
        if proto not in entry["protocols"]:
            entry["protocols"].append(proto)
        entry["names"].append(e["name"])
        entry["ip_count"] = max(entry["ip_count"], e.get("ip_count", 0))
        entry["speed_mbps"] = max(entry.get("speed_mbps", 0), e.get("speed_mbps", 0))
        if e.get("last_seen", "") > entry.get("last_seen", ""):
            entry["last_seen"] = e["last_seen"]

        # Build identity set for offline matching
        online_identities.add((e["type"], e["name"]))
        if e["name"].endswith("_h"):
            base = e["name"][:-2]
            online_identities.add(("vless", base))

    online = list(merged.values())

    # Sort: by first_name
    online.sort(key=lambda u: (u.get("first_name") or '').lower())

    # Default speed
    for u in online:
        u.setdefault("speed_mbps", 0)

    return online, online_identities


@router.get("/online")
async def online_users():
    online, _ = _get_online_users()
    return online


@router.get("/online/stream", dependencies=[Depends(_require_admin_session)])
async def online_stream(request: Request):
    """SSE stream — kept for backward compatibility. New clients use /ws."""
    async def event_generator():
        while True:
            if await request.is_disconnected():
                break
            users, _ = _get_online_users()
            yield {"event": "online", "data": json.dumps(users)}
            await asyncio.sleep(10)

    return EventSourceResponse(event_generator())


@router.get("/offline")
async def offline_users():
    """Users with active VPN keys who are NOT currently online.
    All protocols merged into a single row per user (by tg_id/user_id)."""
    _, online_identities = _get_online_users()

    # Collect raw entries: {name, type, last_seen, last_seen_ts}
    raw_entries = []

    # ── VLESS: get clients + last_online directly from x-ui SQLite ──
    try:
        import sqlite3 as _sqlite3
        XUI_DB = "/etc/x-ui/x-ui.db"
        now_ms = int(time.time() * 1000)

        conn = _sqlite3.connect(f"file:{XUI_DB}?mode=ro", uri=True)
        cur = conn.cursor()

        cur.execute("SELECT email, last_online FROM client_traffics WHERE enable = 1")
        last_online_map = {row[0]: row[1] for row in cur.fetchall()}

        cur.execute("SELECT settings FROM inbounds WHERE protocol = 'vless'")
        for (settings_json,) in cur.fetchall():
            clients = json.loads(settings_json).get("clients", [])
            for c in clients:
                email = c.get("email", "").strip()
                if not email:
                    continue
                identity = ("vless", email)
                if identity in online_identities:
                    continue
                if not c.get("enable", True):
                    continue
                expiry = c.get("expiryTime", 0)
                if expiry and 0 < expiry < now_ms:
                    continue  # expired

                last_online = last_online_map.get(email, 0) or 0
                last_str = ""
                if last_online > 0:
                    try:
                        last_str = datetime.fromtimestamp(
                            last_online / 1000, tz=timezone.utc
                        ).strftime("%Y-%m-%d %H:%M")
                    except Exception:
                        pass

                raw_entries.append({
                    "name": email,
                    "type": "vless",
                    "last_seen": last_str,
                    "last_seen_ts": last_online,
                })

        # ── Hysteria2: get clients + last_online from x-ui SQLite ──
        cur.execute("SELECT settings FROM inbounds WHERE protocol = 'hysteria'")
        for (settings_json,) in cur.fetchall():
            clients = json.loads(settings_json).get("clients", [])
            for c in clients:
                email = c.get("email", "").strip()
                if not email:
                    continue
                identity = ("hysteria", email)
                if identity in online_identities:
                    continue
                if not c.get("enable", True):
                    continue
                expiry = c.get("expiryTime", 0)
                if expiry and 0 < expiry < now_ms:
                    continue  # expired

                last_online = last_online_map.get(email, 0) or 0
                last_str = ""
                if last_online > 0:
                    try:
                        last_str = datetime.fromtimestamp(
                            last_online / 1000, tz=timezone.utc
                        ).strftime("%Y-%m-%d %H:%M")
                    except Exception:
                        pass

                raw_entries.append({
                    "name": email,
                    "type": "hysteria",
                    "last_seen": last_str,
                    "last_seen_ts": last_online,
                })

        conn.close()
    except Exception as e:
        logger.warning(f"Offline VLESS/Hysteria error: {e}")

    # ── AWG: last handshake for all peers ──
    try:
        awg_clients = awg_db.list_clients()
        pub_to_name = {c["public_key"]: c["name"] for c in awg_clients}
        enabled_names = {c["name"] for c in awg_clients if c.get("enabled", True)}
        result = subprocess.run(
            ["awg", "show", "awg0", "dump"], capture_output=True, text=True, timeout=5
        )
        for line in result.stdout.strip().split("\n")[1:]:
            parts = line.split("\t")
            if len(parts) < 7:
                continue
            pub_key = parts[0]
            name = pub_to_name.get(pub_key)
            if not name or ("awg", name) in online_identities or name not in enabled_names:
                continue
            last_hs = int(parts[4]) if parts[4] != "0" else 0
            last_str = ""
            last_ts = 0
            if last_hs > 0:
                last_str = datetime.fromtimestamp(
                    last_hs, tz=timezone.utc
                ).strftime("%Y-%m-%d %H:%M")
                last_ts = last_hs * 1000

            raw_entries.append({
                "name": name,
                "type": "awg",
                "last_seen": last_str,
                "last_seen_ts": last_ts,
            })
    except Exception as e:
        logger.warning(f"Offline AWG error: {e}")

    # Resolve all client names to user info
    all_names = [e["name"] for e in raw_entries]
    user_info = _resolve_names_to_users(all_names)

    # Merge entries by user identity (tg_id or user_id)
    merged: dict[tuple, dict] = {}

    for e in raw_entries:
        info = user_info.get(e["name"], {})
        tg_id = info.get("tg_id")
        user_id = info.get("user_id")
        if tg_id and tg_id != 0:
            key = ("tg", tg_id)
        elif user_id:
            key = ("uid", user_id)
        else:
            key = ("name", e["name"])

        if key not in merged:
            merged[key] = {
                "tg_id": tg_id,
                "user_id": user_id,
                "first_name": info.get("first_name", ""),
                "web_token": info.get("web_token", ""),
                "expires": info.get("expires"),
                "is_test": info.get("is_test", False),
                "protocols": [],
                "names": [],
                "last_seen": "",
                "last_seen_ts": 0,
            }

        entry = merged[key]
        proto = e["type"]
        if proto not in entry["protocols"]:
            entry["protocols"].append(proto)
        entry["names"].append(e["name"])
        if (e.get("last_seen_ts") or 0) > (entry.get("last_seen_ts") or 0):
            entry["last_seen_ts"] = e.get("last_seen_ts", 0)
            entry["last_seen"] = e.get("last_seen", "")

    offline = list(merged.values())

    # Sort: most recently seen first, never-seen at the end
    offline.sort(key=lambda u: u.get("last_seen_ts", 0), reverse=True)

    return offline


@router.get("/speed/users")
async def speed_users(
    request: Request,
    hours: float = Query(24.0, ge=0.1, le=168),
    proto: str = Query("all"),
    search: str = Query(None),
):
    """Per-user traffic summary: protocol, IPs, total traffic over period."""
    _require_admin_session(request)

    conn = awg_db._get_conn()
    cur = conn.cursor(dictionary=True)

    since_dt = (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:%S")

    proto_filter = ""
    params: list = [since_dt]
    if proto and proto != "all":
        proto_filter = "AND s.vpn_type = %s"
        params.append(proto)

    search_filter = ""
    if search:
        search_filter = "AND (s.client_name LIKE %s OR u.first_name LIKE %s OR u.tg_id LIKE %s)"
        params.extend([f"%{search}%", f"%{search}%", f"%{search}%"])

    cur.execute(f"""
        SELECT
            s.client_name,
            s.vpn_type AS protocol,
            s.tg_id,
            s.user_id,
            COALESCE(NULLIF(u.first_name,''), u.old_first_name) AS first_name,
            MAX(s.ip_count) AS ip_count_max,
            GROUP_CONCAT(DISTINCT JSON_EXTRACT(s.ips_json, '$[0]')) AS sample_ips,
            SUM(s.total_bytes) AS total_bytes_sum,
            SUM(s.rx_bytes) AS rx_bytes_sum,
            SUM(s.tx_bytes) AS tx_bytes_sum,
            MAX(s.rx_speed_bps) AS rx_speed_max,
            MAX(s.tx_speed_bps) AS tx_speed_max,
            AVG(s.rx_speed_bps) AS rx_speed_avg,
            AVG(s.tx_speed_bps) AS tx_speed_avg,
            COUNT(*) AS snapshots
        FROM user_traffic_snapshots s
        LEFT JOIN users u ON (s.tg_id != 0 AND s.tg_id = u.tg_id)
                          OR (s.user_id IS NOT NULL AND s.user_id = u.id)
        WHERE s.snapshot_at >= %s {proto_filter} {search_filter}
        GROUP BY s.client_name, s.vpn_type, s.tg_id, s.user_id, u.first_name, u.old_first_name
        ORDER BY total_bytes_sum DESC
    """, tuple(params))

    rows = cur.fetchall()
    cur.close()
    conn.close()

    def fmt_bytes(b):
        if not b:
            return "0 B"
        b = float(b)
        for unit in ("B", "KB", "MB", "GB", "TB"):
            if b < 1024:
                return f"{b:.1f} {unit}"
            b /= 1024
        return f"{b:.1f} PB"

    def fmt_speed(bps):
        if not bps:
            return "0"
        bps = float(bps)
        if bps >= 1_000_000:
            return f"{bps / 1_000_000:.1f} Mbit/s"
        if bps >= 1_000:
            return f"{bps / 1_000:.0f} kbit/s"
        return f"{bps:.0f} bit/s"

    users = []
    for r in rows:
        users.append({
            "client_name": r["client_name"],
            "protocol": r["protocol"],
            "tg_id": r["tg_id"],
            "user_id": r["user_id"],
            "first_name": r["first_name"] or "",
            "ip_count": r["ip_count_max"] or 0,
            "sample_ips": r["sample_ips"] or "",
            "total_bytes": int(r["total_bytes_sum"] or 0),
            "rx_bytes": int(r["rx_bytes_sum"] or 0),
            "tx_bytes": int(r["tx_bytes_sum"] or 0),
            "total_fmt": fmt_bytes(r["total_bytes_sum"]),
            "rx_fmt": fmt_bytes(r["rx_bytes_sum"]),
            "tx_fmt": fmt_bytes(r["tx_bytes_sum"]),
            "rx_speed_max": fmt_speed(r["rx_speed_max"]),
            "tx_speed_max": fmt_speed(r["tx_speed_max"]),
            "rx_speed_avg": fmt_speed(r["rx_speed_avg"]),
            "tx_speed_avg": fmt_speed(r["tx_speed_avg"]),
            "snapshots": r["snapshots"],
        })

    return {"users": users, "hours": hours, "total": len(users)}


@router.get("/finance")
async def finance():
    """Server financials: revenue, costs, profitability."""
    from datetime import datetime as dt

    conn = awg_db._get_conn()
    cur = conn.cursor(dictionary=True)

    # Total revenue
    cur.execute("SELECT COALESCE(SUM(amount),0) as total FROM payments WHERE status='paid' AND is_test=0")
    total_revenue = float(cur.fetchone()["total"])

    # First payment date (service start)
    cur.execute("SELECT MIN(created_at) as d FROM payments WHERE status='paid' AND is_test=0")
    row = cur.fetchone()
    first_payment = row["d"] if row and row["d"] else None

    # Monthly breakdown
    fmt = "%Y-%m"
    cur.execute(
        "SELECT DATE_FORMAT(created_at, %s) as month,"
        " COUNT(*) as payments, SUM(amount) as revenue"
        " FROM payments WHERE status='paid' AND is_test=0"
        " GROUP BY DATE_FORMAT(created_at, %s) ORDER BY month",
        (fmt, fmt),
    )
    monthly = cur.fetchall()

    cur.close()
    conn.close()

    # Server cost
    server_cost = float(os.getenv("SERVER_MONTHLY_COST", "0"))

    # Days running
    now = dt.now()
    days_running = 0
    months_running = 0
    if first_payment:
        days_running = (now - first_payment).days or 1
        months_running = max(1, round(days_running / 30, 1))

    # Uptime
    try:
        r = subprocess.run(["uptime", "-s"], capture_output=True, text=True, timeout=5)
        uptime_since = r.stdout.strip()
    except Exception:
        uptime_since = "?"

    total_cost = server_cost * months_running if months_running else 0
    profit = total_revenue - total_cost
    roi = (total_revenue / total_cost * 100) if total_cost > 0 else 0

    # Avg revenue per month
    avg_monthly = total_revenue / months_running if months_running else 0

    return {
        "total_revenue": total_revenue,
        "server_monthly_cost": server_cost,
        "months_running": months_running,
        "days_running": days_running,
        "total_cost": round(total_cost, 2),
        "profit": round(profit, 2),
        "roi_percent": round(roi, 1),
        "avg_monthly_revenue": round(avg_monthly, 2),
        "monthly_profit": round(avg_monthly - server_cost, 2),
        "uptime_since": uptime_since,
        "first_payment": first_payment.isoformat() if first_payment else None,
        "monthly": [{
            "month": m["month"],
            "payments": m["payments"],
            "revenue": float(m["revenue"]),
        } for m in monthly],
    }


# ── Dashboard ────────────────────────────────────────────────────────────────

@router.get("/dashboard")
async def dashboard():
    # AWG stats
    awg_clients = awg_db.list_clients()
    awg_enabled = sum(1 for c in awg_clients if c["enabled"])
    awg_up = False
    try:
        r = subprocess.run(["awg", "show", "awg0"], capture_output=True, text=True)
        awg_up = r.returncode == 0
    except Exception:
        pass

    # XUI stats
    xui_data = {"inbounds": 0, "clients": 0, "up": 0, "down": 0, "running": False}
    try:
        r = subprocess.run(["pgrep", "-f", "xray-linux-amd64"], capture_output=True)
        xui_data["running"] = r.returncode == 0
    except Exception:
        pass
    try:
        xui = _get_xui()
        inbounds = xui.get_inbounds()
        xui_data["inbounds"] = len(inbounds)
        for ib in inbounds:
            settings = ib.get("settings", {})
            if isinstance(settings, str):
                settings = json.loads(settings)
            xui_data["clients"] += len(settings.get("clients", []))
            for cs in ib.get("clientStats", []):
                xui_data["up"] += cs.get("up", 0)
                xui_data["down"] += cs.get("down", 0)
    except Exception as e:
        logger.warning(f"XUI stats error: {e}")

    # Bot stats
    user_stats = admin_db.count_users()
    pay_stats = admin_db.payment_stats()
    recent = admin_db.recent_payments(10)

    # Extra dashboard data
    protocol_stats = admin_db.protocol_breakdown()
    discount_stats = admin_db.permanent_discount_summary()
    autopay_stats = admin_db.autopay_summary()

    # MTProto Proxy stats
    proxy_metrics = _parse_mtg_metrics()

    nl_proxy_status = "unknown"
    nl_proxy_routing = "de-to-nl-youtube"
    try:
        with open("/home/alvik/vpn-service/data/yt_nl_proxy_state", "r") as f:
            nl_proxy_status = f.read().strip()
        logger.info(f"NL proxy state file: {nl_proxy_status}")
    except Exception as e:
        logger.warning(f"NL proxy state read error: {e}")
    try:
        with open("/usr/local/x-ui/bin/config.json", "r") as f:
            xcfg = json.load(f)
        for rule in xcfg.get("routing", {}).get("rules", []):
            if any(d in rule.get("domain", []) for d in ["youtube.com", "geosite:youtube"]):
                nl_proxy_routing = rule.get("outboundTag", "unknown")
                break
        logger.info(f"NL proxy routing from config: {nl_proxy_routing}")
    except Exception as e:
        logger.warning(f"NL proxy config read error: {e}")

    return {
        "awg": {
            "clients_total": len(awg_clients),
            "clients_enabled": awg_enabled,
            "interface_up": awg_up,
        },
        "proxy": {
            "connections": proxy_metrics["client_connections"],
            "running": proxy_metrics["running"],
            "traffic_in": proxy_metrics["traffic_from_client"],
            "traffic_out": proxy_metrics["traffic_to_client"],
            "traffic_in_fmt": _fmt_bytes(proxy_metrics["traffic_from_client"]),
            "traffic_out_fmt": _fmt_bytes(proxy_metrics["traffic_to_client"]),
            "ping_ms": _ping_telegram_dc(),
        },
        "xui": {
            "inbounds": xui_data["inbounds"],
            "clients": xui_data["clients"],
            "traffic_up": xui_data["up"],
            "traffic_up_fmt": _fmt_bytes(xui_data["up"]),
            "traffic_down": xui_data["down"],
            "traffic_down_fmt": _fmt_bytes(xui_data["down"]),
            "xray_running": xui_data["running"],
        },
        "nl_proxy": {
            "status": nl_proxy_status,
            "routing": nl_proxy_routing,
            "host": "2.26.76.210",
            "port": 15687,
        },
        "users": {
            "total": user_stats["total"],
            "active": user_stats["active"],
            "active_sub": user_stats.get("active_sub", 0),
            "active_key_only": user_stats.get("active_key_only", 0),
        },
        "payments": {
            "total": pay_stats["total"],
            "paid": pay_stats["paid"],
            "revenue": float(pay_stats["revenue"] or 0),
        },
        "recent_payments": _clean(recent),
        "protocol_breakdown": protocol_stats,
        "discount_summary": discount_stats,
        "autopay_summary": autopay_stats,
    }


# ── AWG Server ───────────────────────────────────────────────────────────────

@router.get("/awg/server")
async def awg_server():
    srv = awg_db.get_server_config()
    if not srv:
        return {"error": "No server config"}
    # Don't expose private key
    srv.pop("private_key", None)
    return srv


# ── XUI Inbounds ─────────────────────────────────────────────────────────────

@router.get("/xui/inbounds")
async def xui_inbounds():
    try:
        xui = _get_xui()
        inbounds = xui.get_inbounds()
        result = []
        for ib in inbounds:
            settings = json.loads(ib.get("settings", "{}"))
            stream = json.loads(ib.get("streamSettings", "{}"))
            clients = settings.get("clients", [])
            stats = {cs["email"]: cs for cs in ib.get("clientStats", [])}

            total_up = sum(cs.get("up", 0) for cs in ib.get("clientStats", []))
            total_down = sum(cs.get("down", 0) for cs in ib.get("clientStats", []))

            result.append({
                "id": ib["id"],
                "remark": ib.get("remark", ""),
                "port": ib.get("port"),
                "protocol": ib.get("protocol"),
                "enable": ib.get("enable"),
                "network": stream.get("network"),
                "security": stream.get("security"),
                "client_count": len(clients),
                "traffic_up": total_up,
                "traffic_up_fmt": _fmt_bytes(total_up),
                "traffic_down": total_down,
                "traffic_down_fmt": _fmt_bytes(total_down),
            })
        return result
    except Exception as e:
        logger.error(f"XUI inbounds error: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)


@router.get("/xui/inbounds/{inbound_id}/clients")
async def xui_inbound_clients(inbound_id: int):
    try:
        xui = _get_xui()
        inbounds = xui.get_inbounds()

        # Build global stats map across ALL inbounds (email -> aggregated stats)
        global_stats: dict[str, dict] = {}
        for _ib in inbounds:
            for cs in _ib.get("clientStats", []):
                email = cs.get("email", "")
                if email in global_stats:
                    global_stats[email]["up"] += cs.get("up", 0)
                    global_stats[email]["down"] += cs.get("down", 0)
                else:
                    global_stats[email] = {**cs}

        # Enrich with last_online from SQLite (API doesn't return it)
        try:
            import sqlite3 as _sqlite3
            XUI_DB = "/home/alvik/vpn-service/docker/x-ui-data/x-ui.db"
            _conn = _sqlite3.connect(f"file:{XUI_DB}?mode=ro", uri=True)
            _cur = _conn.cursor()
            _cur.execute("SELECT email, last_online FROM client_traffics")
            for _email, _lo in _cur.fetchall():
                if _email in global_stats:
                    global_stats[_email]["last_online"] = _lo or 0
                else:
                    global_stats[_email] = {"last_online": _lo or 0, "up": 0, "down": 0}
            _conn.close()
        except Exception as e:
            logger.warning(f"SQLite last_online enrichment: {e}")

        for ib in inbounds:
            if ib["id"] != inbound_id:
                continue
            settings = json.loads(ib.get("settings", "{}"))
            clients = settings.get("clients", [])

            # Lookup first_name via vpn_keys → users
            emails = [c.get("email", "") for c in clients]
            name_map: dict[str, str] = {}  # email -> first_name
            tgid_map: dict[str, int] = {}  # email -> tg_id
            token_map: dict[str, str] = {}  # email -> web_token
            if emails:
                try:
                    conn = awg_db._get_conn()
                    cur = conn.cursor(dictionary=True)
                    ph = ",".join(["%s"] * len(emails))
                    cur.execute(
                        f"SELECT k.client_name, u.first_name, u.tg_id, u.web_token "
                        f"FROM vpn_keys k JOIN users u ON k.tg_id = u.tg_id "
                        f"WHERE k.client_name IN ({ph})",
                        tuple(emails),
                    )
                    for r in cur.fetchall():
                        name_map[r["client_name"]] = r.get("first_name") or ""
                        tgid_map[r["client_name"]] = r.get("tg_id")
                        token_map[r["client_name"]] = r.get("web_token") or ""
                    cur.close()
                    conn.close()
                except Exception as e:
                    logger.warning(f"VLESS first_name lookup: {e}")

            result = []
            for c in clients:
                email = c.get("email", "")
                cs = global_stats.get(email, {})
                expiry = c.get("expiryTime", 0)
                expiry_str = ""
                if expiry and expiry > 0:
                    try:
                        expiry_str = datetime.fromtimestamp(expiry / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
                    except Exception:
                        pass

                last_online = cs.get("last_online", 0)
                last_str = ""
                if last_online and last_online > 0:
                    try:
                        last_str = datetime.fromtimestamp(last_online / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
                    except Exception:
                        pass

                result.append({
                    "email": email,
                    "uuid": c.get("id", ""),
                    "tgId": c.get("tgId", ""),
                    "subId": c.get("subId", ""),
                    "enable": c.get("enable", True),
                    "flow": c.get("flow", ""),
                    "up": cs.get("up", 0),
                    "up_fmt": _fmt_bytes(cs.get("up", 0)),
                    "down": cs.get("down", 0),
                    "down_fmt": _fmt_bytes(cs.get("down", 0)),
                    "expiry": expiry_str,
                    "expiry_ts": expiry,
                    "last_online": last_str,
                    "limitIp": c.get("limitIp", 0),
                    "first_name": name_map.get(email, ""),
                    "tg_id_db": tgid_map.get(email),
                    "web_token": token_map.get(email, ""),
                })
            return result
        return JSONResponse({"error": "Inbound not found"}, status_code=404)
    except Exception as e:
        logger.error(f"XUI clients error: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)



    """Ping Telegram DC2 and return latency in ms."""
    try:
        r = subprocess.run(
            ["ping", "-c", "1", "-W", "2", "149.154.167.51"],
            capture_output=True, text=True, timeout=5,
        )
        match = re.search(r"time=([\d.]+)", r.stdout)
        if match:
            return round(float(match.group(1)), 1)
    except Exception:
        pass
    return None


def _parse_mtg_metrics() -> dict:
    """Fetch and parse mtg Prometheus metrics."""
    import urllib.request
    result = {
        "running": False,
        "client_connections": 0,
        "telegram_connections": 0,
        "replay_attacks": 0,
        "domain_fronting": 0,
        "traffic_from_client": 0,
        "traffic_to_client": 0,
        "dc_details": [],
    }
    try:
        resp = urllib.request.urlopen("http://127.0.0.1:3129/metrics", timeout=3)
        text = resp.read().decode()
        result["running"] = True

        dc_map: dict[str, dict] = {}  # dc -> {ip, connections, from, to}

        for line in text.split("\n"):
            if line.startswith("#") or not line.strip():
                continue
            if line.startswith("mtg_client_connections"):
                val = line.split("} ")[-1] if "} " in line else "0"
                result["client_connections"] += int(float(val))
            elif line.startswith("mtg_telegram_connections"):
                val = line.split("} ")[-1] if "} " in line else "0"
                count = int(float(val))
                result["telegram_connections"] += count
                # Parse DC details
                dc_match = re.search(r'dc="(\d+)"', line)
                ip_match = re.search(r'telegram_ip="([^"]+)"', line)
                if dc_match:
                    dc = dc_match.group(1)
                    dc_map.setdefault(dc, {"dc": dc, "ip": ip_match.group(1) if ip_match else "?",
                                           "connections": 0, "traffic_from_client": 0, "traffic_to_client": 0})
                    dc_map[dc]["connections"] = count
            elif line.startswith("mtg_replay_attacks"):
                val = line.split()[-1]
                result["replay_attacks"] = int(float(val))
            elif line.startswith("mtg_domain_fronting"):
                val = line.split()[-1]
                result["domain_fronting"] = int(float(val))
            elif line.startswith("mtg_telegram_traffic"):
                val = line.split("} ")[-1] if "} " in line else "0"
                traffic = int(float(val))
                dc_match = re.search(r'dc="(\d+)"', line)
                ip_match = re.search(r'telegram_ip="([^"]+)"', line)
                if dc_match:
                    dc = dc_match.group(1)
                    dc_map.setdefault(dc, {"dc": dc, "ip": ip_match.group(1) if ip_match else "?",
                                           "connections": 0, "traffic_from_client": 0, "traffic_to_client": 0})
                if 'direction="from_client"' in line:
                    result["traffic_from_client"] += traffic
                    if dc_match:
                        dc_map[dc_match.group(1)]["traffic_from_client"] = traffic
                elif 'direction="to_client"' in line:
                    result["traffic_to_client"] += traffic
                    if dc_match:
                        dc_map[dc_match.group(1)]["traffic_to_client"] = traffic

        result["dc_details"] = list(dc_map.values())
    except Exception as e:
        logger.warning(f"MTProto metrics parse error: {e}")
    return result


@router.get("/proxy")
async def proxy_stats():
    return _parse_mtg_metrics()


# ── New Users Today ───────────────────────────────────────────────────────────

@router.get("/users/today")
async def users_today():
    rows = admin_db.new_users_today()
    cleaned = _clean(rows)

    # Get client_names for these users from vpn_keys
    tg_ids = [r["tg_id"] for r in cleaned if r.get("tg_id")]
    user_ids = [r["id"] for r in cleaned if not r.get("tg_id") and r.get("id")]

    if not tg_ids and not user_ids:
        return cleaned

    conn = awg_db._get_conn()
    cur = conn.cursor(dictionary=True)
    conditions = []
    params: list = []
    if tg_ids:
        conditions.append(f"tg_id IN ({','.join(['%s'] * len(tg_ids))})")
        params.extend(tg_ids)
    if user_ids:
        conditions.append(f"user_id IN ({','.join(['%s'] * len(user_ids))})")
        params.extend(user_ids)
    cur.execute(
        f"SELECT tg_id, user_id, client_name, vpn_type FROM vpn_keys WHERE {' OR '.join(conditions)}",
        tuple(params),
    )
    key_rows = cur.fetchall()
    cur.close()
    conn.close()

    # tg_id -> list of client_names (for TG users)
    tg_keys: dict[int, list[dict]] = {}
    for kr in key_rows:
        if kr.get("tg_id") and kr["tg_id"] != 0:
            tg_keys.setdefault(kr["tg_id"], []).append(kr)

    # user_id -> list of client_names (for web users)
    uid_keys: dict[int, list[dict]] = {}
    for kr in key_rows:
        if kr.get("user_id"):
            uid_keys.setdefault(kr["user_id"], []).append(kr)

    # Get VLESS traffic from x-ui
    vless_traffic: dict[str, dict] = {}  # email -> {up, down}
    try:
        xui = _get_xui()
        for ib in xui.get_inbounds():
            for cs in ib.get("clientStats", []):
                email = cs.get("email", "")
                if email in vless_traffic:
                    vless_traffic[email]["up"] += cs.get("up", 0)
                    vless_traffic[email]["down"] += cs.get("down", 0)
                else:
                    vless_traffic[email] = {"up": cs.get("up", 0), "down": cs.get("down", 0)}
    except Exception as e:
        logger.warning(f"VLESS traffic fetch for new users: {e}")

    # Get AWG traffic from awg show dump
    awg_traffic: dict[str, dict] = {}  # name -> {rx, tx}
    try:
        awg_clients = awg_db.list_clients()
        pub_to_name = {c["public_key"]: c["name"] for c in awg_clients}
        result = subprocess.run(
            ["awg", "show", "awg0", "dump"], capture_output=True, text=True, timeout=5
        )
        for line in result.stdout.strip().split("\n")[1:]:
            parts = line.split("\t")
            if len(parts) < 7:
                continue
            name = pub_to_name.get(parts[0], "")
            if name:
                rx = int(parts[5]) if parts[5].isdigit() else 0
                tx = int(parts[6]) if parts[6].isdigit() else 0
                awg_traffic[name] = {"rx": rx, "tx": tx}
    except Exception as e:
        logger.warning(f"AWG traffic fetch for new users: {e}")

    # Get online users for speed — build name->speed map from all protocol names
    online, _ = _get_online_users()
    online_speed: dict[str, float] = {}
    for u in online:
        speed = u.get("speed_mbps", 0)
        for nm in (u.get("names") or [u.get("name")]):
            if nm:
                online_speed[nm] = speed

    # Enrich each user
    for u in cleaned:
        total_up = 0
        total_down = 0
        speed = 0.0
        keys = tg_keys.get(u["tg_id"], []) if u.get("tg_id") else uid_keys.get(u.get("id"), [])
        for k in keys:
            cn = k["client_name"]
            if k["vpn_type"] == "vless" and cn in vless_traffic:
                total_up += vless_traffic[cn]["up"]
                total_down += vless_traffic[cn]["down"]
            elif k["vpn_type"] == "awg" and cn in awg_traffic:
                total_up += awg_traffic[cn]["tx"]
                total_down += awg_traffic[cn]["rx"]
            if cn in online_speed:
                speed = max(speed, online_speed[cn])
        u["traffic_up"] = total_up
        u["traffic_down"] = total_down
        u["traffic_up_fmt"] = _fmt_bytes(total_up)
        u["traffic_down_fmt"] = _fmt_bytes(total_down)
        u["speed_mbps"] = round(speed, 1)

    return cleaned


# ── Site Analytics ─────────────────────────────────────────────────────────────

@router.get("/site-stats")
async def site_stats():
    from api.db import execute_query
    rows = execute_query(
        "SELECT event_type, COUNT(*) AS cnt FROM site_events GROUP BY event_type",
        fetch='all',
    )
    stats = {r["event_type"]: r["cnt"] for r in rows}

    # Unique visitors = distinct visitor_id for 'visit' events
    uv = execute_query(
        "SELECT COUNT(DISTINCT visitor_id) AS cnt FROM site_events WHERE event_type='visit'",
        fetch='one',
    )
    stats["unique_visitors"] = uv["cnt"] if uv else 0

    # Today counts (UTC+9)
    today_rows = execute_query(
        """SELECT event_type, COUNT(*) AS cnt FROM site_events
           WHERE DATE(CONVERT_TZ(created_at, '+00:00', '+09:00'))
               = DATE(CONVERT_TZ(UTC_TIMESTAMP(), '+00:00', '+09:00'))
           GROUP BY event_type""",
        fetch='all',
    )
    today = {r["event_type"]: r["cnt"] for r in today_rows}

    uv_today = execute_query(
        """SELECT COUNT(DISTINCT visitor_id) AS cnt FROM site_events
           WHERE event_type='visit'
           AND DATE(CONVERT_TZ(created_at, '+00:00', '+09:00'))
             = DATE(CONVERT_TZ(UTC_TIMESTAMP(), '+00:00', '+09:00'))""",
        fetch='one',
    )
    today["unique_visitors"] = uv_today["cnt"] if uv_today else 0

    # Email codes sent
    codes_total = execute_query(
        "SELECT COUNT(*) AS cnt FROM auth_codes WHERE channel = 'email'",
        fetch='one',
    )
    codes_today = execute_query(
        """SELECT COUNT(*) AS cnt FROM auth_codes
           WHERE channel = 'email'
           AND DATE(CONVERT_TZ(created_at, '+00:00', '+09:00'))
             = DATE(CONVERT_TZ(UTC_TIMESTAMP(), '+00:00', '+09:00'))""",
        fetch='one',
    )

    return {
        "total": stats,
        "today": today,
        "codes_sent": codes_total["cnt"] if codes_total else 0,
        "codes_sent_today": codes_today["cnt"] if codes_today else 0,
    }


# ── Email Stats ───────────────────────────────────────────────────────────────

@router.get("/email-stats")
async def email_stats():
    from api.db import execute_query

    # Total sent / opened from email_opens
    totals = execute_query(
        "SELECT COUNT(*) AS total, SUM(opened_at IS NOT NULL) AS opened FROM email_opens",
        fetch='one',
    )
    total_sent = totals["total"] if totals else 0
    total_opened = int(totals["opened"] or 0) if totals else 0

    # Today's support messages (from site contact form)
    today_support = execute_query(
        """SELECT COUNT(*) AS cnt FROM auth_codes
           WHERE channel = 'email'
           AND DATE(CONVERT_TZ(created_at, '+00:00', '+09:00'))
             = DATE(CONVERT_TZ(UTC_TIMESTAMP(), '+00:00', '+09:00'))""",
        fetch='one',
    )

    # Recent tracking entries (convert CET → UTC+9)
    recent = execute_query(
        """SELECT email, campaign,
           CONVERT_TZ(opened_at, '+01:00', '+09:00') AS opened_at,
           CONVERT_TZ(created_at, '+01:00', '+09:00') AS created_at
           FROM email_opens ORDER BY created_at DESC LIMIT 20""",
        fetch='all',
    )

    return {
        "total_sent": total_sent,
        "total_opened": total_opened,
        "today_codes": today_support["cnt"] if today_support else 0,
        "recent": _clean(recent or []),
    }


# ── Winback Log ───────────────────────────────────────────────────────────────

_WINBACK_MESSAGES = {
    'zero_traffic': "👋 Привет!\nМы заметили, что вы ещё не подключились к VPN. Нужна помощь с настройкой?\n📱 Быстрый старт:\n1️⃣ Нажмите Мои конфиги\n2️⃣ Скопируйте ссылку подписки\n3️⃣ Вставьте в приложение (Happ, Hiddify, Streisand)\nЕсли что-то не получается — напишите нам 💬",
    'low_traffic': "👋 Привет!\nПохоже, VPN подключение не заработало как нужно. Мы можем помочь!\nПопробуйте:\n• Обновите ссылку подписки\n• Используйте Happ или Hiddify\n• Включите/выключите VPN заново\nЕсли не помогло — напишите в поддержку 💬",
    'expired_fresh': "⏰ Ваша подписка недавно истекла.\nПродлите сейчас!\n🎁 Персональный промокод со скидкой 10% (7 дней) создан автоматически.",
    'expired_old': "👋 Давно не виделись!\nМы обновили сервис — стало быстрее и стабильнее.\n🎁 Персональный промокод со скидкой 20% (14 дней) создан автоматически.",
    'test_no_purchase': "👋 Вы пробовали тестовый период.\nГотовы к полному доступу?\n🎁 Персональный промокод со скидкой 15% (7 дней) создан автоматически.",
    'test_no_connect': "👋 Вы активировали тестовый период, но не подключились.\nМы продлили доступ на 1 день!\n🎁 Персональный промокод со скидкой 15% (7 дней) создан автоматически.\n📱 Быстрый старт:\n1️⃣ Мои конфиги\n2️⃣ Скопируйте ссылку\n3️⃣ Вставьте в приложение\nПомощь — пишите 💬",
    'payment_no_config': "⚠️ Мы обнаружили, что ваш платёж был успешным, но VPN конфиг не был создан.\nМы уже разбираемся с этим. Если вопрос не решится — напишите в поддержку 💬",
    'panel_db_mismatch': "⚠️ Обнаружена проблема с вашим конфигом. Мы уже работаем над исправлением.\nЕсли VPN не подключается — напишите в поддержку 💬",
    'never_activated': "👋 Привет!\nВы зарегистрировались, но ещё не попробовали VPN.\nАктивируйте бесплатный тест — это займёт пару минут!\n🔒 Безопасный интернет без ограничений.",
    'vless_only_inactive': "👋 Заметили, что вы не подключались к VPN больше суток.\nЕсли есть проблемы с подключением — попробуйте протокол AmneziaWG. Он лучше работает на нестабильных каналах, мобильном интернете и в удалённых регионах.\nНажмите кнопку ниже — мы выдадим вам конфиг AmneziaWG в дополнение к текущему VLESS.",
    'awg_inactive': "👋 Привет!\nЗаметили, что вы давно не подключались к VPN. Всё ли в порядке?\nЕсли возникли вопросы или проблемы с подключением — напишите нам, поможем! 💬",
    'recently_inactive': "👋 Мы скучаем!\nЗаметили, что вы давно не заходили. Всё ли в порядке с подключением?\n💡 У нас есть бесплатный прокси для Telegram — работает без VPN.",
    'second_expiry_reminder': "⏰ Напоминаем: ваша подписка истекла 14 дней назад.\nПродлите сейчас!\n🎁 Персональный промокод со скидкой 20% (14 дней) создан автоматически.",
    'hysteria_inactive': "👋 Привет!\nЗаметили, что вы давно не подключались к Hysteria 2.\nЭтот протокол отлично работает для обхода жёстких блокировок.\nЕсли возникли проблемы — напишите нам, поможем! 💬",
    'long_inactive_7d': "👋 Давно не виделись!\nВы не заходили к нам больше недели. Мы обновили сервис — стало быстрее и стабильнее!\nВозвращайтесь — будем рады 🎁",
    'referral_prompt': "👋 Привет!\nВы с нами уже {reg_days} дней — надеемся, всё отлично!\n💡 Приглашайте друзей: вы +{referrer_days} дней, друг +{newcomer_days} дней бесплатно!\nДелитесь ссылкой прямо сейчас!",
}


@router.get("/winback")
async def winback_log():
    from config import REFERRAL_REWARD_DAYS, REFERRAL_NEWCOMER_DAYS
    rows = admin_db.list_winback_log()
    cleaned = _clean(rows)
    for r in cleaned:
        msg = _WINBACK_MESSAGES.get(r.get("scenario", ""), "")
        if '{referrer_days}' in msg:
            msg = msg.format(
                reg_days=r.get("reg_days", ""),
                referrer_days=REFERRAL_REWARD_DAYS,
                newcomer_days=REFERRAL_NEWCOMER_DAYS,
            )
        r["message"] = msg
    return cleaned


@router.get("/winback/effectiveness")
async def winback_effectiveness():
    """Conversion rates per winback scenario (payment or key creation within 7 days)."""
    return _clean(admin_db.winback_effectiveness())


# ── Promocodes ────────────────────────────────────────────────────────────────

@router.get("/promocodes")
async def promocodes_list():
    rows = admin_db.list_promocodes()
    return _clean(rows)


# ── Autopay Failures ──────────────────────────────────────────────────────────

@router.get("/autopay-failures")
async def autopay_failures(limit: int = Query(50)):
    return _clean(admin_db.autopay_failures(limit=limit))


# ── Referral Network ─────────────────────────────────────────────────────────

@router.get("/referral-network")
async def referral_network(limit: int = Query(50)):
    return _clean(admin_db.referral_network(limit=limit))


# ── Failed Payments ──────────────────────────────────────────────────────────

@router.get("/failed-payments")
async def failed_payments(limit: int = Query(30)):
    return _clean(admin_db.failed_pending_payments(limit=limit))


# ── Promo Usage Log ──────────────────────────────────────────────────────────

@router.get("/promo-usages")
async def promo_usages(limit: int = Query(50)):
    return _clean(admin_db.promo_usage_details(limit=limit))


# ── Test to Paid Conversion ──────────────────────────────────────────────────

@router.get("/test-conversion")
async def test_conversion():
    return admin_db.test_to_paid_by_protocol()


@router.get("/expiry")
async def get_expiry(names: str = Query(...)):
    import json
    try:
        name_list = json.loads(names)
        return admin_db.get_expiry_by_client_names(name_list)
    except:
        return {}


# ── Users ────────────────────────────────────────────────────────────────────

@router.get("/users")
async def users_list(search: str = Query(None), limit: int = Query(100)):
    rows = admin_db.list_users(search=search, limit=limit)
    tg_ids = [r["tg_id"] for r in rows if r.get("tg_id")]
    keys_map = admin_db.get_users_keys_batch(tg_ids)
    for r in rows:
        entry = keys_map.get(r["tg_id"], {})
        r["keys"] = entry.get("active", [])
        r["last_key_expires"] = entry.get("last_expires")
    return _clean(rows)


@router.get("/users/{tg_id}/keys")
async def user_keys(tg_id: int):
    rows = admin_db.get_user_keys(tg_id)
    return _clean(rows)


@router.get("/users/{tg_id}/payments")
async def user_payments(tg_id: int):
    rows = admin_db.get_user_payments(tg_id)
    return _clean(rows)


@router.get("/users/{tg_id}/autopay")
async def user_autopay(tg_id: int):
    from api.db import execute_query
    row = execute_query(
        "SELECT autopay_enabled, autopay_tariff, autopay_vpn_type, payment_method_id "
        "FROM users WHERE tg_id = %s", (tg_id,), fetch='one'
    )
    return row or {}


@router.get("/funnel")
async def conversion_funnel():
    """Воронка конверсий: регистрация → тест → подключение → оплата → повторная."""
    from api.db import execute_query

    total = execute_query("SELECT COUNT(*) AS n FROM users", fetch='one')['n']

    test_activated = execute_query(
        "SELECT COUNT(*) AS n FROM users WHERE test_vless_activated = 1 OR test_awg_activated = 1",
        fetch='one',
    )['n']

    has_keys = execute_query(
        "SELECT COUNT(DISTINCT COALESCE(user_id, tg_id)) AS n FROM vpn_keys",
        fetch='one',
    )['n']

    connected = execute_query(
        "SELECT COUNT(DISTINCT tg_id) AS n FROM vpn_keys WHERE tg_id != 0",
        fetch='one',
    )['n']

    paid_users = execute_query(
        "SELECT COUNT(DISTINCT tg_id) AS n FROM payments WHERE status = 'paid' AND is_test = 0",
        fetch='one',
    )['n']

    repeat_buyers = execute_query(
        "SELECT COUNT(*) AS n FROM ("
        "  SELECT tg_id FROM payments WHERE status = 'paid' AND is_test = 0 "
        "  GROUP BY tg_id HAVING COUNT(*) >= 2"
        ") t",
        fetch='one',
    )['n']

    autopay_on = execute_query(
        "SELECT COUNT(*) AS n FROM users WHERE autopay_enabled = 1",
        fetch='one',
    )['n']

    revenue_total = execute_query(
        "SELECT COALESCE(SUM(amount), 0) AS n FROM payments WHERE status = 'paid' AND is_test = 0",
        fetch='one',
    )['n']

    revenue_30d = execute_query(
        "SELECT COALESCE(SUM(amount), 0) AS n FROM payments "
        "WHERE status = 'paid' AND is_test = 0 AND created_at >= NOW() - INTERVAL 30 DAY",
        fetch='one',
    )['n']

    # Tariff popularity
    tariff_stats = execute_query(
        "SELECT tariff, COUNT(*) AS cnt, SUM(amount) AS revenue "
        "FROM payments WHERE status = 'paid' AND is_test = 0 "
        "GROUP BY tariff ORDER BY cnt DESC",
        fetch='all',
    )

    # Daily registrations (last 30 days)
    daily_regs = execute_query(
        "SELECT DATE(created_at) AS day, COUNT(*) AS cnt FROM users "
        "WHERE created_at >= NOW() - INTERVAL 30 DAY "
        "GROUP BY DATE(created_at) ORDER BY day",
        fetch='all',
    )

    return _clean({
        "funnel": {
            "total_users": total,
            "test_activated": test_activated,
            "has_vpn_keys": has_keys,
            "connected": connected,
            "paid_users": paid_users,
            "repeat_buyers": repeat_buyers,
            "autopay_enabled": autopay_on,
        },
        "rates": {
            "test_rate": round(test_activated / total * 100, 1) if total else 0,
            "paid_rate": round(paid_users / total * 100, 1) if total else 0,
            "repeat_rate": round(repeat_buyers / paid_users * 100, 1) if paid_users else 0,
        },
        "revenue": {
            "total": float(revenue_total),
            "last_30d": float(revenue_30d),
            "arpu": round(float(revenue_total) / paid_users, 1) if paid_users else 0,
        },
        "tariff_stats": tariff_stats,
        "daily_registrations": daily_regs,
    })


# ── x-ui Backup ───────────────────────────────────────────────────────────────

@router.post("/xui/backup")
async def xui_backup():
    """Create a full x-ui backup snapshot on the server."""
    import sqlite3 as _sqlite3
    import shutil

    backup_dir = "/var/backups/x-ui"
    timestamp = (datetime.now(timezone.utc) + timedelta(hours=9)).strftime("%Y-%m-%d_%H-%M-%S")
    snapshot = os.path.join(backup_dir, timestamp)
    os.makedirs(snapshot, exist_ok=True)

    # 1. x-ui SQLite database (inbounds, clients, settings)
    shutil.copy2("/etc/x-ui/x-ui.db", os.path.join(snapshot, "x-ui.db"))

    # 2. system metrics
    metrics_src = "/etc/x-ui/system_metrics.gob"
    if os.path.exists(metrics_src):
        shutil.copy2(metrics_src, os.path.join(snapshot, "system_metrics.gob"))

    # 3. Full /etc/x-ui archive
    subprocess.run(
        ["tar", "czf", os.path.join(snapshot, "x-ui-etc.tar.gz"), "-C", "/etc/x-ui", "."],
        capture_output=True, timeout=10,
    )

    # 4. x-ui binary
    shutil.copy2("/usr/local/x-ui/x-ui", os.path.join(snapshot, "x-ui-bin"))

    # Count total backups
    backups = sorted(os.listdir(backup_dir))

    # Cleanup: keep last 10 snapshots
    while len(backups) > 10:
        old = os.path.join(backup_dir, backups[0])
        shutil.rmtree(old, ignore_errors=True)
        backups.pop(0)

    return {"status": "ok", "path": snapshot, "total_backups": len(backups)}


@router.get("/xui/backups")
async def xui_list_backups():
    """List all available x-ui backup snapshots."""
    import shutil as _shutil

    backup_dir = "/var/backups/x-ui"
    if not os.path.isdir(backup_dir):
        return {"backups": []}

    backups = []
    for name in sorted(os.listdir(backup_dir), reverse=True):
        path = os.path.join(backup_dir, name)
        if not os.path.isdir(path):
            continue
        # Parse timestamp from dirname: stored in UTC+9
        try:
            dt = datetime.strptime(name, "%Y-%m-%d_%H-%M-%S")
            label = dt.strftime("%d %b %Y %H:%M:%S")
        except ValueError:
            label = name
        # Size
        total_size = 0
        for dirpath, _, filenames in os.walk(path):
            for f in filenames:
                fp = os.path.join(dirpath, f)
                total_size += os.path.getsize(fp)
        backups.append({
            "id": name,
            "label": label,
            "size": total_size,
            "size_fmt": _fmt_bytes(total_size),
        })

    return {"backups": backups}


@router.post("/xui/restore/{backup_id}")
async def xui_restore(backup_id: str, request: Request):
    """Restore x-ui database from a backup snapshot."""
    import shutil as _shutil

    # Sanitize backup_id — only allow dirnames matching our timestamp format
    import re as _re
    if not _re.match(r"^\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}$", backup_id):
        raise HTTPException(status_code=400, detail="Invalid backup ID format")

    snapshot = os.path.join("/var/backups/x-ui", backup_id)
    db_src = os.path.join(snapshot, "x-ui.db")
    if not os.path.isfile(db_src):
        raise HTTPException(status_code=404, detail="Backup not found or missing x-ui.db")

    # Must confirm via body
    body = await request.json()
    if not body.get("confirm"):
        raise HTTPException(status_code=400, detail="Pass confirm: true in body to proceed")

    # Safety: create emergency backup of current DB first
    ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    emergency = f"/etc/x-ui/x-ui.db.before-restore-{ts}"
    with open("/etc/x-ui/x-ui.db", "rb") as src, open(emergency, "wb") as dst:
        dst.write(src.read())

    # Restore — write content only to avoid PermissionError on docker bind mounts
    with open(db_src, "rb") as src, open("/etc/x-ui/x-ui.db", "wb") as dst:
        dst.write(src.read())

    # Restart x-ui to apply
    subprocess.run(["systemctl", "restart", "x-ui"], capture_output=True, timeout=15)

    return {"status": "ok", "restored_from": backup_id, "emergency_backup": emergency}


# ── Admin page serving ────────────────────────────────────────────────────────

def get_admin_page_route():
    """Return the admin page endpoint for mounting in the app."""
    async def admin_page(request: Request):
        # nginx auth_basic already protects this path — skip session check
        # Pre-create a session cookie so JS doesn't need the API password
        import secrets as _secrets
        from awg_api.main import _sessions, SESSION_MAX_AGE

        html_path = os.path.join(os.path.dirname(__file__), "static", "admin.html")
        with open(html_path) as f:
            html = f.read()
        # Do NOT embed the real password — use a placeholder so JS skips login
        html = html.replace("{{AWG_PASSWORD}}", "")

        token = _secrets.token_hex(24)
        _sessions[token] = datetime.now(timezone.utc).timestamp()

        response = HTMLResponse(html)
        response.set_cookie(
            key="connect.sid", value=token,
            httponly=True, secure=True, samesite="lax", max_age=SESSION_MAX_AGE,
        )
        return response
    return admin_page


# ── Test Payment (YooKassa → VLESS sub link) ────────────────────────────────

@router.post("/test-payment")
async def create_test_payment(request: Request):
    """Create a YooKassa test payment so admin can verify the full flow:
    payment → webhook → VLESS sub link registration."""
    from yookassa import Configuration, Payment as YooPayment
    from config import (
        YOO_KASSA_TEST_SHOP_ID, YOO_KASSA_TEST_SECRET_KEY,
        YOO_KASSA_SHOP_ID, YOO_KASSA_SECRET_KEY, ADMIN_TG_ID,
    )
    from api.db import create_payment
    import uuid

    if not YOO_KASSA_TEST_SHOP_ID or not YOO_KASSA_TEST_SECRET_KEY:
        return JSONResponse({"error": "YooKassa test credentials not configured"}, status_code=500)

    body = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
    tariff_id = body.get("tariff_id", "weekly_7d")

    from bot_xui.tariffs import TARIFFS
    tariff = TARIFFS.get(tariff_id)
    if not tariff or tariff.get("is_test"):
        return JSONResponse({"error": f"Invalid tariff: {tariff_id}"}, status_code=400)

    # Temporarily switch to test credentials, then restore production
    Configuration.account_id = YOO_KASSA_TEST_SHOP_ID
    Configuration.secret_key = YOO_KASSA_TEST_SECRET_KEY

    try:
        payment = YooPayment.create(
            {
                "amount": {"value": str(tariff["price"]), "currency": "RUB"},
                "confirmation": {
                    "type": "redirect",
                    "return_url": "https://tiinservice.online/admin",
                },
                "capture": True,
                "description": f"[ADMIN TEST] {tariff['name']}",
                "metadata": {
                    "tg_id": str(ADMIN_TG_ID),
                    "tariff": tariff_id,
                    "vpn_type": "vless",
                    "test_mode": "true",
                },
            },
            str(uuid.uuid4()),
        )
    except Exception as e:
        logger.error(f"Test payment creation error: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)
    finally:
        # Restore production credentials
        Configuration.account_id = YOO_KASSA_SHOP_ID
        Configuration.secret_key = YOO_KASSA_SECRET_KEY

    create_payment(
        payment_id=payment.id,
        tg_id=ADMIN_TG_ID,
        tariff=tariff_id,
        amount=tariff["price"],
        status="pending",
        is_test=True,
    )

    logger.info(f"Admin test payment created: {payment.id}")

    return {
        "payment_id": payment.id,
        "payment_url": payment.confirmation.confirmation_url,
        "tariff": tariff_id,
        "amount": tariff["price"],
    }


@router.get("/test-payment/{payment_id}/result")
async def test_payment_result(payment_id: str):
    """Check whether a test payment was processed and VLESS sub link created."""
    from api.db import get_payment_by_id, execute_query

    payment = get_payment_by_id(payment_id)
    if not payment:
        return JSONResponse({"error": "Payment not found"}, status_code=404)

    result = {
        "payment_id": payment_id,
        "status": payment.get("status"),
        "tariff": payment.get("tariff"),
        "amount": float(payment.get("amount") or 0),
        "created_at": _serialize(payment.get("created_at")),
        "is_test": bool(payment.get("is_test")),
        "vless_registered": False,
        "subscription_link": None,
        "vless_link": None,
        "expires_at": None,
    }

    # Check if VPN key was created for this payment
    vpn_key = execute_query(
        "SELECT client_name, vless_link, subscription_link, expires_at, vpn_type "
        "FROM vpn_keys WHERE payment_id = %s LIMIT 1",
        (payment_id,), fetch='one',
    )

    if vpn_key:
        result["vless_registered"] = True
        result["subscription_link"] = vpn_key.get("subscription_link")
        result["vless_link"] = vpn_key.get("vless_link")
        result["expires_at"] = _serialize(vpn_key.get("expires_at"))
        result["client_name"] = vpn_key.get("client_name")
        result["vpn_type"] = vpn_key.get("vpn_type")

    return result


@router.get("/favicon.png")
async def favicon():
    path = os.path.join(os.path.dirname(__file__), "static", "favicon.png")
    return FileResponse(path, media_type="image/png")


# ─────────────────────────────────────────────
#  Message Log
# ─────────────────────────────────────────────

@router.get("/api/messages")
async def api_message_log(
    request: Request,
    tg_id: int = Query(None),
    source: str = Query(None),
    status: str = Query(None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    """List sent Telegram messages with filters."""
    _require_admin_session(request)
    return {
        "items": admin_db.list_message_log(tg_id=tg_id, source=source, status=status,
                                            limit=limit, offset=offset),
        "counts": admin_db.count_message_log(tg_id=tg_id, source=source, status=status),
    }


@router.get("/api/messages/stats")
async def api_message_log_stats(
    request: Request,
    days: int = Query(7, ge=1, le=90),
):
    """Per-source message stats."""
    _require_admin_session(request)
    return {"sources": admin_db.message_log_source_stats(days=days)}


@router.get("/messages")
async def message_log_page(request: Request):
    """Message log HTML page."""
    _require_admin_session(request)
    return HTMLResponse(_render_message_log_html())


def _render_message_log_html() -> str:
    return """<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Message Log — TIIN Admin</title>
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body { background: #0f1117; color: #e2e8f0; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; font-size: 14px; }
.header { background: #1a1d2e; border-bottom: 1px solid #2d3748; padding: 16px 24px; display: flex; align-items: center; justify-content: space-between; }
.header h1 { font-size: 18px; font-weight: 600; }
.header a { color: #818cf8; text-decoration: none; font-size: 13px; }
.filters { background: #1a1d2e; border-bottom: 1px solid #2d3748; padding: 12px 24px; display: flex; gap: 12px; flex-wrap: wrap; align-items: center; }
.filters label { color: #94a3b8; font-size: 12px; }
.filters input, .filters select { background: #0f1117; border: 1px solid #2d3748; color: #e2e8f0; border-radius: 6px; padding: 6px 10px; font-size: 13px; }
.filters input:focus, .filters select:focus { border-color: #818cf8; outline: none; }
.filters button { background: #6366f1; color: #fff; border: none; border-radius: 6px; padding: 6px 16px; cursor: pointer; font-size: 13px; }
.filters button:hover { background: #4f46e5; }
.stats-row { display: flex; gap: 16px; padding: 12px 24px; background: #151820; border-bottom: 1px solid #2d3748; }
.stat-card { background: #1a1d2e; border: 1px solid #2d3748; border-radius: 8px; padding: 10px 16px; text-align: center; flex: 1; min-width: 100px; }
.stat-card .val { font-size: 20px; font-weight: 700; }
.stat-card .lbl { font-size: 11px; color: #94a3b8; margin-top: 2px; }
.stat-sent .val { color: #34d399; }
.stat-failed .val { color: #f87171; }
.stat-blocked .val { color: #fbbf24; }
.stat-total .val { color: #e2e8f0; }
.table-wrap { overflow-x: auto; padding: 0 24px 24px; }
table { width: 100%; border-collapse: collapse; margin-top: 12px; }
th { text-align: left; padding: 10px 12px; color: #94a3b8; font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.5px; border-bottom: 2px solid #2d3748; }
td { padding: 10px 12px; border-bottom: 1px solid #1e2330; vertical-align: top; }
tr:hover { background: #1a1d2e; }
.badge { display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 11px; font-weight: 600; }
.badge-sent { background: #064e3b; color: #34d399; }
.badge-failed { background: #7f1d1d; color: #f87171; }
.badge-blocked { background: #78350f; color: #fbbf24; }
.source-tag { font-family: monospace; font-size: 11px; color: #818cf8; }
.scenario-tag { font-size: 11px; color: #94a3b8; }
.msg-text { max-width: 300px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; color: #cbd5e1; font-size: 12px; }
.tg-id { font-family: monospace; color: #a78bfb; cursor: pointer; }
.tg-id:hover { text-decoration: underline; }
.time-cell { color: #94a3b8; font-size: 12px; white-space: nowrap; }
.pagination { display: flex; justify-content: center; gap: 8px; padding: 16px; }
.pagination button { background: #1a1d2e; border: 1px solid #2d3748; color: #e2e8f0; border-radius: 6px; padding: 6px 12px; cursor: pointer; }
.pagination button:hover { background: #2d3748; }
.loading { text-align: center; padding: 40px; color: #94a3b8; }
</style>
</head>
<body>
<div class="header">
  <h1>📨 Message Log</h1>
  <a href="/admin">← Back to Dashboard</a>
</div>

<div class="stats-row" id="stats-row">
  <div class="stat-card stat-total"><div class="val" id="stat-total">—</div><div class="lbl">Total</div></div>
  <div class="stat-card stat-sent"><div class="val" id="stat-sent">—</div><div class="lbl">Sent</div></div>
  <div class="stat-card stat-failed"><div class="val" id="stat-failed">—</div><div class="lbl">Failed</div></div>
  <div class="stat-card stat-blocked"><div class="val" id="stat-blocked">—</div><div class="lbl">Blocked</div></div>
</div>

<div class="filters">
  <div><label>TG ID</label><input type="number" id="f-tg_id" placeholder="e.g. 123456" style="width:120px"></div>
  <div><label>Source</label>
    <select id="f-source">
      <option value="">All sources</option>
      <option value="webhook">webhook</option>
      <option value="cron_winback">cron_winback</option>
      <option value="cron_broadcast">cron_broadcast</option>
      <option value="cron_autopay">cron_autopay</option>
      <option value="cron_expiry">cron_expiry</option>
      <option value="admin_broadcast">admin_broadcast</option>
      <option value="admin_send">admin_send</option>
      <option value="admin_notify">admin_notify</option>
      <option value="bot_command">bot_command</option>
      <option value="bot_menu">bot_menu</option>
      <option value="broadcast_script">broadcast_script</option>
      <option value="announcement">announcement</option>
    </select>
  </div>
  <div><label>Status</label>
    <select id="f-status">
      <option value="">All</option>
      <option value="sent">sent</option>
      <option value="failed">failed</option>
      <option value="blocked">blocked</option>
    </select>
  </div>
  <button onclick="loadData()">Filter</button>
  <button onclick="resetFilters()" style="background:#2d3748">Reset</button>
</div>

<div class="table-wrap">
  <table id="msg-table">
    <thead><tr>
      <th>Time</th><th>TG ID</th><th>Name</th><th>Source</th><th>Scenario</th><th>Status</th><th>Message Preview</th><th>Error</th>
    </tr></thead>
    <tbody id="msg-body"><tr><td colspan="8" class="loading">Loading...</td></tr></tbody>
  </table>
</div>

<div class="pagination" id="pagination"></div>

<script>
let offset = 0;
const limit = 50;

function badge(s){ return `<span class="badge badge-${s}">${s}</span>`; }
function esc(s){ const d=document.createElement('div'); d.textContent=s||''; return d.innerHTML; }

async function loadData(){
  offset = 0;
  await fetchData();
}

async function fetchData(){
  const p = new URLSearchParams();
  const t=document.getElementById('f-tg_id').value;
  const s=document.getElementById('f-source').value;
  const st=document.getElementById('f-status').value;
  if(t) p.set('tg_id', t);
  if(s) p.set('source', s);
  if(st) p.set('status', st);
  p.set('limit', limit);
  p.set('offset', offset);
  try{
    const r = await fetch('/admin/api/messages?'+p);
    const d = await r.json();
    document.getElementById('stat-total').textContent = d.counts.total||0;
    document.getElementById('stat-sent').textContent = d.counts.sent||0;
    document.getElementById('stat-failed').textContent = d.counts.failed||0;
    document.getElementById('stat-blocked').textContent = d.counts.blocked||0;
    const tbody = document.getElementById('msg-body');
    if(!d.items.length){ tbody.innerHTML='<tr><td colspan="8" class="loading">No messages found</td></tr>'; }
    else{
      tbody.innerHTML = d.items.map(m=>{
        const ts = m.created_at ? new Date(m.created_at+'Z').toLocaleString('ru-RU') : '';
        const msgPreview = esc((m.message_text||'').substring(0,100));
        const errText = esc((m.error_text||'').substring(0,80));
        return `<tr>
          <td class="time-cell">${ts}</td>
          <td><span class="tg-id" onclick="document.getElementById('f-tg_id').value='${m.tg_id}';loadData()">${m.tg_id}</span></td>
          <td>${esc(m.first_name||'')}</td>
          <td><span class="source-tag">${esc(m.source)}</span></td>
          <td><span class="scenario-tag">${esc(m.scenario||'')}</span></td>
          <td>${badge(m.status)}</td>
          <td><div class="msg-text" title="${esc(m.message_text||'')}">${msgPreview}</div></td>
          <td style="color:#f87171;font-size:12px">${errText}</td>
        </tr>`;
      }).join('');
    }
    // Pagination
    const pg = document.getElementById('pagination');
    pg.innerHTML = '';
    if(offset>0){
      const b=document.createElement('button'); b.textContent='← Prev';
      b.onclick=()=>{offset-=limit; fetchData();}; pg.appendChild(b);
    }
    if(d.items.length===limit){
      const b=document.createElement('button'); b.textContent='Next →';
      b.onclick=()=>{offset+=limit; fetchData();}; pg.appendChild(b);
    }
  }catch(e){ console.error(e); }
}

function resetFilters(){
  document.getElementById('f-tg_id').value='';
  document.getElementById('f-source').value='';
  document.getElementById('f-status').value='';
  loadData();
}

loadData();
</script></body></html>"""



