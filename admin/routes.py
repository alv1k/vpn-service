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

SESSION_MAX_AGE = 86400  # 24h


def _require_admin_session(request: Request):
    """Verify connect.sid session cookie — reuses AWG API session store."""
    from awg_api.main import _sessions
    token = request.cookies.get("connect.sid")
    if not token or token not in _sessions:
        raise HTTPException(status_code=401, detail="Unauthorized")
    from datetime import timezone
    created = _sessions[token]
    if datetime.now(timezone.utc).timestamp() - created > SESSION_MAX_AGE:
        del _sessions[token]
        raise HTTPException(status_code=401, detail="Session expired")


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
    """Make datetime JSON-serializable."""
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, bytes):
        return obj.decode("utf-8", errors="replace")
    return str(obj)


def _clean(rows: list[dict]) -> list[dict]:
    return [{k: _serialize(v) if isinstance(v, (datetime, bytes)) else v
             for k, v in row.items()} for row in rows]


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


@router.get("/finance")
async def finance():
    """Server financials: revenue, costs, profitability."""
    from datetime import datetime as dt

    conn = awg_db._get_conn()
    cur = conn.cursor(dictionary=True)

    # Total revenue
    cur.execute("SELECT COALESCE(SUM(amount),0) as total FROM payments WHERE status='paid'")
    total_revenue = float(cur.fetchone()["total"])

    # First payment date (service start)
    cur.execute("SELECT MIN(created_at) as d FROM payments WHERE status='paid'")
    row = cur.fetchone()
    first_payment = row["d"] if row and row["d"] else None

    # Monthly breakdown
    fmt = "%Y-%m"
    cur.execute(
        "SELECT DATE_FORMAT(created_at, %s) as month,"
        " COUNT(*) as payments, SUM(amount) as revenue"
        " FROM payments WHERE status='paid'"
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
        logger.warning(f"XUI stats error: {e}")

    # Bot stats
    user_stats = admin_db.count_users()
    pay_stats = admin_db.payment_stats()
    recent = admin_db.recent_payments(10)

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

        for ib in inbounds:
            if ib["id"] != inbound_id:
                continue
            settings = json.loads(ib.get("settings", "{}"))
            clients = settings.get("clients", [])

            # Lookup first_name via vpn_keys → users
            emails = [c.get("email", "") for c in clients]
            name_map: dict[str, str] = {}  # email -> first_name
            tgid_map: dict[str, int] = {}  # email -> tg_id
            if emails:
                try:
                    conn = awg_db._get_conn()
                    cur = conn.cursor(dictionary=True)
                    ph = ",".join(["%s"] * len(emails))
                    cur.execute(
                        f"SELECT k.client_name, u.first_name, u.tg_id "
                        f"FROM vpn_keys k JOIN users u ON k.tg_id = u.tg_id "
                        f"WHERE k.client_name IN ({ph})",
                        tuple(emails),
                    )
                    for r in cur.fetchall():
                        name_map[r["client_name"]] = r.get("first_name") or ""
                        tgid_map[r["client_name"]] = r.get("tg_id")
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
        return list_users()
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
        return {"status": "password_updated"}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


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

    return {"total": stats, "today": today}


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
}


@router.get("/winback")
async def winback_log():
    rows = admin_db.list_winback_log()
    cleaned = _clean(rows)
    for r in cleaned:
        r["message"] = _WINBACK_MESSAGES.get(r.get("scenario", ""), "")
    return cleaned


# ── Promocodes ────────────────────────────────────────────────────────────────

@router.get("/promocodes")
async def promocodes_list():
    rows = admin_db.list_promocodes()
    return _clean(rows)


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


# ── Admin page serving ────────────────────────────────────────────────────────

def get_admin_page_route():
    """Return the admin page endpoint for mounting in the app."""
    async def admin_page(request: Request):
        # nginx auth_basic already protects this path — skip session check
        # so the page can load and create its own session via POST /api/session
        html_path = os.path.join(os.path.dirname(__file__), "static", "admin.html")
        with open(html_path) as f:
            html = f.read()
        from awg_api.config import API_PASSWORD
        html = html.replace("{{AWG_PASSWORD}}", API_PASSWORD)
        return HTMLResponse(html)
    return admin_page


@router.get("/favicon.png")
async def favicon():
    path = os.path.join(os.path.dirname(__file__), "static", "favicon.png")
    return FileResponse(path, media_type="image/png")
