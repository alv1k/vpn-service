"""Bot DB queries for admin panel (users, payments, vpn_keys)."""
import logging
import json
import os
import sqlite3
import time
from datetime import datetime

from awg_api.db import _get_conn

logger = logging.getLogger(__name__)


def list_users(search: str = None, limit: int = 100) -> list[dict]:
    conn = _get_conn()
    cur = conn.cursor(dictionary=True)
    if search:
        cur.execute("""
            SELECT id, tg_id, COALESCE(NULLIF(first_name,''), NULLIF(old_first_name,'')) AS first_name,
                   old_first_name, last_name, subscription_until,
                   permanent_discount, referral_count, created_at,
                   test_awg_activated, test_vless_activated, web_token
            FROM users
            WHERE tg_id LIKE %s OR first_name LIKE %s OR last_name LIKE %s OR old_first_name LIKE %s
            ORDER BY created_at DESC LIMIT %s
        """, (f"%{search}%", f"%{search}%", f"%{search}%", f"%{search}%", limit))
    else:
        cur.execute("""
            SELECT id, tg_id, COALESCE(NULLIF(first_name,''), NULLIF(old_first_name,'')) AS first_name,
                   old_first_name, last_name, subscription_until,
                   permanent_discount, referral_count, created_at,
                   test_awg_activated, test_vless_activated, web_token
            FROM users ORDER BY created_at DESC LIMIT %s
        """, (limit,))
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows


def get_users_keys_batch(tg_ids: list[int]) -> dict[int, dict]:
    """Return keys info grouped by tg_id for a batch of users.
    Returns {tg_id: {'active': [...], 'last_expires': datetime}}"""
    if not tg_ids:
        return {}
    conn = _get_conn()
    cur = conn.cursor(dictionary=True)
    placeholders = ",".join(["%s"] * len(tg_ids))

    # All keys for these users
    cur.execute(f"""
        SELECT tg_id, client_name, vpn_type, expires_at, created_at
        FROM vpn_keys
        WHERE tg_id IN ({placeholders})
        ORDER BY created_at DESC
    """, tuple(tg_ids))
    rows = cur.fetchall()
    cur.close()
    conn.close()

    from datetime import datetime
    result: dict[int, dict] = {}
    for r in rows:
        entry = result.setdefault(r["tg_id"], {"active": [], "last_expires": None})
        if r["expires_at"] and r["expires_at"] > datetime.utcnow():
            entry["active"].append(r)
        if entry["last_expires"] is None or (r["expires_at"] and r["expires_at"] > entry["last_expires"]):
            entry["last_expires"] = r["expires_at"]
    return result


def count_users() -> dict:
    conn = _get_conn()
    cur = conn.cursor(dictionary=True)
    cur.execute("""
        SELECT
            COUNT(*) as total,
            SUM(CASE WHEN subscription_until > NOW() THEN 1 ELSE 0 END) as active_sub,
            SUM(CASE WHEN subscription_until IS NULL AND EXISTS (
                SELECT 1 FROM vpn_keys k WHERE k.tg_id = users.tg_id AND k.expires_at > NOW()
            ) THEN 1 ELSE 0 END) as active_key_only
        FROM users
    """)
    row = cur.fetchone()
    cur.close()
    conn.close()
    active_sub = row.get("active_sub", 0) or 0
    active_key = row.get("active_key_only", 0) or 0
    return {
        "total": row.get("total", 0) or 0,
        "active": active_sub + active_key,
        "active_sub": active_sub,
        "active_key_only": active_key,
    }


def get_user_keys(tg_id: int) -> list[dict]:
    conn = _get_conn()
    cur = conn.cursor(dictionary=True)
    cur.execute("""
        SELECT id, tg_id, payment_id, client_id, client_name, client_ip,
               vless_link, expires_at, vpn_type, subscription_link, created_at
        FROM vpn_keys WHERE tg_id = %s ORDER BY created_at DESC
    """, (tg_id,))
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows


def get_user_payments(tg_id: int) -> list[dict]:
    conn = _get_conn()
    cur = conn.cursor(dictionary=True)
    cur.execute("""
        SELECT payment_id, tg_id, tariff, amount, status, vpn_issued, is_test, created_at
        FROM payments WHERE tg_id = %s ORDER BY created_at DESC
    """, (tg_id,))
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows


def recent_payments(limit: int = 20) -> list[dict]:
    conn = _get_conn()
    cur = conn.cursor(dictionary=True)
    cur.execute("""
        SELECT p.payment_id, p.tg_id, p.tariff, p.amount, p.status, p.is_test, p.created_at,
               COALESCE(NULLIF(u.first_name,''), u.old_first_name) AS first_name, u.last_name
        FROM payments p
        LEFT JOIN users u ON p.tg_id = u.tg_id
        ORDER BY p.created_at DESC LIMIT %s
    """, (limit,))
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows


