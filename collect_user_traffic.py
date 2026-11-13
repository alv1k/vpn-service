#!/usr/bin/env python3
"""Collect per-user VPN traffic from all protocols, store in MySQL. Runs every 60 seconds."""
import json
import logging
import os
import re
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone

import mysql.connector

sys.path.insert(0, "/home/alvik/vpn-service")

from awg_api.config import (
    MYSQL_HOST, MYSQL_PORT, MYSQL_USER, MYSQL_PASSWORD, MYSQL_DATABASE,
)
from config import (
    XUI_HOST, XUI_USERNAME, XUI_PASSWORD,
    ADMIN_TG_ID,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)scollect_user_traffic %(levelname)s %(message)s",
)
logger = logging.getLogger("collect_user_traffic")

XUI_DB = "/etc/x-ui/x-ui.db"
ACCESS_LOG = "/var/log/x-ui/access.log"
ALERT_LOG = "/var/log/x-ui/alert.log"
PREV_FILE = "/home/alvik/vpn-service/data/user_traffic_prev.json"
SNAPSHOT_RETENTION_DAYS = 7
DAILY_RETENTION_DAYS = 90

# Coalesce (snapshot interval) in seconds — snapshots within this window are merged
COALESCE_WINDOW = 90

# Speed drop alerting
SPEED_HISTORY_SIZE = 10
MIN_BASELINE_BPS = 5_000_000  # 5 Mbit/s — only alert if user had decent speed
DROP_RATIO = 0.2              # current < 20% of baseline = dropped
MIN_DROPPED_USERS = 2         # alert if N+ users drop simultaneously
MASS_ALERT_COOLDOWN = 1800    # 30 min between mass alerts
USER_ALERT_COOLDOWN = 3600    # 1h between per-user alerts

TOKEN = None

def _get_token():
    global TOKEN
    if TOKEN is None:
        import dotenv
        dotenv.load_dotenv("/home/alvik/vpn-service/.env")
        TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    return TOKEN


def _fmt_bps(bps: float) -> str:
    if bps >= 1_000_000:
        return f"{bps / 1_000_000:.1f} Mbit/s"
    if bps >= 1_000:
        return f"{bps / 1_000:.0f} kbit/s"
    return f"{bps:.0f} bit/s"


def _send_alert(text: str):
    token = _get_token()
    if not token:
        logger.warning("TELEGRAM_BOT_TOKEN not set, skipping alert")
        return
    try:
        import urllib.request
        data = json.dumps({
            "chat_id": ADMIN_TG_ID,
            "text": text,
            "parse_mode": "HTML",
            "disable_notification": False,
        }).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=data,
            headers={"Content-Type": "application/json"},
        )
        resp = urllib.request.urlopen(req, timeout=10)
        logger.info(f"Alert sent: {resp.status}")
    except Exception as e:
        logger.warning(f"Alert send failed: {e}")


