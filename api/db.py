import logging
from datetime import datetime, timezone, timedelta
import mysql.connector
from mysql.connector import pooling
from config import MYSQL_HOST, MYSQL_USER, MYSQL_PASSWORD, MYSQL_DATABASE, REFERRAL_REWARD_DAYS, REFERRAL_NEWCOMER_DAYS

TZ_TOKYO = timezone(timedelta(hours=9))

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
#  Connection pool
# ─────────────────────────────────────────────

_pool = None


def _get_pool():
    global _pool
    if _pool is None:
        _pool = pooling.MySQLConnectionPool(
            pool_name="vpn_pool",
            pool_size=5,
            pool_reset_session=True,
            host=MYSQL_HOST,
            user=MYSQL_USER,
            password=MYSQL_PASSWORD,
            database=MYSQL_DATABASE,
        )
        logger.info("MySQL connection pool created (size=5)")
    return _pool


def get_db():
    return _get_pool().get_connection()


def execute_query(sql: str, params: tuple = (), fetch: str = None):
    """
    Universal query helper.
    fetch=None   → INSERT / UPDATE / DELETE  (returns lastrowid)
    fetch='one'  → fetchone() → dict | None
    fetch='all'  → fetchall() → list[dict]
    """
    db = get_db()
    cursor = db.cursor(dictionary=True)
    try:
        cursor.execute(sql, params)
        if fetch == 'one':
            return cursor.fetchone()
        if fetch == 'all':
            return cursor.fetchall()
        db.commit()
        return cursor.lastrowid
    finally:
        cursor.close()
        db.close()


# ─────────────────────────────────────────────
#  Users
# ─────────────────────────────────────────────

def get_user_by_tg_id(tg_id: int) -> dict | None:
    return execute_query(
        "SELECT * FROM users WHERE tg_id = %s",
        (tg_id,), fetch='one'
    )


def get_all_users_tg_ids() -> list[int]:
    rows = execute_query("SELECT tg_id FROM users", fetch='all')
    return [r['tg_id'] for r in rows]


def get_active_subscribers_tg_ids() -> list[int]:
    """Возвращает tg_id пользователей с активной подпиской (subscription_until > NOW())."""
    rows = execute_query(
        "SELECT tg_id FROM users WHERE subscription_until > NOW()",
        fetch='all'
    )
    return [r['tg_id'] for r in rows]

def get_or_create_user(tg_id: int, first_name: str | None = None, last_name: str | None = None) -> int:
    """Returns internal user id. Creates user if not exists, updates name if exists."""
    import secrets
    user = get_user_by_tg_id(tg_id)
    if user:
        execute_query(
            "UPDATE users SET first_name = %s, last_name = %s WHERE tg_id = %s",
            (first_name, last_name, tg_id)
        )
        if not user.get('web_token'):
            execute_query(
                "UPDATE users SET web_token = %s WHERE tg_id = %s",
                (secrets.token_urlsafe(16), tg_id)
            )
        return user['id']
    return execute_query(
        "INSERT INTO users (tg_id, first_name, last_name, web_token) VALUES (%s, %s, %s, %s)",
        (tg_id, first_name, last_name, secrets.token_urlsafe(16))
    )


def get_user_by_web_token(token: str) -> dict | None:
    return execute_query(
        "SELECT * FROM users WHERE web_token = %s",
        (token,), fetch='one'
    )


def get_web_token(tg_id: int) -> str | None:
    row = execute_query(
        "SELECT web_token FROM users WHERE tg_id = %s",
        (tg_id,), fetch='one'
    )
    return row['web_token'] if row else None


def get_subscription_until(tg_id: int):
    row = execute_query(
        "SELECT subscription_until FROM users WHERE tg_id = %s",
        (tg_id,), fetch='one'
    )
    return row['subscription_until'] if row else None


def get_permanent_discount(tg_id: int) -> int:
    row = execute_query(
        "SELECT permanent_discount FROM users WHERE tg_id = %s",
        (tg_id,), fetch='one'
    )
    return row['permanent_discount'] if row else 0


def set_permanent_discount(tg_id: int, discount: int):
    execute_query(
        "UPDATE users SET permanent_discount = GREATEST(permanent_discount, %s) WHERE tg_id = %s",
        (discount, tg_id)
    )