def get_expiry_by_client_names(names: list[str]) -> dict[str, dict]:
    """Return {client_name: {expires, first_name, is_test}} for given client names."""
    if not names:
        return {}
    conn = _get_conn()
    cur = conn.cursor(dictionary=True)
    placeholders = ",".join(["%s"] * len(names))
    cur.execute(f"""
        SELECT k.client_name, k.expires_at AS key_expires_at, u.subscription_until,
               COALESCE(NULLIF(u.first_name,''), u.old_first_name) AS first_name, u.web_token
        FROM vpn_keys k
        JOIN users u ON k.tg_id = u.tg_id
        WHERE k.client_name IN ({placeholders})
    """, tuple(names))
    rows = cur.fetchall()
    cur.close()
    conn.close()
    result = {}
    for r in rows:
        sub = r.get("subscription_until")
        key_exp = r.get("key_expires_at")
        # Determine expiry: prefer user subscription, fall back to key expiry (test keys)
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
            "expires": expires,
            "first_name": r.get("first_name") or "",
            "web_token": r.get("web_token") or "",
            "is_test": is_test,
        }
    return result


def new_users_today() -> list[dict]:
    conn = _get_conn()
    cur = conn.cursor(dictionary=True)
    cur.execute("""
        SELECT id, tg_id, COALESCE(NULLIF(first_name,''), old_first_name) AS first_name, last_name, email, created_at, web_token
        FROM users
        WHERE DATE(CONVERT_TZ(created_at, '+00:00', '+09:00'))
            = DATE(CONVERT_TZ(UTC_TIMESTAMP(), '+00:00', '+09:00'))
        ORDER BY created_at DESC
    """)
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows


def list_winback_log(limit: int = 50) -> list[dict]:
    conn = _get_conn()
    cur = conn.cursor(dictionary=True)
    cur.execute("""
        SELECT w.id, w.tg_id, w.scenario, w.sent_at,
               COALESCE(NULLIF(u.first_name,''), u.old_first_name) AS first_name, u.last_name
        FROM winback_log w
        LEFT JOIN users u ON w.tg_id = u.tg_id
        WHERE w.sent_at >= NOW() - INTERVAL 6 DAY
        ORDER BY w.sent_at DESC LIMIT %s
    """, (limit,))
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows


def winback_effectiveness() -> list[dict]:
    """Per-scenario winback conversion stats.

    For each scenario returns:
      - total_sent: how many messages were sent
      - converted: users who made a payment or activated a test within 7 days
      - conversion_rate: percentage
    """
    conn = _get_conn()
    cur = conn.cursor(dictionary=True)
    cur.execute("""
        SELECT
            w.scenario,
            COUNT(DISTINCT w.tg_id) AS total_sent,
            COUNT(DISTINCT CASE
                WHEN p.id IS NOT NULL OR k.id IS NOT NULL THEN w.tg_id
            END) AS converted
        FROM winback_log w
        LEFT JOIN payments p
            ON p.tg_id = w.tg_id
            AND p.status = 'paid'
            AND p.is_test = 0
            AND p.created_at BETWEEN w.sent_at AND w.sent_at + INTERVAL 7 DAY
        LEFT JOIN vpn_keys k
            ON k.tg_id = w.tg_id
            AND k.created_at BETWEEN w.sent_at AND w.sent_at + INTERVAL 7 DAY
        GROUP BY w.scenario
        ORDER BY converted DESC
    """)
    rows = cur.fetchall()
    cur.close()
    conn.close()
    for r in rows:
        sent = r['total_sent'] or 0
        conv = r['converted'] or 0
        r['conversion_rate'] = round(conv / sent * 100, 1) if sent > 0 else 0
    return rows


def list_promocodes() -> list[dict]:
    conn = _get_conn()
    cur = conn.cursor(dictionary=True)
    cur.execute("""
        SELECT id, code, type, value, max_uses, used_count,
               per_user_limit, expires_at, is_active, created_at
        FROM promocodes ORDER BY created_at DESC
    """)
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows


def payment_stats() -> dict:
    conn = _get_conn()
    cur = conn.cursor(dictionary=True)
    cur.execute("""
        SELECT
            COUNT(*) as total,
            SUM(CASE WHEN status='paid' AND is_test=0 THEN 1 ELSE 0 END) as paid,
            SUM(CASE WHEN status='paid' AND is_test=0 THEN amount ELSE 0 END) as revenue
        FROM payments
    """)
    row = cur.fetchone()
    cur.close()
    conn.close()
    return row or {"total": 0, "paid": 0, "revenue": 0}


def autopay_failures(limit: int = 50) -> list[dict]:
    conn = _get_conn()
    cur = conn.cursor(dictionary=True)
    cur.execute("""
        SELECT a.tg_id, a.user_id, a.tariff, a.amount, a.payment_id,
               a.status, a.error_message, a.created_at,
               COALESCE(NULLIF(u.first_name,''), u.old_first_name) AS first_name
        FROM autopay_log a
        LEFT JOIN users u ON a.user_id = u.id
        ORDER BY a.created_at DESC LIMIT %s
    """, (limit,))
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows


def referral_network(limit: int = 50) -> list[dict]:
    conn = _get_conn()
    cur = conn.cursor(dictionary=True)
    cur.execute("""
        SELECT u.id, u.tg_id,
               COALESCE(NULLIF(u.first_name,''), u.old_first_name) AS first_name,
               u.referred_by, u.created_at,
               COALESCE(NULLIF(r.first_name,''), r.old_first_name) AS referrer_name,
               r.tg_id AS referrer_tg_id
        FROM users u
        JOIN users r ON u.referred_by = r.id
        ORDER BY u.created_at DESC LIMIT %s
    """, (limit,))
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows


def protocol_breakdown() -> list[dict]:
    conn = _get_conn()
    cur = conn.cursor(dictionary=True)
    cur.execute("""
        SELECT k.vpn_type, COUNT(*) AS count
        FROM vpn_keys k
        JOIN users u ON (k.tg_id = u.tg_id AND k.tg_id != 0) OR (k.user_id = u.id)
        WHERE u.subscription_until > NOW()
        GROUP BY k.vpn_type
    """)
    rows = cur.fetchall()
    cur.close()
    conn.close()
    
    # Ensure all expected protocols are present, even if count is 0
    known_protocols = ['vless', 'awg', 'hysteria']
    result_map = {r['vpn_type']: r['count'] for r in rows}
    return [{"vpn_type": p, "count": result_map.get(p, 0)} for p in known_protocols]


def failed_pending_payments(limit: int = 30) -> list[dict]:
    conn = _get_conn()
    cur = conn.cursor(dictionary=True)
    cur.execute("""
        SELECT p.payment_id, p.tg_id, p.tariff, p.amount, p.status, p.created_at,
               COALESCE(NULLIF(u.first_name,''), u.old_first_name) AS first_name
        FROM payments p
        LEFT JOIN users u ON p.tg_id = u.tg_id
        WHERE p.status != 'paid'
        ORDER BY p.created_at DESC LIMIT %s
    """, (limit,))
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows


def permanent_discount_summary() -> list[dict]:
    conn = _get_conn()
    cur = conn.cursor(dictionary=True)
    cur.execute("""
        SELECT permanent_discount AS discount, COUNT(*) AS user_count
        FROM users WHERE permanent_discount > 0
        GROUP BY permanent_discount ORDER BY permanent_discount
    """)
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows


def autopay_summary() -> dict:
    conn = _get_conn()
    cur = conn.cursor(dictionary=True)
    cur.execute("""
        SELECT
            SUM(autopay_enabled = 1) AS enabled,
            SUM(autopay_enabled = 1 AND payment_method_id IS NOT NULL) AS with_method
        FROM users
    """)
    row = cur.fetchone()
    cur.close()
    conn.close()
    return row or {"enabled": 0, "with_method": 0}


def promo_usage_details(limit: int = 50) -> list[dict]:
    conn = _get_conn()
    cur = conn.cursor(dictionary=True)
    cur.execute("""
        SELECT pu.id, pu.tg_id, p.code, p.type, p.value, pu.used_at,
               COALESCE(NULLIF(u.first_name,''), u.old_first_name) AS first_name
        FROM promocode_usages pu
        JOIN promocodes p ON pu.promocode_id = p.id
        LEFT JOIN users u ON pu.tg_id = u.tg_id
        ORDER BY pu.used_at DESC LIMIT %s
    """, (limit,))
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows


def test_to_paid_by_protocol() -> list[dict]:
    conn = _get_conn()
    cur = conn.cursor(dictionary=True)
    cur.execute("""
        SELECT
            CASE
                WHEN test_vless_activated = 1 AND test_awg_activated = 0 THEN 'vless_only'
                WHEN test_awg_activated = 1 AND test_vless_activated = 0 THEN 'awg_only'
                WHEN test_vless_activated = 1 AND test_awg_activated = 1 THEN 'both'
                ELSE 'none'
            END AS test_protocol,
            COUNT(*) AS users,
            SUM(CASE WHEN EXISTS (
                SELECT 1 FROM payments p WHERE p.tg_id = users.tg_id AND p.status='paid' AND p.is_test=0 AND p.tg_id != 0
            ) THEN 1 ELSE 0 END) AS converted
        FROM users
        GROUP BY test_protocol
    """)
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows


def list_message_log(tg_id: int = None, source: str = None, status: str = None,
                     limit: int = 100, offset: int = 0) -> list[dict]:
    """Query message_log with optional filters."""
    conn = _get_conn()
    cur = conn.cursor(dictionary=True)
    conditions = []
    params = []
    if tg_id:
        conditions.append("m.tg_id = %s")
        params.append(tg_id)
    if source:
        conditions.append("m.source = %s")
        params.append(source)
    if status:
        conditions.append("m.status = %s")
        params.append(status)
    where = "WHERE " + " AND ".join(conditions) if conditions else ""
    cur.execute(f"""
        SELECT m.id, m.tg_id, m.source, m.scenario, m.message_text, m.status,
               m.error_text, m.created_at,
               COALESCE(NULLIF(u.first_name,''), u.old_first_name) AS first_name
        FROM message_log m
        LEFT JOIN users u ON m.tg_id = u.tg_id
        {where}
        ORDER BY m.created_at DESC
        LIMIT %s OFFSET %s
    """, tuple(params) + (limit, offset))
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows


def count_message_log(tg_id: int = None, source: str = None, status: str = None) -> dict:
    """Count message_log with optional filters, grouped by status."""
    conn = _get_conn()
    cur = conn.cursor(dictionary=True)
    conditions = []
    params = []
    if tg_id:
        conditions.append("tg_id = %s")
        params.append(tg_id)
    if source:
        conditions.append("source = %s")
        params.append(source)
    if status:
        conditions.append("status = %s")
        params.append(status)
    where = "WHERE " + " AND ".join(conditions) if conditions else ""
    cur.execute(f"""
        SELECT status, COUNT(*) AS cnt
        FROM message_log
        {where}
        GROUP BY status
    """, tuple(params))
    rows = cur.fetchall()
    cur.close()
    conn.close()
    result = {r['status']: r['cnt'] for r in rows}
    result['total'] = sum(result.values())
    return result


def message_log_source_stats(days: int = 7) -> list[dict]:
    """Per-source message stats for the last N days."""
    conn = _get_conn()
    cur = conn.cursor(dictionary=True)
    cur.execute("""
        SELECT source,
               COUNT(*) AS total,
               SUM(CASE WHEN status = 'sent' THEN 1 ELSE 0 END) AS sent,
               SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failed,
               SUM(CASE WHEN status = 'blocked' THEN 1 ELSE 0 END) AS blocked
        FROM message_log
        WHERE created_at >= NOW() - INTERVAL %s DAY
        GROUP BY source
        ORDER BY total DESC
    """, (days,))
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows


def list_webpage_events(limit: int = 100, event_type: str = None, web_token: str = None) -> list[dict]:
    conn = _get_conn()
    cur = conn.cursor(dictionary=True)
    query = "SELECT * FROM webpage_events WHERE 1=1"
    params = []
    if event_type:
        query += " AND event_type = %s"
        params.append(event_type)
    if web_token:
        query += " AND web_token = %s"
        params.append(web_token)
    query += " ORDER BY created_at DESC LIMIT %s"
    params.append(limit)
    cur.execute(query, tuple(params))
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows


def webpage_events_stats(days: int = 7) -> list[dict]:
    conn = _get_conn()
    cur = conn.cursor(dictionary=True)
    cur.execute("""
        SELECT event_type, COUNT(*) as cnt
        FROM webpage_events
        WHERE created_at >= NOW() - INTERVAL %s DAY
        GROUP BY event_type
        ORDER BY cnt DESC
    """, (days,))
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows


def webpage_active_users_last_24h() -> list[dict]:
    conn = _get_conn()
    cur = conn.cursor(dictionary=True)
    cur.execute("""
        SELECT
            u.tg_id,
            u.first_name,
            u.last_name,
            u.web_token,
            UNIX_TIMESTAMP(w.last_event) AS last_event_ts,
            w.event_count
        FROM (
            SELECT
                tg_id,
                MAX(created_at) AS last_event,
                COUNT(*) AS event_count
            FROM webpage_events
            WHERE tg_id IS NOT NULL
              AND created_at >= NOW() - INTERVAL 24 HOUR
            GROUP BY tg_id
        ) w
        JOIN users u ON u.tg_id = w.tg_id
        ORDER BY w.last_event DESC
    """)
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows


def webpage_visitor_journey(web_token: str, hours: int = 24) -> list[dict]:
    conn = _get_conn()
    cur = conn.cursor(dictionary=True)
    cur.execute("""
        SELECT event_type, element_id, element_text, extra_data, ip, user_agent, created_at
        FROM webpage_events
        WHERE web_token = %s
          AND created_at >= NOW() - INTERVAL %s HOUR
        ORDER BY created_at ASC
    """, (web_token, hours))
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows


def get_user_delete_preview(tg_id: int) -> dict:
    """Collect all user data that would be deleted for a given tg_id."""
    conn = _get_conn()
    cur = conn.cursor(dictionary=True)

    # User info
    cur.execute("SELECT * FROM users WHERE tg_id = %s", (tg_id,))
    user = cur.fetchone()

    if not user:
        cur.close()
        conn.close()
        return {"found": False}

    # VPN keys
    cur.execute("SELECT id, client_name, vpn_type, expires_at, created_at FROM vpn_keys WHERE tg_id = %s ORDER BY created_at DESC", (tg_id,))
    vpn_keys = cur.fetchall()

    # Payments (last 10)
    cur.execute("SELECT payment_id, tariff, amount, status, is_test, created_at FROM payments WHERE tg_id = %s ORDER BY created_at DESC LIMIT 10", (tg_id,))
    payments = cur.fetchall()

    # AWG clients by name pattern
    client_name = f"tiin_{tg_id}"
    cur.execute("SELECT id, name, address, enabled FROM awg_clients WHERE name = %s", (client_name,))
    awg_clients = cur.fetchall()

    # Related record counts
    counts = {}
    for table, condition in [
        ("autopay_log", "tg_id = %s"),
        ("message_log", "tg_id = %s"),
        ("promocode_usages", "tg_id = %s"),
        ("promo_activations", "tg_id = %s"),
        ("winback_log", "tg_id = %s"),
        ("webpage_events", "tg_id = %s"),
    ]:
        cur.execute(f"SELECT COUNT(*) AS cnt FROM {table} WHERE {condition}", (tg_id,))
        counts[table] = cur.fetchone()["cnt"]

    # auth_sessions count (via user_id)
    if user.get("id"):
        cur.execute("SELECT COUNT(*) AS cnt FROM auth_sessions WHERE user_id = %s", (user["id"],))
        counts["auth_sessions"] = cur.fetchone()["cnt"]

    cur.close()
    conn.close()

    return {
        "found": True,
        "user": {
            "id": user["id"],
            "tg_id": user["tg_id"],
            "first_name": user.get("first_name") or user.get("old_first_name") or "",
            "email": user.get("email") or "",
            "subscription_until": str(user.get("subscription_until") or "") if user.get("subscription_until") else None,
            "created_at": str(user.get("created_at") or "") if user.get("created_at") else None,
        },
        "vpn_keys": vpn_keys,
        "payments": payments,
        "awg_clients": awg_clients,
        "client_name": client_name,
        "related_counts": counts,
    }


