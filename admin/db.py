"""Bot DB queries for admin panel (users, payments, vpn_keys)."""
import logging

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


def webpage_visitor_journey(web_token: str) -> list[dict]:
    conn = _get_conn()
    cur = conn.cursor(dictionary=True)
    cur.execute("""
        SELECT event_type, element_id, element_text, extra_data, ip, user_agent, created_at
        FROM webpage_events
        WHERE web_token = %s
        ORDER BY created_at ASC
    """, (web_token,))
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows
