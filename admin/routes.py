"""Admin API router — unified panel endpoints for AWG + VLESS + Bot data."""
import asyncio
import json
import logging
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query, Request, HTTPException
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


def _get_online_users() -> list[dict]:
    """Parse xray access log + awg handshakes to find who's online now."""
    online = []
    vless_ips: dict[str, set[str]] = {}   # email -> set of IPs
    vless_ts: dict[str, str] = {}         # email -> latest timestamp

    # VLESS: parse access.log for activity in last 5 minutes
    access_log = "/home/alvik/vpn-service/docker/x-ui-logs/access.log"
    try:
        cutoff = time.time() - 300  # 5 min ago
        result = subprocess.run(
            ["tail", "-500", access_log], capture_output=True, text=True, timeout=5
        )
        for line in result.stdout.strip().split("\n"):
            if "email:" not in line or "127.0.0.1" in line.split("from ")[1][:15] if "from " in line else True:
                continue
            # Parse timestamp: 2026/03/18 09:17:23.354755
            ts_match = re.match(r"(\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2})", line)
            if not ts_match:
                continue
            try:
                ts = datetime.strptime(ts_match.group(1), "%Y/%m/%d %H:%M:%S").replace(tzinfo=timezone.utc)
                if ts.timestamp() < cutoff:
                    continue
            except Exception:
                continue

            # Parse IP
            ip_match = re.search(r"from (?:tcp:)?(\d+\.\d+\.\d+\.\d+)", line)
            ip = ip_match.group(1) if ip_match else "?"

            # Parse email
            email_match = re.search(r"email: (.+)$", line)
            if not email_match:
                continue
            email = email_match.group(1).strip()
            vless_ips.setdefault(email, set()).add(ip)
            vless_ts[email] = ts.strftime("%H:%M:%S")

        for email, ips in vless_ips.items():
            online.append({
                "name": email,
                "ip_count": len(ips),
                "type": "vless",
                "last_seen": vless_ts[email],
            })
    except Exception as e:
        logger.warning(f"Access log parse error: {e}")

    # VLESS: get per-client traffic from x-ui for speed calc
    try:
        xui = _get_xui()
        inbounds = xui.get_inbounds()
        vless_traffic: dict[str, int] = {}  # email -> total bytes
        for ib in inbounds:
            for cs in ib.get("clientStats", []):
                email = cs.get("email", "")
                vless_traffic[email] = vless_traffic.get(email, 0) + cs.get("up", 0) + cs.get("down", 0)
        for u in online:
            if u["type"] == "vless" and u["name"] in vless_traffic:
                speed = _calc_speed(u["name"], "vless", vless_traffic[u["name"]])
                u["speed_mbps"] = _speed_mbps(speed)
    except Exception as e:
        logger.warning(f"VLESS traffic fetch error: {e}")

    # AWG: check last handshake from awg show dump
    try:
        awg_clients = awg_db.list_clients()
        pub_to_name = {c["public_key"]: c["name"] for c in awg_clients}
        result = subprocess.run(
            ["awg", "show", "awg0", "dump"], capture_output=True, text=True, timeout=5
        )
        for line in result.stdout.strip().split("\n")[1:]:  # skip interface line
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
                online.append({
                    "name": name,
                    "ip_count": 1,
                    "type": "awg",
                    "last_seen": datetime.fromtimestamp(last_handshake, tz=timezone.utc).strftime("%H:%M:%S"),
                    "speed_mbps": _speed_mbps(speed),
                })
    except Exception as e:
        logger.warning(f"AWG online parse error: {e}")

    # SoftEther: active sessions
    try:
        from bot_xui.softether import list_sessions
        for s in list_sessions():
            speed = _calc_speed(s["username"], "softether", s.get("transfer_bytes", 0))
            online.append({
                "name": s["username"],
                "ip_count": 1,
                "type": "softether",
                "last_seen": "connected",
                "speed_mbps": _speed_mbps(speed),
            })
    except Exception as e:
        logger.warning(f"SoftEther online parse error: {e}")

    # Enrich with expiration dates and first names
    try:
        names = [u["name"] for u in online]
        info_map = admin_db.get_expiry_by_client_names(names)
        for u in online:
            info = info_map.get(u["name"], {})
            u["expires"] = info.get("expires")
            u["first_name"] = info.get("first_name", "")
            u["web_token"] = info.get("web_token", "")
    except Exception as e:
        logger.warning(f"Expiry lookup error: {e}")

    # Default speed for entries that didn't get it
    for u in online:
        u.setdefault("speed_mbps", 0)

    return online


@router.get("/online")
async def online_users():
    return _get_online_users()


@router.get("/online/stream")
async def online_stream(request: Request):
    """SSE stream — pushes online users only when the list changes."""
    async def event_generator():
        while True:
            if await request.is_disconnected():
                break
            users = _get_online_users()
            yield {"event": "online", "data": json.dumps(users)}
            await asyncio.sleep(10)

    return EventSourceResponse(event_generator())


@router.get("/offline")
async def offline_users():
    """Users with active VPN keys who are NOT currently online."""
    online = _get_online_users()
    online_names = {u["name"] for u in online}

    offline = []

    # ── VLESS: get clients + last_online directly from x-ui SQLite ──
    try:
        import sqlite3 as _sqlite3
        XUI_DB = "/home/alvik/vpn-service/docker/x-ui-data/x-ui.db"
        now_ms = int(time.time() * 1000)

        conn = _sqlite3.connect(f"file:{XUI_DB}?mode=ro", uri=True)
        cur = conn.cursor()

        # last_online per email from client_traffics
        cur.execute("SELECT email, last_online FROM client_traffics WHERE enable = 1")
        last_online_map = {row[0]: row[1] for row in cur.fetchall()}

        # active clients from inbound settings JSON
        cur.execute("SELECT settings FROM inbounds WHERE protocol = 'vless'")
        for (settings_json,) in cur.fetchall():
            clients = json.loads(settings_json).get("clients", [])
            for c in clients:
                email = c.get("email", "")
                if not email or email in online_names:
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

                offline.append({
                    "name": email,
                    "type": "vless",
                    "last_seen": last_str,
                    "last_seen_ts": last_online,
                })
        conn.close()
    except Exception as e:
        logger.warning(f"Offline VLESS error: {e}")

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
            if not name or name in online_names or name not in enabled_names:
                continue
            last_hs = int(parts[4]) if parts[4] != "0" else 0
            last_str = ""
            last_ts = 0
            if last_hs > 0:
                last_str = datetime.fromtimestamp(
                    last_hs, tz=timezone.utc
                ).strftime("%Y-%m-%d %H:%M")
                last_ts = last_hs * 1000  # to ms for consistency

            offline.append({
                "name": name,
                "type": "awg",
                "last_seen": last_str,
                "last_seen_ts": last_ts,
            })
    except Exception as e:
        logger.warning(f"Offline AWG error: {e}")

    # ── SoftEther: all users minus active sessions ──
    try:
        from bot_xui.softether import list_users as se_list_users
        for u in se_list_users():
            uname = u.get("username", "")
            if not uname or uname in online_names:
                continue
            # SoftEther doesn't expose last-login via list_users easily
            offline.append({
                "name": uname,
                "type": "softether",
                "last_seen": "",
                "last_seen_ts": 0,
            })
    except Exception as e:
        logger.warning(f"Offline SoftEther error: {e}")

    # Enrich with first_name and expiry
    try:
        names = [u["name"] for u in offline]
        info_map = admin_db.get_expiry_by_client_names(names)
        for u in offline:
            info = info_map.get(u["name"], {})
            u["expires"] = info.get("expires")
            u["first_name"] = info.get("first_name", "")
            u["web_token"] = info.get("web_token", "")
    except Exception as e:
        logger.warning(f"Offline expiry lookup error: {e}")

    # Sort: most recently seen first, never-seen at the end
    offline.sort(key=lambda u: u.get("last_seen_ts", 0), reverse=True)

    return offline


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
        logger.warning("🧑🏼‍🎨TRY BLOCK reached:")
        xui = _get_xui()
        inbounds = xui.get_inbounds()
        xui_data["inbounds"] = len(inbounds)
        for ib in inbounds:
            settings = json.loads(ib.get("settings", "{}"))
            xui_data["clients"] += len(settings.get("clients", []))
            for cs in ib.get("clientStats", []):
                xui_data["up"] += cs.get("up", 0)
                xui_data["down"] += cs.get("down", 0)
        r = subprocess.run(["docker", "inspect", "-f", "{{.State.Running}}", "x-ui"],
                           capture_output=True, text=True)
        xui_data["running"] = "true" in r.stdout.lower()
    except Exception as e:
        logger.warning("🛀🏼🛀🏼🛀🏼Failed to get XUI stats:")
        logger.warning(f"XUI stats error: {e}")

    # Bot stats
    user_stats = admin_db.count_users()
    pay_stats = admin_db.payment_stats()
    recent = admin_db.recent_payments(10)

    # Extra dashboard data
    protocol_stats = admin_db.protocol_breakdown()
    discount_stats = admin_db.permanent_discount_summary()
    autopay_stats = admin_db.autopay_summary()

    # SoftEther stats
    se_users = []
    se_running = False
    try:
        from bot_xui.softether import list_users as se_list_users
        se_users = se_list_users()
        se_running = True
    except Exception as e:
        logger.warning(f"SoftEther stats error: {e}")

    # MTProto Proxy stats
    proxy_metrics = _parse_mtg_metrics()

    return {
        "awg": {
            "clients_total": len(awg_clients),
            "clients_enabled": awg_enabled,
            "interface_up": awg_up,
        },
        "softether": {
            "users_total": len(se_users),
            "running": se_running,
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
        "users": {
            "total": user_stats["total"],
            "active_subscribers": user_stats["active"],
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


# ── SoftEther ─────────────────────────────────────────────────────────────────

@router.get("/softether/users")
async def softether_users():
    try:
        from bot_xui.softether import list_users
        users = list_users()
        # Merge stored configs from vpn_keys
        try:
            conn = awg_db._get_conn()
            cur = conn.cursor(dictionary=True)
            cur.execute(
                "SELECT client_name, vless_link, vpn_file, tg_id, expires_at, created_at "
                "FROM vpn_keys WHERE vpn_type = 'softether'"
            )
            rows = cur.fetchall()
            cur.close()
            conn.close()
            # Build lookup by client_name (latest entry wins)
            configs = {}
            for row in rows:
                name = row["client_name"]
                if name not in configs or (row["created_at"] and configs[name]["created_at"] and row["created_at"] > configs[name]["created_at"]):
                    cfg = {}
                    if row.get("vless_link"):
                        try:
                            cfg = json.loads(row["vless_link"])
                        except Exception:
                            cfg = {"raw": row["vless_link"]}
                    configs[name] = {
                        "config": cfg,
                        "vpn_file": bool(row.get("vpn_file")),
                        "tg_id": row.get("tg_id"),
                        "db_expires_at": row["expires_at"].isoformat() if row.get("expires_at") else None,
                        "created_at": row["created_at"].isoformat() if row.get("created_at") else None,
                    }
            for u in users:
                db = configs.get(u["username"], {})
                u["config"] = db.get("config")
                u["has_vpn_file"] = db.get("vpn_file", False)
                u["tg_id"] = db.get("tg_id")
                u["created_at"] = db.get("created_at")
        except Exception as e:
            logger.warning(f"Failed to merge SE configs from DB: {e}")
        return users
    except Exception as e:
        logger.error(f"SoftEther users error: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)


@router.delete("/softether/users/{username}")
async def softether_delete_user(username: str):
    try:
        from bot_xui.softether import delete_user
        ok = delete_user(username)
        if ok:
            return {"status": "deleted"}
        return JSONResponse({"error": "Failed to delete"}, status_code=500)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.post("/softether/users/{username}/disable")
async def softether_disable_user(username: str):
    try:
        from bot_xui.softether import disable_user
        ok = disable_user(username)
        if ok:
            return {"status": "disabled"}
        return JSONResponse({"error": "Failed to disable"}, status_code=500)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.get("/softether/users/{username}/config")
async def softether_user_config(username: str):
    """Get stored connection config for a SoftEther user from vpn_keys."""
    try:
        conn = awg_db._get_conn()
        cur = conn.cursor(dictionary=True)
        cur.execute(
            "SELECT vless_link, vpn_file FROM vpn_keys "
            "WHERE client_name = %s AND vpn_type = 'softether' "
            "ORDER BY created_at DESC LIMIT 1",
            (username,),
        )
        row = cur.fetchone()
        cur.close()
        conn.close()
        if not row:
            return JSONResponse({"error": "No stored config for this user"}, status_code=404)
        config_json = row.get("vless_link")  # stores JSON {host, port, hub, username, password}
        vpn_file = row.get("vpn_file")
        config = {}
        if config_json:
            try:
                config = json.loads(config_json)
            except Exception:
                config = {"raw": config_json}
        return {"config": config, "vpn_file": vpn_file}
    except Exception as e:
        logger.error(f"SoftEther config lookup error: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)


@router.post("/softether/users")
async def softether_create_user(request: Request):
    try:
        body = await request.json()
        username = body.get("username", "").strip()
        password = body.get("password", "").strip()
        expiry = body.get("expiry", "").strip()  # YYYY/MM/DD
        if not username or not password:
            return JSONResponse({"error": "username and password required"}, status_code=400)
        from bot_xui.softether import create_user, set_user_expiry
        ok = create_user(username, password)
        if not ok:
            return JSONResponse({"error": "Failed to create user"}, status_code=500)
        if expiry:
            set_user_expiry(username, expiry)
        return {"status": "created", "username": username}
    except Exception as e:
        logger.error(f"SoftEther create error: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)


@router.patch("/softether/users/{username}/password")
async def softether_set_password(username: str, request: Request):
    try:
        body = await request.json()
        password = body.get("password", "").strip()
        if not password:
            return JSONResponse({"error": "password required"}, status_code=400)
        from bot_xui.softether import _run
        _run("UserPasswordSet", username, f"/PASSWORD:{password}")
        # Sync password to DB (vpn_keys.vless_link JSON + regenerate vpn_file)
        try:
            conn = awg_db._get_conn()
            cur = conn.cursor(dictionary=True)
            cur.execute(
                "SELECT id, vless_link FROM vpn_keys "
                "WHERE client_name = %s AND vpn_type = 'softether'",
                (username,),
            )
            for row in cur.fetchall():
                if row.get("vless_link"):
                    cfg = json.loads(row["vless_link"])
                    cfg["password"] = password
                    new_link = json.dumps(cfg)
                    # Regenerate .vpn file from scratch instead of string replace
                    from bot_xui.vpn_factory import _make_softether_vpn_file
                    vpn_file = _make_softether_vpn_file(username, password).getvalue().decode("utf-8")
                    cur.execute(
                        "UPDATE vpn_keys SET vless_link = %s, vpn_file = %s WHERE id = %s",
                        (new_link, vpn_file, row["id"]),
                    )
            conn.commit()
            cur.close()
            conn.close()
        except Exception as e:
            logger.warning(f"Failed to sync SE password to DB: {e}")
        return {"status": "password_updated"}
    except Exception as e:
        return JSONResponse({"error": "Failed to update password"}, status_code=500)


@router.patch("/softether/users/{username}/expiry")
async def softether_set_expiry(username: str, request: Request):
    try:
        body = await request.json()
        expiry = body.get("expiry", "").strip()  # YYYY/MM/DD or "none"
        if not expiry:
            return JSONResponse({"error": "expiry required (YYYY/MM/DD or 'none')"}, status_code=400)
        from bot_xui.softether import set_user_expiry
        ok = set_user_expiry(username, expiry if expiry != "none" else "none")
        if ok:
            return {"status": "expiry_updated"}
        return JSONResponse({"error": "Failed to set expiry"}, status_code=500)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


def _ping_telegram_dc() -> float | None:
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

    # Get online users for speed
    online = _get_online_users()
    online_speed: dict[str, float] = {u["name"]: u.get("speed_mbps", 0) for u in online}

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
    'expired_fresh': "⏰ Ваша подписка недавно истекла.\nПродлите сейчас и получите бесперебойный доступ к VPN!\n💡 Чем длиннее период — тем выгоднее цена за день.",
    'expired_old': "👋 Давно не виделись!\nМы обновили сервис — стало быстрее и стабильнее.\nВозвращайтесь — будем рады! 🎁",
    'test_no_purchase': "👋 Вы пробовали наш тестовый период.\nГотовы к полному доступу? Выберите тариф — подписка с доступом ко всем сайтам 🌐",
    'test_no_connect': "👋 Вы активировали тестовый период, но так и не подключились.\nМы продлили вам доступ на 1 день — попробуйте прямо сейчас!\n📱 Быстрый старт:\n1️⃣ Нажмите Мои конфиги\n2️⃣ Скопируйте ссылку подписки\n3️⃣ Вставьте в приложение\nЕсли что-то не получается — напишите нам 💬",
    'payment_no_config': "⚠️ Мы обнаружили, что ваш платёж был успешным, но VPN конфиг не был создан.\nМы уже разбираемся с этим. Если вопрос не решится — напишите в поддержку 💬",
    'panel_db_mismatch': "⚠️ Обнаружена проблема с вашим конфигом. Мы уже работаем над исправлением.\nЕсли VPN не подключается — напишите в поддержку 💬",
    'never_activated': "👋 Привет!\nВы зарегистрировались, но ещё не попробовали VPN.\nАктивируйте бесплатный тест — это займёт пару минут!\n🔒 Безопасный интернет без ограничений.",
    'recently_inactive': "👋 Мы скучаем!\nЗаметили, что вы давно не заходили. Всё ли в порядке с подключением?\n💡 У нас есть бесплатный прокси для Telegram — работает без VPN.",
}


@router.get("/winback")
async def winback_log():
    rows = admin_db.list_winback_log()
    cleaned = _clean(rows)
    for r in cleaned:
        r["message"] = _WINBACK_MESSAGES.get(r.get("scenario", ""), "")
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


# ── Users ────────────────────────────────────────────────────────────────────

@router.get("/users")
async def users_list(search: str = Query(None), limit: int = Query(100)):
    rows = admin_db.list_users(search=search, limit=limit)
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


# ── Admin page serving ────────────────────────────────────────────────────────

def get_admin_page_route():
    """Return the admin page endpoint for mounting in the app."""
    async def admin_page(request: Request):
        # nginx auth_basic already protects this path — skip session check
        # Pre-create a session cookie so JS doesn't need the API password
        import secrets as _secrets
        from datetime import datetime, timezone
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
                    "return_url": "https://tiinservice.ru/admin",
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