def _check_speed_drops(prev: dict):
    now = time.time()
    dropped = []

    for key, data in list(prev.items()):
        if not isinstance(data, dict) or key.startswith("_"):
            continue
        history = data.get("speed_history", [])
        if len(history) < 3:
            continue
        baseline = sum(history) / len(history)
        data["avg_baseline_bps"] = baseline
        current = data.get("speed_bps", 0)
        if baseline > MIN_BASELINE_BPS and current < baseline * DROP_RATIO:
            parts = key.split("::", 1)
            client_name = parts[0] if parts else key
            vpn_type = parts[1] if len(parts) > 1 else "?"
            dropped.append({
                "client_name": client_name,
                "vpn_type": vpn_type,
                "baseline": baseline,
                "current": current,
                "first_name": data.get("first_name", ""),
            })

    if len(dropped) < MIN_DROPPED_USERS:
        return

    last_alert = prev.get("_mass_alert_ts", 0)
    if now - last_alert < MASS_ALERT_COOLDOWN:
        return
    prev["_mass_alert_ts"] = now

    total_baseline = sum(d["baseline"] for d in dropped)
    total_current = sum(d["current"] for d in dropped)
    pct = int((1 - total_current / total_baseline) * 100) if total_baseline > 0 else 0

    proto_counts: dict[str, int] = {}
    for d in dropped:
        proto_counts[d["vpn_type"]] = proto_counts.get(d["vpn_type"], 0) + 1
    proto_line = ", ".join(f"{p} ({c})" for p, c in sorted(proto_counts.items()))

    lines = [
        f"⚠️ Массовая просадка скорости ({len(dropped)} users)",
        f"Протоколы: {proto_line}",
        f"Суммарная: {_fmt_bps(total_baseline)} → {_fmt_bps(total_current)} ({pct}%)",
        "",
    ]
    for d in dropped[:5]:
        lines.append(
            f"• {d['first_name'] or d['client_name']} ({d['vpn_type']}): "
            f"{_fmt_bps(d['baseline'])} → {_fmt_bps(d['current'])}"
        )
    if len(dropped) > 5:
        lines.append(f"... и ещё {len(dropped) - 5}")

    _send_alert("\n".join(lines))
    logger.warning(f"Speed drop alert sent: {len(dropped)} users dropped, total {total_baseline/1e6:.0f}→{total_current/1e6:.0f} Mbit/s")


def _get_mysql():
    return mysql.connector.connect(
        host=MYSQL_HOST, port=MYSQL_PORT,
        user=MYSQL_USER, password=MYSQL_PASSWORD,
        database=MYSQL_DATABASE,
    )


def _get_xui_api():
    from bot_xui.utils import XUIClient
    return XUIClient(XUI_HOST, XUI_USERNAME, XUI_PASSWORD)


