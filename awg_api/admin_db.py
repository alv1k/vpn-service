"""Bot DB queries for admin panel (users, payments, vpn_keys)."""
import logging
from .db import _get_conn

logger = logging.getLogger(__name__)


def list_users(search: str = None, limit: int = 100) -> list[dict]:
    conn = _get_conn()
    cur = conn.cursor(dictionary=True)
    if search:
        cur.execute("""
            SELECT id, tg_id, first_name, last_name, subscription_until,
                   permanent_discount, referral_count, created_at,
                   test_awg_activated, test_vless_activated
            FROM users
            WHERE tg_id LIKE %s OR first_name LIKE %s OR last_name LIKE %s
            ORDER BY created_at DESC LIMIT %s
        """, (f"%{search}%", f"%{search}%", f"%{search}%", limit))
    else:
        cur.execute("""
            SELECT id, tg_id, first_name, last_name, subscription_until,
                   permanent_discount, referral_count, created_at,
                   test_awg_activated, test_vless_activated
            FROM users ORDER BY created_at DESC LIMIT %s
        """, (limit,))
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows


def count_users() -> dict:
    conn = _get_conn()
    cur = conn.cursor(dictionary=True)
    cur.execute("""
        SELECT
            COUNT(*) as total,
            SUM(CASE WHEN subscription_until > NOW() THEN 1 ELSE 0 END) as active
        FROM users
    """)
    row = cur.fetchone()
    cur.close()
    conn.close()
    return row or {"total": 0, "active": 0}


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
        SELECT payment_id, tg_id, tariff, amount, status, vpn_issued, created_at
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
        SELECT p.payment_id, p.tg_id, p.tariff, p.amount, p.status, p.created_at,
               u.first_name, u.last_name
        FROM payments p
        LEFT JOIN users u ON p.tg_id = u.tg_id
        ORDER BY p.created_at DESC LIMIT %s
    """, (limit,))
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
            SUM(CASE WHEN status='paid' THEN 1 ELSE 0 END) as paid,
            SUM(CASE WHEN status='paid' THEN amount ELSE 0 END) as revenue
        FROM payments
    """)
    row = cur.fetchone()
    cur.close()
    conn.close()
    return row or {"total": 0, "paid": 0, "revenue": 0}