def delete_user_data(tg_id: int) -> dict:
    """Fully delete all user data across all tables. Returns deletion stats."""
    conn = _get_conn()
    cur = conn.cursor(dictionary=True)

    # Get user info needed for deletion (user_id for auth_sessions)
    cur.execute("SELECT id FROM users WHERE tg_id = %s", (tg_id,))
    user_row = cur.fetchone()
    user_id = user_row["id"] if user_row else None

    deleted = {}

    # 1. vpn_keys
    cur.execute("DELETE FROM vpn_keys WHERE tg_id = %s", (tg_id,))
    deleted["vpn_keys"] = cur.rowcount

    # 2. payments
    cur.execute("DELETE FROM payments WHERE tg_id = %s", (tg_id,))
    deleted["payments"] = cur.rowcount

    # 3. autopay_log
    cur.execute("DELETE FROM autopay_log WHERE tg_id = %s", (tg_id,))
    deleted["autopay_log"] = cur.rowcount

    # 4. message_log
    cur.execute("DELETE FROM message_log WHERE tg_id = %s", (tg_id,))
    deleted["message_log"] = cur.rowcount

    # 5. promocode_usages
    cur.execute("DELETE FROM promocode_usages WHERE tg_id = %s", (tg_id,))
    deleted["promocode_usages"] = cur.rowcount

    # 6. promo_activations
    cur.execute("DELETE FROM promo_activations WHERE tg_id = %s", (tg_id,))
    deleted["promo_activations"] = cur.rowcount

    # 7. winback_log
    cur.execute("DELETE FROM winback_log WHERE tg_id = %s", (tg_id,))
    deleted["winback_log"] = cur.rowcount

    # 8. webpage_events
    cur.execute("DELETE FROM webpage_events WHERE tg_id = %s", (tg_id,))
    deleted["webpage_events"] = cur.rowcount

    # 9. auth_sessions (by user_id)
    if user_id:
        cur.execute("DELETE FROM auth_sessions WHERE user_id = %s", (user_id,))
        deleted["auth_sessions"] = cur.rowcount

    # 10. users (last)
    cur.execute("DELETE FROM users WHERE tg_id = %s", (tg_id,))
    deleted["users"] = cur.rowcount

    conn.commit()
    cur.close()
    conn.close()

    return deleted


def get_monitoring_stats() -> dict:
    """Fetch monitoring data for admin panel (traffic stats + instagram probe stats)."""
    import sqlite3

    # Получаем актуальные активные инбаунды из 3x-ui + awg0
    active_inbounds = {}
    try:
        conn_xui = sqlite3.connect("/etc/x-ui/x-ui.db")
        c_xui = conn_xui.cursor()
        for row in c_xui.execute("SELECT id, remark, port, protocol FROM inbounds WHERE enable = 1"):
            active_inbounds[row[0]] = {
                "remark": row[1] or f"Inbound-{row[0]}",
                "port": row[2],
                "protocol": row[3]
            }
        conn_xui.close()
    except Exception as e:
        logger.error(f"Error fetching x-ui inbounds for monitoring: {e}")

    active_inbounds[100] = {"remark": "AmneziaWG-awg0", "port": 51888, "protocol": "amneziawg"}

    conn = _get_conn()
    cur = conn.cursor(dictionary=True)

    # 1. Latest snapshot per active inbound
    cur.execute("""
        SELECT s1.inbound_id, s1.remark, s1.port, s1.protocol, s1.up_bytes, s1.down_bytes,
               s1.total_bytes, s1.up_speed_bps, s1.down_speed_bps, s1.created_at
        FROM inbound_traffic_stats s1
        INNER JOIN (
            SELECT inbound_id, MAX(id) as max_id
            FROM inbound_traffic_stats
            GROUP BY inbound_id
        ) s2 ON s1.inbound_id = s2.inbound_id AND s1.id = s2.max_id
        ORDER BY s1.inbound_id ASC
    """)
    raw_current = cur.fetchall()
    latest_map = {r["inbound_id"]: r for r in raw_current}
    current_inbounds = []
    for ib_id, meta in active_inbounds.items():
        if ib_id in latest_map:
            item = dict(latest_map[ib_id])
            item["remark"] = meta["remark"]
            item["port"] = meta["port"]
            item["protocol"] = meta["protocol"]
            current_inbounds.append(item)

    # 2. Hourly averages / max for the last 24h per active inbound
    cur.execute("""
        SELECT inbound_id,
               COUNT(*) as snapshots,
               ROUND(AVG(down_speed_bps)/1024/1024, 4) as avg_down_mbps,
               ROUND(MAX(down_speed_bps)/1024/1024, 4) as max_down_mbps,
               ROUND(AVG(up_speed_bps)/1024/1024, 4) as avg_up_mbps,
               ROUND(MAX(up_speed_bps)/1024/1024, 4) as max_up_mbps
        FROM inbound_traffic_stats
        WHERE created_at >= NOW() - INTERVAL 24 HOUR
        GROUP BY inbound_id
    """)
    stats_map = {r["inbound_id"]: r for r in cur.fetchall()}
    summary_24h = []
    for ib_id, meta in active_inbounds.items():
        st = stats_map.get(ib_id, {
            "snapshots": 0, "avg_down_mbps": 0.0, "max_down_mbps": 0.0,
            "avg_up_mbps": 0.0, "max_up_mbps": 0.0
        })
        summary_24h.append({
            "inbound_id": ib_id,
            "remark": meta["remark"],
            "port": meta["port"],
            "protocol": meta["protocol"],
            "snapshots": st["snapshots"],
            "avg_down_mbps": float(st["avg_down_mbps"]),
            "max_down_mbps": float(st["max_down_mbps"]),
            "avg_up_mbps": float(st["avg_up_mbps"]),
            "max_up_mbps": float(st["max_up_mbps"]),
        })

    summary_24h.sort(key=lambda x: x["max_down_mbps"], reverse=True)

    cur.close()
    conn.close()

    return {
        "current_inbounds": current_inbounds,
        "summary_24h": summary_24h,
    }