def upsert_user_subscription(tg_id: int, subscription_until):
    execute_query(
        """
        INSERT INTO users (tg_id, subscription_until)
        VALUES (%s, %s)
        ON DUPLICATE KEY UPDATE subscription_until = %s
        """,
        (tg_id, subscription_until, subscription_until)
    )


# ─────────────────────────────────────────────
#  Test periods
# ─────────────────────────────────────────────

def set_awg_test_activated(tg_id: int, activated: bool = True):
    """Устанавливает статус активации тестового периода AWG"""
    execute_query(
        "UPDATE users SET test_awg_activated = %s WHERE tg_id = %s",
        (1 if activated else 0, tg_id)
    )


def is_awg_test_activated(tg_id: int) -> bool:
    """Проверяет, активирован ли тестовый период AWG"""
    row = execute_query(
        "SELECT test_awg_activated FROM users WHERE tg_id = %s",
        (tg_id,), fetch='one'
    )
    return bool(row['test_awg_activated']) if row else False


def set_vless_test_activated(tg_id: int, activated: bool = True):
    """Устанавливает статус активации тестового периода VLESS"""
    execute_query(
        "UPDATE users SET test_vless_activated = %s WHERE tg_id = %s",
        (1 if activated else 0, tg_id)
    )


def is_vless_test_activated(tg_id: int) -> bool:
    """Проверяет, активирован ли тестовый период VLESS"""
    row = execute_query(
        "SELECT test_vless_activated FROM users WHERE tg_id = %s",
        (tg_id,), fetch='one'
    )
    return bool(row['test_vless_activated']) if row else False


def set_softether_test_activated(tg_id: int, activated: bool = True):
    execute_query(
        "UPDATE users SET test_softether_activated = %s WHERE tg_id = %s",
        (1 if activated else 0, tg_id)
    )


def is_softether_test_activated(tg_id: int) -> bool:
    row = execute_query(
        "SELECT test_softether_activated FROM users WHERE tg_id = %s",
        (tg_id,), fetch='one'
    )
    return bool(row['test_softether_activated']) if row else False


# ─────────────────────────────────────────────
#  Referrals
# ─────────────────────────────────────────────


def register_user_with_referral(
    new_tg_id: int,
    referrer_tg_id: int | None,
    first_name: str | None = None,
    last_name: str | None = None,
) -> bool:
    if get_user_by_tg_id(new_tg_id):
        return False  # already registered

    # INSERT IGNORE prevents race condition: if two requests arrive simultaneously,
    # only the first one inserts; the second gets 0 affected rows.
    affected = execute_query(
        "INSERT IGNORE INTO users (tg_id, first_name, last_name) VALUES (%s, %s, %s)",
        (new_tg_id, first_name, last_name)
    )
    if not affected:
        return False  # another request already created this user

    if referrer_tg_id and referrer_tg_id != new_tg_id:
        if get_user_by_tg_id(referrer_tg_id):
            reward_referrer(referrer_tg_id)
            reward_newcomer(new_tg_id)
            return True

    return False


def _round_subscription_eod(tg_id: int):
    """Округляет subscription_until до 23:59:59 по Tokyo (+9)."""
    row = execute_query(
        "SELECT subscription_until FROM users WHERE tg_id = %s",
        (tg_id,), fetch='one'
    )
    if not row or not row['subscription_until']:
        return
    dt = row['subscription_until']
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    dt_tokyo = dt.astimezone(TZ_TOKYO)
    eod = dt_tokyo.replace(hour=23, minute=59, second=59, microsecond=0)
    new_until = eod.astimezone(timezone.utc).replace(tzinfo=None)
    execute_query(
        "UPDATE users SET subscription_until = %s WHERE tg_id = %s",
        (new_until, tg_id)
    )


def reward_referrer(referrer_tg_id: int):
    """
    Adds REFERRAL_REWARD_DAYS to referrer's subscription.
    If subscription is NULL or expired, starts counting from today.
    """
    logger.info(f"Rewarding referrer {referrer_tg_id} with +{REFERRAL_REWARD_DAYS} days")
    
    execute_query(
        """
        UPDATE users
        SET
            referral_count = referral_count + 1,
            subscription_until = DATE_ADD(
                GREATEST(
                    COALESCE(subscription_until, NOW()),
                    NOW()
                ),
                INTERVAL %s DAY
            )
        WHERE tg_id = %s
        """,
        (REFERRAL_REWARD_DAYS, referrer_tg_id)
    )
    # Округляем до 23:59:59 Tokyo
    _round_subscription_eod(referrer_tg_id)