def _load_prev():
    if os.path.exists(PREV_FILE):
        try:
            with open(PREV_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def _save_prev(data):
    os.makedirs(os.path.dirname(PREV_FILE), exist_ok=True)
    with open(PREV_FILE, "w") as f:
        json.dump(data, f)


def _parse_log_ips(log_file, protocols, seconds=300):
    """Parse x-ui access log for remote IPs per client (VLESS/Hysteria)."""
    result = {}  # email -> set of IPs
    if not os.path.exists(log_file):
        return result
    cutoff = time.time() - seconds
    try:
        proc = subprocess.run(
            ["tail", "-200", log_file],
            capture_output=True, text=True, timeout=5,
        )
        for line in proc.stdout.strip().split("\n"):
            if "email:" not in line:
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
            ip_match = re.search(r"from (?:tcp:)?(\[?[\d.]+\]?)(?::\d+)?", line)
            if not ip_match:
                continue
            ip = ip_match.group(1).strip("[]")
            if ip.startswith("127.") or ip == "::1":
                continue
            for proto in protocols:
                if f" {proto} " in line or f" {proto}/" in line or line.strip().endswith(proto):
                    email_match = re.search(r"email: (.+?)(?:\s|$)", line)
                    if email_match:
                        email = email_match.group(1).strip()
                        result.setdefault(email, set()).add(ip)
                    break
    except Exception as e:
        logger.debug(f"Log parse error: {e}")
    return result


def _collect_vless_hysteria():
    """Get VLESS/Hysteria client traffic from x-ui API + access log IPs."""
    entries = []
    try:
        xui = _get_xui_api()
        inbounds = xui.get_inbounds()

        # Build inbound map for protocol detection
        inbound_protocols = {}  # inbound_tag -> protocol
        for ib in inbounds:
            proto = ib.get("protocol", "")
            tag = ib.get("tag", ib.get("id", ""))
            inbound_protocols[tag] = proto

        # Collect per-client traffic from all inbounds and record user_inbound_activity
        client_stats = {}  # email -> {up, down, vpn_type, rx, tx}
        inbound_user_stats = []  # list of (email, inbound_id, remark, up, down)
        for ib in inbounds:
            proto = ib.get("protocol", "")
            if proto not in ("vless", "hysteria", "hysteria2"):
                continue
            vpn_t = "hysteria" if ("hysteria" in proto) else "vless"
            ib_id = ib.get("id", 0)
            remark = ib.get("remark", f"Inbound-{ib_id}")
            clients = ib.get("clientStats", ib.get("clients", []))
            for cs in clients:
                email = cs.get("email", "").strip()
                if not email:
                    continue
                if not cs.get("enable", True):
                    continue
                c_up = cs.get("up", 0)
                c_down = cs.get("down", 0)
                if email not in client_stats:
                    client_stats[email] = {"up": 0, "down": 0, "vpn_type": vpn_t, "rx": 0, "tx": 0}
                client_stats[email]["up"] += c_up
                client_stats[email]["down"] += c_down
                if c_up > 0 or c_down > 0:
                    inbound_user_stats.append((email, ib_id, remark, c_up, c_down))

        # Update user_inbound_activity in MySQL
        if inbound_user_stats:
            try:
                conn_ia = _get_mysql()
                cur_ia = conn_ia.cursor()
                now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                for u_email, u_ib_id, u_remark, u_up, u_down in inbound_user_stats:
                    u_tot = u_up + u_down
                    cur_ia.execute("""
                        INSERT INTO user_inbound_activity
                        (email, inbound_id, inbound_remark, connections_count, up_bytes, down_bytes, total_bytes, first_connected_at, last_connected_at)
                        VALUES (%s, %s, %s, 1, %s, %s, %s, %s, %s)
                        ON DUPLICATE KEY UPDATE
                            connections_count = IF(total_bytes < VALUES(total_bytes), connections_count + 1, connections_count),
                            up_bytes = VALUES(up_bytes),
                            down_bytes = VALUES(down_bytes),
                            total_bytes = VALUES(total_bytes),
                            last_connected_at = IF(total_bytes < VALUES(total_bytes), VALUES(last_connected_at), last_connected_at)
                    """, (u_email, u_ib_id, u_remark, u_up, u_down, u_tot, now_str, now_str))
                conn_ia.commit()
                cur_ia.close()
                conn_ia.close()
            except Exception as ex_ia:
                logger.warning(f"Error updating user_inbound_activity: {ex_ia}")

        # Parse access log for IPs
        log_ips = _parse_log_ips(ACCESS_LOG, ["vless", "hysteria"], seconds=300)

        for email, stats in client_stats.items():
            is_online_now = email in log_ips
            ips = list(log_ips.get(email, set()))
            entries.append({
                "client_name": email,
                "vpn_type": stats["vpn_type"],
                "protocol": stats["vpn_type"],
                "ip_count": len(ips),
                "ips": ips,
                "rx_bytes": stats["down"],
                "tx_bytes": stats["up"],
                "total_bytes": stats["down"] + stats["up"],
                "is_online": is_online_now,
            })
    except Exception as e:
        logger.warning(f"VLESS/Hysteria collect error: {e}")
    return entries


def _collect_awg():
    """Get AWG peer traffic from awg show dump."""
    entries = []
    try:
        conn = _get_mysql()
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT id, name, public_key, enabled FROM awg_clients")
        clients = cur.fetchall()
        cur.close()
        conn.close()

        pub_to_client = {c["public_key"]: c for c in clients if c.get("enabled", True)}
        if not pub_to_client:
            return entries

        result = subprocess.run(
            ["awg", "show", "awg0", "dump"],
            capture_output=True, text=True, timeout=5,
        )
        for line in result.stdout.strip().split("\n")[1:]:
            parts = line.split("\t")
            if len(parts) < 7:
                continue
            pub = parts[0]
            client = pub_to_client.get(pub)
            if not client:
                continue
            last_hs = int(parts[4]) if parts[4] != "0" else 0
            rx = int(parts[5]) if parts[5].isdigit() else 0
            tx = int(parts[6]) if parts[6].isdigit() else 0
            is_online = (last_hs > 0 and (time.time() - last_hs) < 300)
            entries.append({
                "client_name": client["name"],
                "vpn_type": "awg",
                "protocol": "awg",
                "ip_count": 1 if is_online else 0,
                "ips": [] if not is_online else [parts[1].split("/")[0] if "/" in parts[1] else parts[1]],
                "rx_bytes": rx,
                "tx_bytes": tx,
                "total_bytes": rx + tx,
                "is_online": is_online,
            })
    except Exception as e:
        logger.warning(f"AWG collect error: {e}")
    return entries


def _resolve_to_users(client_names):
    """Resolve client names to user info via vpn_keys + users tables."""
    if not client_names:
        return {}
    conn = _get_mysql()
    cur = conn.cursor(dictionary=True)
    placeholders = ",".join(["%s"] * len(client_names))
    cur.execute(f"""
        SELECT k.client_name, k.tg_id, k.user_id, k.vpn_type,
               k.expires_at AS key_expires_at,
               COALESCE(NULLIF(u.first_name,''), u.old_first_name) AS first_name,
               u.last_name, u.web_token, u.subscription_until
        FROM vpn_keys k
        LEFT JOIN users u ON (k.tg_id != 0 AND k.tg_id = u.tg_id)
                          OR (k.user_id IS NOT NULL AND k.user_id = u.id)
        WHERE k.client_name IN ({placeholders})
    """, tuple(client_names))
    rows = cur.fetchall()
    cur.close()
    conn.close()

    result = {}
    for r in rows:
        key_exp = r.get("key_expires_at")
        sub_until = r.get("subscription_until")
        expires = None
        if sub_until and hasattr(sub_until, "strftime"):
            expires = sub_until.strftime("%Y-%m-%d")
        elif key_exp and hasattr(key_exp, "strftime"):
            expires = key_exp.strftime("%Y-%m-%d")

        result[r["client_name"]] = {
            "tg_id": r.get("tg_id", 0) or 0,
            "user_id": r.get("user_id", 0) or 0,
            "first_name": r.get("first_name") or "",
            "last_name": r.get("last_name") or "",
            "web_token": r.get("web_token") or "",
            "expires": expires or "",
        }

    # Hysteria fallback to vless base name
    missing_bases = set()
    for name in client_names:
        if name.endswith("_h") and name in result and not result[name].get("tg_id"):
            base = name[:-2]
            if base in result and result[base].get("tg_id"):
                result[name]["tg_id"] = result[base]["tg_id"]
                result[name]["user_id"] = result[base]["user_id"]
                result[name]["first_name"] = result[base]["first_name"]
                result[name]["web_token"] = result[base]["web_token"]
                result[name]["expires"] = result[base]["expires"]
            elif base not in result:
                missing_bases.add(base)

    if missing_bases:
        base_results = _resolve_to_users(list(missing_bases))
        for name in client_names:
            if name.endswith("_h") and name in result and not result[name].get("tg_id"):
                base = name[:-2]
                base_entry = base_results.get(base)
                if base_entry and base_entry.get("tg_id"):
                    result[name]["tg_id"] = base_entry["tg_id"]
                    result[name]["user_id"] = base_entry["user_id"]
                    result[name]["first_name"] = base_entry["first_name"]
                    result[name]["web_token"] = base_entry["web_token"]
                    result[name]["expires"] = base_entry["expires"]

    return result


def _calc_speed(prev_data, key, total_bytes, now):
    """Calculate speed in bytes/sec from previous snapshot."""
    if key in prev_data:
        p = prev_data[key]
        dt = now - p.get("ts", 0)
        if dt > 0:
            delta = total_bytes - p.get("bytes", 0)
            if delta > 0:
                return delta / dt
    return 0.0


def collect():
    """Main collection cycle."""
    now = int(time.time())
    now_dt = datetime.now()
    prev = _load_prev()

    # Collect from all protocols
    all_entries = []
    all_entries.extend(_collect_vless_hysteria())
    all_entries.extend(_collect_awg())

    if not all_entries:
        logger.warning("No entries collected from any protocol")
        return

    # Resolve user info
    all_names = [e["client_name"] for e in all_entries]
    user_info = _resolve_to_users(all_names)

    # Build snapshot records
    conn = _get_mysql()
    cur = conn.cursor()

    inserted = 0
    for entry in all_entries:
        name = entry["client_name"]
        vpn_type = entry["vpn_type"]
        info = user_info.get(name, {})
        tg_id = info.get("tg_id", 0)
        uid = info.get("user_id", 0)
        user_key = f"{name}::{vpn_type}"

        total = entry["total_bytes"]
        speed = _calc_speed(prev, user_key, total, now)
        rx_speed = speed * (entry["rx_bytes"] / total) if total > 0 else 0
        tx_speed = speed * (entry["tx_bytes"] / total) if total > 0 else 0

        try:
            cur.execute("""
                INSERT INTO user_traffic_snapshots
                    (snapshot_at, user_key, tg_id, user_id, client_name, vpn_type, protocol,
                     ip_count, ips_json, rx_bytes, tx_bytes, total_bytes,
                     rx_speed_bps, tx_speed_bps)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    ip_count=VALUES(ip_count), ips_json=VALUES(ips_json),
                    rx_bytes=VALUES(rx_bytes), tx_bytes=VALUES(tx_bytes),
                    total_bytes=VALUES(total_bytes),
                    rx_speed_bps=VALUES(rx_speed_bps), tx_speed_bps=VALUES(tx_speed_bps),
                    tg_id=VALUES(tg_id), user_id=VALUES(user_id)
            """, (
                now_dt.strftime("%Y-%m-%d %H:%M:%S"),
                user_key, tg_id, uid, name, vpn_type, entry["protocol"],
                entry["ip_count"], json.dumps(entry["ips"]) if entry["ips"] else None,
                entry["rx_bytes"], entry["tx_bytes"], total,
                round(rx_speed, 2), round(tx_speed, 2),
            ))
            inserted += cur.rowcount
        except Exception as e:
            logger.warning(f"Insert failed for {user_key}: {e}")

        # Update prev with speed history
        if user_key not in prev:
            prev[user_key] = {}
        prev[user_key].update({"ts": now, "bytes": total, "speed_bps": speed})
        prev[user_key]["first_name"] = info.get("first_name", "")
        if speed > 100_000:  # > 1 Mbit/s — meaningful reading
            if "speed_history" not in prev[user_key]:
                prev[user_key]["speed_history"] = []
            hist = prev[user_key]["speed_history"]
            hist.append(speed)
            if len(hist) > SPEED_HISTORY_SIZE:
                hist.pop(0)

    conn.commit()

    # Check for speed drops
    _check_speed_drops(prev)

    # Cleanup old snapshots (older than 7 days)
    cutoff_snap = (datetime.now() - timedelta(days=SNAPSHOT_RETENTION_DAYS)).strftime("%Y-%m-%d %H:%M:%S")
    cur.execute("DELETE FROM user_traffic_snapshots WHERE snapshot_at < %s", (cutoff_snap,))
    snap_deleted = cur.rowcount

    conn.commit()
    cur.close()
    conn.close()

    # Save prev snapshot
    _save_prev(prev)

    logger.info(f"Snapshots: {inserted} rows updated/inserted, {snap_deleted} old rows deleted, "
                f"prev keys: {len(prev)}")


def aggregate_daily():
    """Aggregate yesterday's snapshots into daily table. Called once per day."""
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    conn = _get_mysql()
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO user_traffic_daily
            (day, user_key, tg_id, user_id, client_name, vpn_type, protocol,
             ip_count_max, ips_all, rx_bytes_total, tx_bytes_total, total_bytes_total,
             rx_speed_max_bps, tx_speed_max_bps, rx_speed_avg_bps, tx_speed_avg_bps,
             snapshots_count)
        SELECT
            day, user_key, tg_id, user_id, client_name, vpn_type, protocol,
            MAX(ip_count) as ip_count_max,
            GROUP_CONCAT(DISTINCT ips_ip) as ips_all,
            SUM(rx_bytes), SUM(tx_bytes), SUM(total_bytes),
            MAX(rx_speed_bps), MAX(tx_speed_bps),
            AVG(rx_speed_bps), AVG(tx_speed_bps),
            COUNT(*) as snapshots_count
        FROM (
            SELECT
                DATE(s.snapshot_at) as day,
                s.user_key, s.tg_id, s.user_id, s.client_name, s.vpn_type, s.protocol,
                s.ip_count, s.rx_bytes, s.tx_bytes, s.total_bytes,
                s.rx_speed_bps, s.tx_speed_bps,
                JSON_EXTRACT(s.ips_json, CONCAT('$[', n.idx, ']')) as ips_ip
            FROM user_traffic_snapshots s
            CROSS JOIN (SELECT 0 idx UNION SELECT 1 UNION SELECT 2 UNION SELECT 3 UNION SELECT 4) n
            WHERE DATE(s.snapshot_at) = %s
        ) expanded
        GROUP BY day, user_key, tg_id, user_id, client_name, vpn_type, protocol
        ON DUPLICATE KEY UPDATE
            ip_count_max=VALUES(ip_count_max), ips_all=VALUES(ips_all),
            rx_bytes_total=VALUES(rx_bytes_total), tx_bytes_total=VALUES(tx_bytes_total),
            total_bytes_total=VALUES(total_bytes_total),
            rx_speed_max_bps=VALUES(rx_speed_max_bps), tx_speed_max_bps=VALUES(tx_speed_max_bps),
            rx_speed_avg_bps=VALUES(rx_speed_avg_bps), tx_speed_avg_bps=VALUES(tx_speed_avg_bps),
            snapshots_count=VALUES(snapshots_count)
    """, (yesterday,))

    conn.commit()
    cur.close()
    conn.close()

    # Cleanup old daily records
    cutoff_daily = (datetime.now() - timedelta(days=DAILY_RETENTION_DAYS)).strftime("%Y-%m-%d")
    conn2 = _get_mysql()
    cur2 = conn2.cursor()
    cur2.execute("DELETE FROM user_traffic_daily WHERE day < %s", (cutoff_daily,))
    daily_deleted = cur2.rowcount
    conn2.commit()
    cur2.close()
    conn2.close()

    logger.info(f"Daily aggregation for {yesterday}: {cur.rowcount} rows, {daily_deleted} old daily rows deleted")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--daily", action="store_true", help="Run daily aggregation instead of snapshot collection")
    parser.add_argument("--test", action="store_true", help="Run once and print collected entries")
    args = parser.parse_args()

    if args.daily:
        aggregate_daily()
    elif args.test:
        # Just collect and print, don't persist
        all_entries = []
        all_entries.extend(_collect_vless_hysteria())
        all_entries.extend(_collect_awg())
        all_names = [e["client_name"] for e in all_entries]
        user_info = _resolve_to_users(all_names)
        for entry in all_entries:
            info = user_info.get(entry["client_name"], {})
            print(json.dumps({
                "client_name": entry["client_name"],
                "vpn_type": entry["vpn_type"],
                "ip_count": entry["ip_count"],
                "rx_bytes": entry["rx_bytes"],
                "tx_bytes": entry["tx_bytes"],
                "total_bytes": entry["total_bytes"],
                "tg_id": info.get("tg_id", 0),
                "first_name": info.get("first_name", ""),
            }, ensure_ascii=False, indent=2))
            print("---")
        print(f"Total: {len(all_entries)} entries")
    else:
        collect()