def subscription_status_summary() -> dict:
    """Return expiring_soon (within 3 days) and recently_expired (within 30 days) user list and counts.
    Considers minimum of users.subscription_until and 3x-ui /etc/x-ui/x-ui.db client expiryTime."""
    conn = _get_conn()
    cur = conn.cursor(dictionary=True)

    # 1. Fetch users from MySQL
    cur.execute("""
        SELECT u.id, u.tg_id, COALESCE(NULLIF(u.first_name,''), NULLIF(u.old_first_name,'')) AS first_name,
               u.last_name, u.web_token, u.subscription_until
        FROM users u
    """)
    user_rows = cur.fetchall()
    cur.close()
    conn.close()

    users_map = {r["tg_id"]: r for r in user_rows if r.get("tg_id")}

    # 2. Fetch min active expiryTime per tg_id from 3x-ui SQLite db
    xui_expires = {}
    import sqlite3
    try:
        xconn = sqlite3.connect('/etc/x-ui/x-ui.db')
        xcur = xconn.cursor()
        xcur.execute("SELECT settings FROM inbounds")
        inbound_rows = xcur.fetchall()
        xconn.close()
        for irow in inbound_rows:
            try:
                sett = json.loads(irow[0])
                for cl in sett.get("clients", []):
                    tg_id = cl.get("tgId")
                    exp_ms = cl.get("expiryTime", 0)
                    if tg_id and exp_ms and exp_ms > 0:
                        if tg_id not in xui_expires or exp_ms < xui_expires[tg_id]:
                            xui_expires[tg_id] = exp_ms
            except Exception:
                pass
    except Exception as e:
        logger.warning(f"Failed to read x-ui.db for expiry: {e}")

    now = datetime.now()
    expiring_soon = []
    recently_expired = []

    for tg_id, u in users_map.items():
        sub = u.get("subscription_until")
        xui_ms = xui_expires.get(tg_id)
        xui_dt = datetime.utcfromtimestamp(xui_ms / 1000.0) if xui_ms else None

        # Effective expiry = LEAST of subscription_until and 3x-ui client expiryTime
        dates = []
        if sub and isinstance(sub, datetime):
            dates.append(sub)
        if xui_dt:
            dates.append(xui_dt)

        if not dates:
            continue

        eff = min(dates)

        diff_seconds = (eff - now).total_seconds()
        hours = int(diff_seconds / 3600)

        entry = {
            "id": u["id"],
            "tg_id": u["tg_id"],
            "first_name": u["first_name"],
            "last_name": u["last_name"],
            "subscription_until": eff.strftime("%Y-%m-%d %H:%M:%S"),
            "web_token": u["web_token"],
        }

        if 0 <= hours <= 72:
            entry["hours_left"] = hours
            expiring_soon.append(entry)
        elif -720 <= hours < 0:
            entry["hours_ago"] = abs(hours)
            recently_expired.append(entry)

    expiring_soon.sort(key=lambda x: x.get("hours_left", 0))
    recently_expired.sort(key=lambda x: x.get("hours_ago", 0))

    return {
        "expiring_soon": expiring_soon,
        "recently_expired": recently_expired,
        "expiring_soon_count": len(expiring_soon),
        "recently_expired_count": len(recently_expired),
    }