def reward_newcomer(tg_id: int):
    """Дарит REFERRAL_NEWCOMER_DAYS дней новому пользователю, пришедшему по рефералке."""
    logger.info(f"Rewarding newcomer {tg_id} with +{REFERRAL_NEWCOMER_DAYS} days")
    execute_query(
        """
        UPDATE users
        SET subscription_until = DATE_ADD(NOW(), INTERVAL %s DAY)
        WHERE tg_id = %s
        """,
        (REFERRAL_NEWCOMER_DAYS, tg_id)
    )
    # Округляем до 23:59:59 Tokyo
    _round_subscription_eod(tg_id)

def get_referral_count(tg_id: int) -> int:
    row = execute_query(
        "SELECT referral_count FROM users WHERE tg_id = %s",
        (tg_id,), fetch='one'
    )
    return row['referral_count'] if row else 0


# ─────────────────────────────────────────────
#  Payments
# ─────────────────────────────────────────────

def create_payment(payment_id: str, tg_id: int, tariff: str, amount, status: str = "pending"):
    execute_query(
        "INSERT INTO payments (payment_id, tg_id, tariff, amount, status) VALUES (%s, %s, %s, %s, %s)",
        (payment_id, tg_id, tariff, amount, status)
    )


def get_payment_by_id(payment_id: str) -> dict | None:
    return execute_query(
        "SELECT * FROM payments WHERE payment_id = %s",
        (payment_id,), fetch='one'
    )


def get_payment_status(payment_id: str) -> str | None:
    row = execute_query(
        "SELECT status FROM payments WHERE payment_id = %s",
        (payment_id,), fetch='one'
    )
    return row['status'] if row else None


def update_payment_status(payment_id: str, status: str):
    execute_query(
        "UPDATE payments SET status = %s WHERE payment_id = %s",
        (status, payment_id)
    )


def is_payment_processed(payment_id: str) -> bool:
    row = execute_query(
        "SELECT status FROM payments WHERE payment_id = %s",
        (payment_id,), fetch='one'
    )
    return row['status'] == 'paid' if row else False


def get_last_paid_payment(tg_id: int) -> dict | None:
    """Возвращает последний оплаченный платёж пользователя"""
    return execute_query(
        """
        SELECT * FROM payments
        WHERE tg_id = %s AND status = 'paid'
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (tg_id,), fetch='one'
    )


def mark_vpn_issued(payment_id: str):
    """Помечает, что VPN выдан"""
    execute_query(
        "UPDATE payments SET vpn_issued = 1 WHERE payment_id = %s",
        (payment_id,)
    )


# ─────────────────────────────────────────────
#  VPN Keys
# ─────────────────────────────────────────────

def create_vpn_key(
    tg_id: int,
    payment_id: str,
    client_id: str,
    client_name: str,
    client_ip: str,
    client_public_key: str,
    vless_link: str = None,
    expires_at=None,
    vpn_type: str = 'awg',
    subscription_link: str = None,
    vpn_file: str = None
):
    logger.debug(f"create_vpn_key called, tg_id={tg_id}")
    execute_query(
        """
        INSERT INTO vpn_keys
            (tg_id, payment_id, client_id, client_name, client_ip, client_public_key, vless_link, expires_at, vpn_type, subscription_link, vpn_file)
        VALUES
            (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            payment_id        = VALUES(payment_id),
            client_id         = VALUES(client_id),
            client_name       = VALUES(client_name),
            client_ip         = VALUES(client_ip),
            client_public_key = VALUES(client_public_key),
            vless_link        = VALUES(vless_link),
            expires_at        = VALUES(expires_at),
            vpn_type          = VALUES(vpn_type),
            subscription_link = VALUES(subscription_link),
            vpn_file          = VALUES(vpn_file)
        """,
        (tg_id, payment_id, client_id, client_name, client_ip, client_public_key, vless_link, expires_at, vpn_type, subscription_link, vpn_file)
    )
    logger.debug("create_vpn_key done")


def get_keys_by_tg_id(tg_id: int) -> list[dict]:
    return execute_query(
        "SELECT * FROM vpn_keys WHERE tg_id = %s",
        (tg_id,), fetch='all'
    )


def get_used_client_ips() -> set[str]:
    rows = execute_query(
        "SELECT client_ip FROM vpn_keys WHERE expires_at > NOW()",
        fetch='all'
    )
    return {r['client_ip'] for r in rows}


def get_user_email(tg_id: int) -> str | None:
    """Получить client_name (email) пользователя из vpn_keys"""
    row = execute_query(
        "SELECT client_name FROM vpn_keys WHERE tg_id = %s",
        (tg_id,), fetch='one'
    )
    return row['client_name'] if row else None


def deactivate_key_by_payment(payment_id: str):
    """Деактивирует ключ по payment_id"""
    execute_query(
        "UPDATE vpn_keys SET expires_at = NOW() WHERE payment_id = %s",
        (payment_id,)
    )

def update_vless_link(tg_id: int, vless_link: str):
    """Обновить vless_link для всех VLESS-ключей пользователя."""
    execute_query(
        "UPDATE vpn_keys SET vless_link = %s WHERE tg_id = %s AND vpn_type = 'vless'",
        (vless_link, tg_id)
    )


def get_users_expiring_in_days(days: int) -> list[dict]:
    """Возвращает пользователей, у которых подписка истекает ровно через `days` дней."""
    return execute_query(
        """
        SELECT tg_id, subscription_until FROM users
        WHERE DATE(subscription_until) = DATE(NOW() + INTERVAL %s DAY)
        """,
        (days,), fetch='all'
    )


# ─────────────────────────────────────────────
#  Promocodes
# ─────────────────────────────────────────────

def create_promocode(code: str, promo_type: str, value: int,
                     max_uses: int | None = None, per_user_limit: int = 1,
                     expires_at=None) -> int:
    return execute_query(
        """
        INSERT INTO promocodes (code, type, value, max_uses, per_user_limit, expires_at)
        VALUES (%s, %s, %s, %s, %s, %s)
        """,
        (code.upper(), promo_type, value, max_uses, per_user_limit, expires_at)
    )


def get_promocode(code: str) -> dict | None:
    return execute_query(
        "SELECT * FROM promocodes WHERE code = %s",
        (code.upper(),), fetch='one'
    )


def validate_promocode(code: str, tg_id: int) -> tuple[dict | None, str | None]:
    """Returns (promo, error_message). If valid, error is None."""
    promo = get_promocode(code)
    if not promo:
        return None, "Промокод не найден"
    if not promo['is_active']:
        return None, "Промокод неактивен"
    if promo['expires_at'] and promo['expires_at'] < datetime.now():
        return None, "Промокод истёк"
    if promo['max_uses'] and promo['used_count'] >= promo['max_uses']:
        return None, "Промокод исчерпан"
    usage_count = get_user_promo_usage_count(promo['id'], tg_id)
    if usage_count >= promo['per_user_limit']:
        return None, "Вы уже использовали этот промокод"
    return promo, None


def use_promocode(promo_id: int, tg_id: int):
    execute_query(
        "INSERT INTO promocode_usages (promocode_id, tg_id) VALUES (%s, %s)",
        (promo_id, tg_id)
    )
    execute_query(
        "UPDATE promocodes SET used_count = used_count + 1 WHERE id = %s",
        (promo_id,)
    )


def get_user_promo_usage_count(promo_id: int, tg_id: int) -> int:
    row = execute_query(
        "SELECT COUNT(*) as cnt FROM promocode_usages WHERE promocode_id = %s AND tg_id = %s",
        (promo_id, tg_id), fetch='one'
    )
    return row['cnt'] if row else 0


def deactivate_promocode(code: str) -> bool:
    execute_query(
        "UPDATE promocodes SET is_active = 0 WHERE code = %s",
        (code.upper(),)
    )
    return True


def list_active_promocodes() -> list[dict]:
    return execute_query(
        "SELECT * FROM promocodes WHERE is_active = 1 ORDER BY created_at DESC",
        fetch='all'
    )