def get_inbounds_popularity_stats(days: int = 3) -> dict:
    """Fetch all inbounds with SNI, transport parameters and traffic deltas for the specified period."""
    import json
    xui_db_path = "/etc/x-ui/x-ui.db"
    now_ts = int(time.time() * 1000)
    period_start_ts = now_ts - (days * 86400 * 1000)

    inbounds_list = []

    # 1. Parse /etc/x-ui/x-ui.db
    if os.path.exists(xui_db_path):
        try:
            xconn = sqlite3.connect(xui_db_path)
            xcur = xconn.cursor()

            # Online stats from client_traffics
            online_recent_map = {} # inbound_id -> count of clients online in period
            try:
                xcur.execute("""
                    SELECT inbound_id, COUNT(DISTINCT email)
                    FROM client_traffics
                    WHERE last_online >= ?
                    GROUP BY inbound_id
                """, (period_start_ts,))
                for row in xcur.fetchall():
                    if row[0]:
                        online_recent_map[row[0]] = row[1]
            except Exception as e:
                logger.warning(f"Error querying client_traffics: {e}")

            xcur.execute("SELECT id, remark, port, protocol, settings, stream_settings, enable, up, down FROM inbounds")
            for row in xcur.fetchall():
                ib_id, remark, port, protocol, settings_str, stream_str, enable, ib_up, ib_down = row
                
                settings = {}
                if settings_str:
                    try:
                        settings = json.loads(settings_str)
                    except Exception:
                        pass
                
                stream = {}
                if stream_str:
                    try:
                        stream = json.loads(stream_str)
                    except Exception:
                        pass

                clients = settings.get("clients", [])
                total_clients = len(clients)
                active_clients = 0
                for cl in clients:
                    exp = cl.get("expiryTime", 0)
                    if cl.get("enable", True) and (exp == 0 or exp > now_ts):
                        active_clients += 1

                net = stream.get("network", "tcp")
                sec = stream.get("security", "none")
                
                sni_list = []
                target = ""
                fingerprint = ""
                path = ""
                mode = ""
                
                if sec == "reality":
                    rs = stream.get("realitySettings", {})
                    sni_list = rs.get("serverNames", [])
                    target = rs.get("target", "")
                    fingerprint = rs.get("settings", {}).get("fingerprint", "")
                elif sec == "tls":
                    ts = stream.get("tlsSettings", {})
                    if ts.get("serverName"):
                        sni_list = [ts.get("serverName")]
                
                if net == "xhttp":
                    xs = stream.get("xhttpSettings", {})
                    path = xs.get("path", "")
                    mode = xs.get("mode", "")
                    if not sni_list and xs.get("host"):
                        sni_list = [xs.get("host")]
                elif net == "ws":
                    ws = stream.get("wsSettings", {})
                    path = ws.get("path", "")
                    if not sni_list and ws.get("headers", {}).get("Host"):
                        sni_list = [ws.get("headers", {}).get("Host")]
                elif net == "grpc":
                    gs = stream.get("grpcSettings", {})
                    path = gs.get("serviceName", "")

                inbounds_list.append({
                    "id": ib_id,
                    "remark": remark or f"Inbound #{ib_id}",
                    "port": port,
                    "protocol": protocol,
                    "network": net,
                    "security": sec,
                    "sni": sni_list,
                    "target": target,
                    "fingerprint": fingerprint,
                    "path": path,
                    "mode": mode,
                    "enable": bool(enable),
                    "total_clients": total_clients,
                    "active_clients": active_clients,
                    "online_in_period": online_recent_map.get(ib_id, 0),
                    "lifetime_up": ib_up or 0,
                    "lifetime_down": ib_down or 0,
                })
            xconn.close()
        except Exception as e:
            logger.error(f"Failed to read x-ui.db: {e}")

    # 2. Add AmneziaWG (id=100) if active
    awg_rx_path = "/sys/class/net/awg0/statistics/rx_bytes"
    awg_tx_path = "/sys/class/net/awg0/statistics/tx_bytes"
    if os.path.exists(awg_rx_path) and os.path.exists(awg_tx_path):
        try:
            # count awg clients from mysql or awg config
            awg_clients_cnt = 0
            try:
                conn_awg = _get_conn()
                c_cur = conn_awg.cursor(dictionary=True)
                c_cur.execute("SELECT COUNT(*) as cnt FROM users WHERE awg_pubkey IS NOT NULL AND awg_pubkey != '' AND is_active = 1")
                r = c_cur.fetchone()
                if r:
                    awg_clients_cnt = r["cnt"]
                c_cur.close()
                conn_awg.close()
            except Exception:
                pass

            inbounds_list.append({
                "id": 100,
                "remark": "AmneziaWG-awg0",
                "port": 51888,
                "protocol": "amneziawg",
                "network": "udp",
                "security": "awg-obfs",
                "sni": ["AWG (H1-H4/S1-S2)"],
                "target": "awg0 kernel",
                "fingerprint": "AmneziaWG 2.0",
                "path": "",
                "mode": "",
                "enable": True,
                "total_clients": awg_clients_cnt,
                "active_clients": awg_clients_cnt,
                "online_in_period": awg_clients_cnt,
                "lifetime_up": 0,
                "lifetime_down": 0,
            })
        except Exception as e:
            logger.warning(f"Error adding AWG inbound: {e}")

    # 3. Query MySQL inbound_traffic_stats for the requested period (days)
    traffic_map = {}
    total_period_traffic = 0

    try:
        conn = _get_conn()
        cur = conn.cursor(dictionary=True)
        cur.execute("""
            SELECT inbound_id,
                   MAX(up_bytes) - MIN(up_bytes) as up_diff,
                   MAX(down_bytes) - MIN(down_bytes) as down_diff,
                   MAX(total_bytes) - MIN(total_bytes) as total_diff,
                   MAX(up_speed_bps) as max_up_speed,
                   MAX(down_speed_bps) as max_down_speed,
                   AVG(up_speed_bps) as avg_up_speed,
                   AVG(down_speed_bps) as avg_down_speed
            FROM inbound_traffic_stats
            WHERE created_at >= NOW() - INTERVAL %s DAY
            GROUP BY inbound_id
        """, (days,))
        
        for row in cur.fetchall():
            ib_id = row["inbound_id"]
            up_d = max(0, row["up_diff"] or 0)
            down_d = max(0, row["down_diff"] or 0)
            tot_d = max(0, row["total_diff"] or 0)
            if tot_d == 0 and (up_d > 0 or down_d > 0):
                tot_d = up_d + down_d

            traffic_map[ib_id] = {
                "up_bytes": up_d,
                "down_bytes": down_d,
                "total_bytes": tot_d,
                "max_up_speed_bps": row["max_up_speed"] or 0,
                "max_down_speed_bps": row["max_down_speed"] or 0,
                "avg_up_speed_bps": float(row["avg_up_speed"] or 0),
                "avg_down_speed_bps": float(row["avg_down_speed"] or 0),
            }
            total_period_traffic += tot_d

        cur.close()
        conn.close()
    except Exception as e:
        logger.error(f"Error querying traffic stats for {days} days: {e}")

    # 4. Query user_inbound_activity details for each inbound
    inbound_users_map = {}
    try:
        conn_u = _get_conn()
        cur_u = conn_u.cursor(dictionary=True)
        cur_u.execute("""
            SELECT a.email, a.inbound_id, a.connections_count, a.up_bytes, a.down_bytes, a.total_bytes,
                   a.first_connected_at, a.last_connected_at,
                   COALESCE(u.tg_id, vk.tg_id) AS tg_id,
                   COALESCE(NULLIF(u.first_name,''), NULLIF(u.old_first_name,'')) AS first_name,
                   u.last_name, u.web_token
            FROM user_inbound_activity a
            LEFT JOIN vpn_keys vk ON a.email = vk.client_name
            LEFT JOIN users u ON (a.tg_id IS NOT NULL AND a.tg_id != 0 AND a.tg_id = u.tg_id)
                              OR (vk.tg_id IS NOT NULL AND vk.tg_id != 0 AND vk.tg_id = u.tg_id)
                              OR (vk.user_id IS NOT NULL AND vk.user_id = u.id)
            ORDER BY a.total_bytes DESC
        """)
        for urow in cur_u.fetchall():
            iid = urow["inbound_id"]
            if iid not in inbound_users_map:
                inbound_users_map[iid] = []
            inbound_users_map[iid].append({
                "email": urow["email"],
                "tg_id": urow["tg_id"],
                "first_name": urow["first_name"] or urow["email"],
                "last_name": urow["last_name"] or "",
                "web_token": urow["web_token"] or "",
                "connections_count": urow["connections_count"] or 1,
                "up_bytes": urow["up_bytes"] or 0,
                "down_bytes": urow["down_bytes"] or 0,
                "total_bytes": urow["total_bytes"] or 0,
                "first_connected_at": urow["first_connected_at"].strftime("%Y-%m-%d %H:%M") if urow["first_connected_at"] else None,
                "last_connected_at": urow["last_connected_at"].strftime("%Y-%m-%d %H:%M") if urow["last_connected_at"] else None,
            })
        cur_u.close()
        conn_u.close()
    except Exception as e:
        logger.error(f"Error querying user_inbound_activity: {e}")

    # Combine inbounds with traffic stats and user breakdowns
    result_inbounds = []
    for ib in inbounds_list:
        ib_id = ib["id"]
        t = traffic_map.get(ib_id, {
            "up_bytes": 0,
            "down_bytes": 0,
            "total_bytes": 0,
            "max_up_speed_bps": 0,
            "max_down_speed_bps": 0,
            "avg_up_speed_bps": 0,
            "avg_down_speed_bps": 0,
        })
        
        tot = t["total_bytes"]
        share_pct = round((tot / total_period_traffic * 100), 2) if total_period_traffic > 0 else 0.0

        users_list = inbound_users_map.get(ib_id, [])
        total_connections = sum(u["connections_count"] for u in users_list) if users_list else (1 if tot > 0 else 0)

        item = {
            **ib,
            "period_up_bytes": t["up_bytes"],
            "period_down_bytes": t["down_bytes"],
            "period_total_bytes": tot,
            "period_share_pct": share_pct,
            "max_down_speed_mbps": round(t["max_down_speed_bps"] / 1024 / 1024, 2),
            "max_up_speed_mbps": round(t["max_up_speed_bps"] / 1024 / 1024, 2),
            "avg_down_speed_mbps": round(t["avg_down_speed_bps"] / 1024 / 1024, 2),
            "avg_up_speed_mbps": round(t["avg_up_speed_bps"] / 1024 / 1024, 2),
            "total_connections": total_connections,
            "users": users_list,
        }
        result_inbounds.append(item)

    # Sort descending by traffic in period, then by active clients
    result_inbounds.sort(key=lambda x: (x["period_total_bytes"], x["active_clients"]), reverse=True)

    return {
        "days": days,
        "total_period_bytes": total_period_traffic,
        "inbounds_count": len(result_inbounds),
        "inbounds": result_inbounds,
    }



