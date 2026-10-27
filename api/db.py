import logging
from datetime import datetime, timezone, timedelta
import mysql.connector
from mysql.connector import pooling
from config import MYSQL_HOST, MYSQL_PORT, MYSQL_USER, MYSQL_PASSWORD, MYSQL_DATABASE, REFERRAL_REWARD_DAYS, REFERRAL_NEWCOMER_DAYS

TZ_MSK = timezone(timedelta(hours=3))
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
            pool_name="vpn_api_pool",
            pool_size=10,
            pool_reset_session=True,
            host=MYSQL_HOST,
            port=MYSQL_PORT,
            user=MYSQL_USER,
            password=MYSQL_PASSWORD,
            database=MYSQL_DATABASE,
        )
        logger.info("MySQL connection pool created (size=10)")
    return _pool


def get_db():
    """Get a connection from pool, recreating pool if needed."""
    global _pool
    try:
        return _get_pool().get_connection()
    except Exception as e:
        logger.warning(f"Pool connection failed, recreating pool: {e}")
        _pool = None
        return _get_pool().get_connection()


def execute_query(sql: str, params: tuple = (), fetch: str = None, _retried: bool = False):
    """
    Universal query helper with auto-retry on transient failures.
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
    except Exception as e:
        err_msg = str(e).lower()
        is_transient = any(s in err_msg for s in (
            'lost connection', 'gone away', 'broken pipe',
            'connection reset', 'can\'t connect',
        ))
        if is_transient and not _retried:
            logger.warning(f"Transient DB error, retrying: {e}")
            try:
                cursor.close()
                db.close()
            except Exception:
                pass
            return execute_query(sql, params, fetch, _retried=True)
        raise
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


def get_all_users_with_web_token() -> list[dict]:
    """Возвращает [{tg_id, web_token}, ...] для всех пользователей с tg_id и web_token."""
    rows = execute_query(
        "SELECT tg_id, web_token FROM users WHERE tg_id IS NOT NULL AND tg_id > 0 AND web_token IS NOT NULL",
        fetch='all'
    )
    return rows or []


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
        old_name = user.get('first_name')
        if old_name and old_name not in ('', '-') and old_name != first_name:
            execute_query(
                "UPDATE users SET old_first_name = %s WHERE tg_id = %s",
                (old_name, tg_id)
            )
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


def get_user_by_id(user_id: int) -> dict | None:
    return execute_query(
        "SELECT * FROM users WHERE id = %s",
        (user_id,), fetch='one'
    )

def get_user_by_email(email: str) -> dict | None:
    return execute_query(
        "SELECT * FROM users WHERE email = %s",
        (email,), fetch='one'
    )


def update_user_subscription_by_id(user_id: int, subscription_until):
    execute_query(
        "UPDATE users SET subscription_until = %s WHERE id = %s",
        (subscription_until, user_id)
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


def create_user(first_name: str, last_name: str) -> dict:
    """Создать пользователя без tg_id. Возвращает {id, web_token}."""
    import secrets
    token = secrets.token_urlsafe(16)
    user_id = execute_query(
        "INSERT INTO users (first_name, last_name, web_token) VALUES (%s, %s, %s)",
        (first_name, last_name, token)
    )
    return {"id": user_id, "web_token": token}


def link_tg_id_to_user(user_id: int, tg_id: int):
    """Привязать tg_id к существующему пользователю из веба или объединить два профиля."""
    web_u = get_user_by_id(user_id)
    tg_u = get_user_by_tg_id(tg_id)

    if not web_u:
        return False

    # Если уже есть отдельный пользователь для этого tg_id:
    if tg_u and tg_u["id"] != user_id:
        # Выбираем максимальную дату подписки (приоритет оплаченному)
        web_sub = web_u.get("subscription_until")
        tg_sub = tg_u.get("subscription_until")
        final_sub = max(filter(None, [web_sub, tg_sub])) if (web_sub or tg_sub) else None

        # Переносим email и дату в основной tg профиль
        email = tg_u.get("email") or web_u.get("email")
        execute_query(
            "UPDATE users SET email = %s, subscription_until = %s WHERE id = %s",
            (email, final_sub, tg_u["id"])
        )
        # Переносим платежи и ключи на tg_id
        execute_query("UPDATE payments SET tg_id = %s WHERE web_user_id = %s", (tg_id, user_id))
        execute_query("UPDATE vpn_keys SET tg_id = %s, user_id = %s WHERE user_id = %s", (tg_id, tg_u["id"], user_id))
        # Деактивируем/удаляем дубликат web_u
        execute_query("DELETE FROM users WHERE id = %s", (user_id,))
    else:
        execute_query(
            "UPDATE users SET tg_id = %s WHERE id = %s",
            (tg_id, user_id)
        )
        execute_query(
            "UPDATE vpn_keys SET tg_id = %s WHERE user_id = %s",
            (tg_id, user_id)
        )

    # В 3x-ui переименовываем tiin_web_{user_id} в приоритетный tiin_{tg_id}
    try:
        import sqlite3, json
        conn = sqlite3.connect("/etc/x-ui/x-ui.db")
        cur = conn.cursor()
        cur.execute("SELECT id, settings FROM inbounds")
        for ib_id, settings_str in cur.fetchall():
            if not settings_str: continue
            data = json.loads(settings_str)
            clients = data.get("clients", [])
            modified = False
            for c in clients:
                if c.get("email") == f"tiin_web_{user_id}":
                    c["email"] = f"tiin_{tg_id}"
                    modified = True
            if modified:
                cur.execute("UPDATE inbounds SET settings = ? WHERE id = ?", (json.dumps(data), ib_id))
        # Объединяем client_traffics если было старое имя
        cur.execute("SELECT 1 FROM client_traffics WHERE email = ?", (f"tiin_{tg_id}",))
        if cur.fetchone():
            cur.execute("DELETE FROM client_traffics WHERE email = ?", (f"tiin_web_{user_id}",))
        else:
            cur.execute("UPDATE client_traffics SET email = ? WHERE email = ?", (f"tiin_{tg_id}", f"tiin_web_{user_id}"))
        conn.commit()
        conn.close()
    except Exception:
        pass

    return True


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


def is_vless_test_activated_by_id(user_id: int) -> bool:
    row = execute_query(
        "SELECT test_vless_activated FROM users WHERE id = %s",
        (user_id,), fetch='one'
    )
    return bool(row['test_vless_activated']) if row else False


def set_vless_test_activated_by_id(user_id: int, activated: bool = True):
    execute_query(
        "UPDATE users SET test_vless_activated = %s WHERE id = %s",
        (1 if activated else 0, user_id)
    )


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
        referrer = get_user_by_tg_id(referrer_tg_id)
        if referrer:
            execute_query(
                "UPDATE users SET referred_by = %s, referral_rewarded = 0 WHERE tg_id = %s",
                (referrer["id"], new_tg_id)
            )
            reward_newcomer(new_tg_id)
            return True

    return False


def confirm_referral_reward(newcomer_user_id: int) -> dict | None:
    """
    Активирует реферальную награду для пригласившего после выполнения условий
    (расход 50 МБ трафика или первая оплата).
    Атомарно переводит referral_rewarded из 0 в 1, предотвращая повторное начисление.
    """
    row = execute_query(
        "SELECT id, tg_id, referred_by, referral_rewarded FROM users WHERE id = %s",
        (newcomer_user_id,), fetch='one'
    )
    if not row or not row.get("referred_by") or row.get("referral_rewarded"):
        return None

    affected = execute_query(
        "UPDATE users SET referral_rewarded = 1 WHERE id = %s AND referral_rewarded = 0",
        (newcomer_user_id,)
    )
    if not affected:
        return None

    referrer_id = row["referred_by"]
    reward_referrer_by_id(referrer_id)
    referrer = execute_query("SELECT id, tg_id FROM users WHERE id = %s", (referrer_id,), fetch='one')
    return {
        "newcomer_id": newcomer_user_id,
        "newcomer_tg_id": row.get("tg_id"),
        "referrer_id": referrer_id,
        "referrer_tg_id": referrer.get("tg_id") if referrer else None
    }


def _round_subscription_eod(tg_id: int):
    """Округляет subscription_until до 23:59:59 по МСК (+3)."""
    row = execute_query(
        "SELECT subscription_until FROM users WHERE tg_id = %s",
        (tg_id,), fetch='one'
    )
    if not row or not row['subscription_until']:
        return
    dt = row['subscription_until']
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    dt_msk = dt.astimezone(TZ_MSK)
    eod = dt_msk.replace(hour=23, minute=59, second=59, microsecond=0)
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
#  Web referrals (by user id)
# ─────────────────────────────────────────────

def _round_subscription_eod_by_id(user_id: int):
    """Округляет subscription_until до 23:59:59 по МСК (+3) — по user.id."""
    row = execute_query(
        "SELECT subscription_until FROM users WHERE id = %s",
        (user_id,), fetch='one'
    )
    if not row or not row['subscription_until']:
        return
    dt = row['subscription_until']
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    dt_msk = dt.astimezone(TZ_MSK)
    eod = dt_msk.replace(hour=23, minute=59, second=59, microsecond=0)
    new_until = eod.astimezone(timezone.utc).replace(tzinfo=None)
    execute_query(
        "UPDATE users SET subscription_until = %s WHERE id = %s",
        (new_until, user_id)
    )


def reward_referrer_by_id(referrer_id: int):
    """Adds REFERRAL_REWARD_DAYS to referrer's subscription (by user.id)."""
    logger.info(f"Rewarding referrer user_id={referrer_id} with +{REFERRAL_REWARD_DAYS} days")
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
        WHERE id = %s
        """,
        (REFERRAL_REWARD_DAYS, referrer_id)
    )
    _round_subscription_eod_by_id(referrer_id)


def _web_referral_promo_days() -> int:
    """Проверяет акцию: 20 дней для первых 5 веб-рефералов, старт 2026-03-30 09:00 Якутск (UTC+9)."""
    now_ykt = datetime.now(TZ_TOKYO)  # Якутск = UTC+9 = то же что Tokyo
    promo_start = datetime(2026, 3, 30, 9, 0, tzinfo=TZ_TOKYO)
    promo_end = datetime(2026, 3, 31, 0, 0, tzinfo=TZ_TOKYO)
    if not (promo_start <= now_ykt < promo_end):
        return 0
    promo_date = datetime(2026, 3, 30).date()
    row = execute_query(
        "SELECT COUNT(*) AS cnt FROM users "
        "WHERE referred_by IS NOT NULL AND DATE(created_at) = %s",
        (promo_date,), fetch='one'
    )
    used = row['cnt'] if row else 0
    if used < 5:
        return 20
    return 0


def reward_newcomer_by_id(newcomer_id: int):
    """Дарит дни новому пользователю: 20 дней по акции или REFERRAL_NEWCOMER_DAYS."""
    promo = _web_referral_promo_days()
    days = promo if promo else REFERRAL_NEWCOMER_DAYS
    logger.info(f"Rewarding newcomer user_id={newcomer_id} with +{days} days (promo={bool(promo)})")
    execute_query(
        """
        UPDATE users
        SET subscription_until = DATE_ADD(
                GREATEST(COALESCE(subscription_until, NOW()), NOW()),
                INTERVAL %s DAY
            )
        WHERE id = %s
        """,
        (days, newcomer_id)
    )
    _round_subscription_eod_by_id(newcomer_id)


def _update_rowcount(sql: str, params: tuple = ()) -> int:
    """Execute UPDATE/INSERT and return rowcount (not lastrowid)."""
    db = get_db()
    cursor = db.cursor(dictionary=True)
    try:
        cursor.execute(sql, params)
        db.commit()
        return cursor.rowcount
    finally:
        cursor.close()
        db.close()


MAX_REFERRALS_PER_DAY = 5


def process_web_referral(newcomer_id: int, referrer_web_token: str) -> bool:
    """
    Process referral from web: look up referrer by web_token,
    atomically set referred_by, reward both.
    Returns True if referral was applied.
    """
    if not referrer_web_token:
        return False

    referrer = execute_query(
        "SELECT id FROM users WHERE web_token = %s",
        (referrer_web_token,), fetch='one'
    )
    if not referrer or referrer['id'] == newcomer_id:
        return False

    # Per-referrer daily limit
    today_count = execute_query(
        "SELECT COUNT(*) AS cnt FROM users "
        "WHERE referred_by = %s AND DATE(created_at) = CURDATE()",
        (referrer['id'],), fetch='one'
    )
    if today_count and today_count['cnt'] >= MAX_REFERRALS_PER_DAY:
        logger.warning(f"Referrer {referrer['id']} hit daily limit ({MAX_REFERRALS_PER_DAY})")
        return False

    # Atomic: only succeeds if referred_by is still NULL (prevents double reward)
    affected = _update_rowcount(
        "UPDATE users SET referred_by = %s WHERE id = %s AND referred_by IS NULL",
        (referrer['id'], newcomer_id)
    )
    if not affected:
        return False

    reward_referrer_by_id(referrer['id'])
    reward_newcomer_by_id(newcomer_id)

    logger.info(f"Web referral: newcomer={newcomer_id}, referrer={referrer['id']}")
    return True


# ─────────────────────────────────────────────
#  Payments
# ─────────────────────────────────────────────

def create_payment(payment_id: str, tg_id: int, tariff: str, amount, status: str = "pending", is_test: bool = False, web_user_id: int = None):
    execute_query(
        "INSERT INTO payments (payment_id, tg_id, web_user_id, tariff, amount, status, is_test) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s) "
        "ON DUPLICATE KEY UPDATE "
        "status = IF(VALUES(status) = 'canceled', 'canceled', status)",
        (payment_id, tg_id, web_user_id, tariff, amount, status, int(is_test))
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


def claim_payment_for_processing(payment_id: str) -> bool:
    """Atomically claim a pending payment for processing.

    Uses UPDATE ... WHERE status IN ('pending', 'succeeded') so only one concurrent call wins.
    Returns True if this call claimed it, False if already claimed/processed.
    """
    db = get_db()
    cursor = db.cursor()
    try:
        cursor.execute(
            "UPDATE payments SET status = 'paid' WHERE payment_id = %s AND status IN ('pending', 'succeeded')",
            (payment_id,),
        )
        db.commit()
        return cursor.rowcount > 0
    finally:
        cursor.close()
        db.close()


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
    xhttp_link: str = None,
    hysteria_link: str = None,
    expires_at=None,
    vpn_type: str = 'awg',
    subscription_link: str = None,
    vpn_file: str = None,
    user_id: int = None,
):
    logger.debug(f"create_vpn_key called, tg_id={tg_id}, user_id={user_id}")
    execute_query(
        """
        INSERT INTO vpn_keys
            (tg_id, payment_id, client_id, client_name, client_ip, client_public_key, vless_link, xhttp_link, hysteria_link, expires_at, vpn_type, subscription_link, vpn_file, user_id)
        VALUES
            (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (tg_id, payment_id, client_id, client_name, client_ip, client_public_key, vless_link, xhttp_link, hysteria_link, expires_at, vpn_type, subscription_link, vpn_file, user_id)
    )
    logger.debug("create_vpn_key done")


def upsert_vpn_key(
    tg_id: int,
    payment_id: str,
    client_id: str,
    client_name: str,
    client_ip: str,
    client_public_key: str,
    vless_link: str = None,
    xhttp_link: str = None,
    hysteria_link: str = None,
    expires_at=None,
    vpn_type: str = 'awg',
    subscription_link: str = None,
    vpn_file: str = None,
    user_id: int = None,
):
    """
    Update an active VPN key if it exists for this tg_id/user_id and vpn_type,
    otherwise insert a new one.
    """
    logger.debug(f"upsert_vpn_key called, tg_id={tg_id}, vpn_type={vpn_type}")
    
    # Try to find an existing active key for this user and type
    where = "tg_id = %s" if tg_id else "user_id = %s"
    val = tg_id if tg_id else user_id
    
    existing = execute_query(
        f"SELECT id FROM vpn_keys WHERE {where} AND vpn_type = %s AND expires_at > NOW() LIMIT 1",
        (val, vpn_type), fetch='one'
    )
    
    if existing:
        execute_query(
            """
            UPDATE vpn_keys 
            SET payment_id = %s, client_id = %s, client_name = %s, client_ip = %s, 
                client_public_key = %s, vless_link = %s, xhttp_link = %s, hysteria_link = %s, 
                expires_at = %s, subscription_link = %s, vpn_file = %s
            WHERE id = %s
            """,
            (payment_id, client_id, client_name, client_ip, client_public_key, 
             vless_link, xhttp_link, hysteria_link, expires_at, subscription_link, vpn_file, existing['id'])
        )
        logger.info(f"Updated existing VPN key {existing['id']} for user {val}")
    else:
        create_vpn_key(tg_id, payment_id, client_id, client_name, client_ip, 
                       client_public_key, vless_link, xhttp_link, hysteria_link, expires_at, 
                       vpn_type, subscription_link, vpn_file, user_id)
        logger.info(f"Inserted new VPN key for user {val}")



def get_hysteria_link_by_tg_id(tg_id: int) -> str | None:
    """Получает Hysteria конфиг из vpn_keys."""
    row = execute_query(
        "SELECT hysteria_link FROM vpn_keys WHERE tg_id = %s AND hysteria_link IS NOT NULL ORDER BY expires_at DESC LIMIT 1",
        (tg_id,), fetch='one'
    )
    return row['hysteria_link'] if row else None


def get_keys_by_tg_id(tg_id: int) -> list[dict]:
    return execute_query(
        "SELECT * FROM vpn_keys WHERE tg_id = %s",
        (tg_id,), fetch='all'
    )


def get_keys_by_user_id(user_id: int) -> list[dict]:
    """Get VPN keys for a web-only user by user_id."""
    return execute_query(
        "SELECT * FROM vpn_keys WHERE user_id = %s",
        (user_id,), fetch='all'
    )


def get_used_client_ips() -> set[str]:
    rows = execute_query(
        "SELECT client_ip FROM vpn_keys WHERE expires_at > NOW()",
        fetch='all'
    )
    return {r['client_ip'] for r in rows}


def get_user_email(tg_id: int, payment_id: str | None = None) -> str | None:
    """Получить client_name (email) пользователя из vpn_keys.
    Если передан payment_id — ищем по нему (точное совпадение).
    Иначе — последний активный ключ.
    """
    if payment_id:
        row = execute_query(
            "SELECT client_name FROM vpn_keys WHERE payment_id = %s",
            (payment_id,), fetch='one'
        )
        if row:
            return row['client_name']
    row = execute_query(
        "SELECT client_name FROM vpn_keys WHERE tg_id = %s ORDER BY expires_at DESC LIMIT 1",
        (tg_id,), fetch='one'
    )
    return row['client_name'] if row else None


def deactivate_key_by_payment(payment_id: str):
    """Деактивирует ключ по payment_id и обновляет дату подписки пользователя в past/NOW"""
    payment = get_payment_by_id(payment_id)
    if payment and payment.get("tg_id"):
        execute_query(
            "UPDATE users SET subscription_until = NOW() WHERE tg_id = %s",
            (payment["tg_id"],)
        )
    elif payment and payment.get("user_id"):
        execute_query(
            "UPDATE users SET subscription_until = NOW() WHERE id = %s",
            (payment["user_id"],)
        )
    execute_query(
        "UPDATE vpn_keys SET expires_at = NOW() WHERE payment_id = %s",
        (payment_id,)
    )

def sync_expiry(tg_id: int, expires_at_utc: datetime):
    """Sync expiry across users.subscription_until, all vpn_keys.expires_at, and RU node."""
    # Ensure naive UTC datetime for MySQL
    if expires_at_utc.tzinfo is not None:
        expires_at_utc = expires_at_utc.astimezone(timezone.utc).replace(tzinfo=None)
    upsert_user_subscription(tg_id, expires_at_utc)
    execute_query(
        "UPDATE vpn_keys SET expires_at = %s WHERE tg_id = %s",
        (expires_at_utc, tg_id)
    )
    logger.info(f"Synced expiry for tg_id={tg_id} to {expires_at_utc}")

    try:
        # Re-enable in Main 3x-ui
        from bot_xui.utils import XUIClient
        from config import XUI_HOST, XUI_USERNAME, XUI_PASSWORD
        xui = XUIClient(XUI_HOST, XUI_USERNAME, XUI_PASSWORD)
        existing = None
        if tg_id and tg_id != 0:
            existing = xui.get_client_by_tg_id(tg_id)
        vk = execute_query(
            "SELECT client_name, client_id FROM vpn_keys WHERE tg_id = %s AND vpn_type = 'vless' LIMIT 1",
            (tg_id,), fetch='one'
        )
        email = vk['client_name'] if vk and vk.get('client_name') else (existing['client'].get('email') if existing else f"tiin_{tg_id}")
        if not existing and email:
            existing = xui.get_client_by_email(email)
        exp_ms = int(expires_at_utc.replace(tzinfo=timezone.utc).timestamp() * 1000)
        if existing and existing.get('inbound_id') != 17:
            xui.update_client_expiry(existing['inbound_id'], existing['client'], exp_ms)

        # Re-enable in RU 3x-ui
        from bot_xui.multi_node import sync_client_to_ru_node
        uuid_val = vk.get('client_id') if vk else (existing['client'].get('id') if existing else None)
        sync_client_to_ru_node(email=email, expiry_ms=exp_ms, enable=True, tg_id=tg_id, uuid_str=uuid_val)

        # Re-enable in AWG if exists
        from awg_api import db as awg_db
        from awg_api import awg_manager
        awg_vk = execute_query(
            "SELECT client_id, client_name FROM vpn_keys WHERE tg_id = %s AND vpn_type = 'awg' LIMIT 1",
            (tg_id,), fetch='one'
        )
        if awg_vk and awg_vk.get("client_id"):
            awg_db.update_client_enabled(awg_vk["client_id"], True)
            awg_manager.write_server_conf()
            if awg_manager.is_interface_up():
                awg_manager.reload_interface()
            logger.info(f"🟢 Re-enabled AWG for tg_id={tg_id}")
    except Exception as e:
        logger.debug(f"sync_expiry: node sync skipped or failed: {e}")


def get_user_paid_payments_count(tg_id: int) -> int:
    """Возвращает количество успешных оплат пользователя."""
    row = execute_query(
        "SELECT COUNT(*) as cnt FROM payments WHERE tg_id = %s AND status = 'paid'",
        (tg_id,), fetch='one'
    )
    return row['cnt'] if row else 0


def grant_grace_period(tg_id: int, hours: int = 12) -> datetime | None:
    """
    Предоставляет аварийный доступ (Grace Period) на указанное количество часов.
    Продлевает от текущего времени (или текущего окончания, если оно в будущем).
    """
    try:
        current_sub = get_subscription_until(tg_id)
        now_dt = datetime.now()
        base_dt = current_sub if current_sub and current_sub > now_dt else now_dt
        new_expiry = base_dt + timedelta(hours=hours)
        sync_expiry(tg_id, new_expiry)
        logger.info(f"🛡️ Grace period granted to tg_id={tg_id} for {hours}h until {new_expiry}")
        return new_expiry
    except Exception as e:
        logger.error(f"Failed to grant grace period to tg_id={tg_id}: {e}")
        return None


def sync_expiry_by_user_id(user_id: int, expires_at_utc: datetime):
    """Sync expiry for web-only users (by user_id) to vpn_keys, users table, and RU node."""
    if expires_at_utc.tzinfo is not None:
        expires_at_utc = expires_at_utc.astimezone(timezone.utc).replace(tzinfo=None)
    execute_query(
        "UPDATE users SET subscription_until = %s WHERE id = %s",
        (expires_at_utc, user_id)
    )
    execute_query(
        "UPDATE vpn_keys SET expires_at = %s WHERE user_id = %s",
        (expires_at_utc, user_id)
    )
    logger.info(f"Synced expiry for user_id={user_id} to {expires_at_utc}")

    try:
        from bot_xui.utils import XUIClient
        from config import XUI_HOST, XUI_USERNAME, XUI_PASSWORD
        xui = XUIClient(XUI_HOST, XUI_USERNAME, XUI_PASSWORD)
        vk = execute_query(
            "SELECT client_name, client_id, tg_id FROM vpn_keys WHERE user_id = %s AND vpn_type = 'vless' LIMIT 1",
            (user_id,), fetch='one'
        )
        exp_ms = int(expires_at_utc.replace(tzinfo=timezone.utc).timestamp() * 1000)
        if vk and vk.get('client_name'):
            email = vk['client_name']
            existing = xui.get_client_by_email(email)
            if existing:
                xui.update_client_expiry(existing['inbound_id'], existing['client'], exp_ms)

            from bot_xui.multi_node import sync_client_to_ru_node
            sync_client_to_ru_node(
                email=email,
                expiry_ms=exp_ms,
                enable=True,
                tg_id=vk.get('tg_id', 0),
                uuid_str=vk.get('client_id')
            )

        from awg_api import db as awg_db
        from awg_api import awg_manager
        awg_vk = execute_query(
            "SELECT client_id FROM vpn_keys WHERE user_id = %s AND vpn_type = 'awg' LIMIT 1",
            (user_id,), fetch='one'
        )
        if awg_vk and awg_vk.get("client_id"):
            awg_db.update_client_enabled(awg_vk["client_id"], True)
            awg_manager.write_server_conf()
            if awg_manager.is_interface_up():
                awg_manager.reload_interface()
    except Exception as e:
        logger.debug(f"sync_expiry_by_user_id: node sync skipped or failed: {e}")


def update_vless_link(tg_id: int, vless_link: str):
    """Обновить vless_link для всех VLESS-ключей пользователя."""
    execute_query(
        "UPDATE vpn_keys SET vless_link = %s WHERE tg_id = %s AND vpn_type = 'vless'",
        (vless_link, tg_id)
    )


def get_users_expiring_in_days(days: int) -> list[dict]:
    """Возвращает пользователей, у которых подписка истекает ровно через `days` дней (по МСК)."""
    return execute_query(
        """
        SELECT tg_id, email, subscription_until, autopay_enabled, payment_method_id, autopay_tariff, bot_blocked
        FROM users
        WHERE DATE(DATE_ADD(subscription_until, INTERVAL 3 HOUR)) = DATE(DATE_ADD(NOW() + INTERVAL %s DAY, INTERVAL 3 HOUR))
          AND bot_blocked = 0
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


UNIVERSAL_WINBACK_CODES = {15: "SPARKLING_BACK15", 20: "MISTY_BACK20", 25: "WINDY_BACK25"}


def create_winback_promo(tg_id: int, promo_type: str = 'discount', value: int = 20, days_valid: int = 14) -> str:
    """Return a universal winback promo code based on discount value."""
    code = UNIVERSAL_WINBACK_CODES.get(value, "SPARKLING_BACK15")
    return code


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
    """Atomically record promo usage and increment counter in a single transaction."""
    db = get_db()
    cursor = db.cursor()
    try:
        cursor.execute(
            "INSERT INTO promocode_usages (promocode_id, tg_id) VALUES (%s, %s)",
            (promo_id, tg_id),
        )
        cursor.execute(
            "UPDATE promocodes SET used_count = used_count + 1 WHERE id = %s",
            (promo_id,),
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        cursor.close()
        db.close()


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


def log_promo_activation(promo_id: int, tg_id: int):
    """Record that user activated a discount promo via /promo (not yet consumed)."""
    execute_query(
        "INSERT IGNORE INTO promo_activations (promocode_id, tg_id) VALUES (%s, %s)",
        (promo_id, tg_id),
    )


def get_users_with_unused_activated_promos(min_days: int = 2) -> list[dict]:
    """Find users who activated a discount promo but never completed a payment."""
    rows = execute_query(
        """
        SELECT pa.tg_id, pa.promocode_id, pa.activated_at, p.code, p.value, p.expires_at
        FROM promo_activations pa
        JOIN promocodes p ON p.id = pa.promocode_id
        LEFT JOIN (
            SELECT DISTINCT pu.promocode_id, pu.tg_id
            FROM promocode_usages pu
        ) pu ON pu.promocode_id = pa.promocode_id AND pu.tg_id = pa.tg_id
        LEFT JOIN (
            SELECT DISTINCT tg_id FROM payments WHERE status = 'paid'
        ) pay ON pay.tg_id = pa.tg_id
        WHERE pu.promocode_id IS NULL
          AND pay.tg_id IS NULL
          AND p.is_active = 1
          AND (p.expires_at IS NULL OR p.expires_at > NOW())
          AND pa.activated_at < NOW() - INTERVAL %s DAY
        """,
        (min_days,),
        fetch='all',
    ) or []
    return rows


# ─────────────────────────────────────────────
#  Autopay
# ─────────────────────────────────────────────

def save_user_payment_method(tg_id: int, payment_method_id: str, tariff: str, vpn_type: str = "vless") -> dict:
    """
    Save payment method for user.
    If the user previously manually disabled autopay (autopay_disabled_at is not NULL),
    keep autopay_enabled = 0 and do not re-enable it automatically.
    Returns a dict: {'autopay_enabled': bool, 'disabled_at': datetime | None}
    """
    user = execute_query(
        "SELECT autopay_disabled_at, autopay_enabled FROM users WHERE tg_id = %s",
        (tg_id,), fetch='one'
    )
    if user and user.get('autopay_disabled_at'):
        execute_query(
            "UPDATE users SET payment_method_id = %s, autopay_tariff = %s, autopay_vpn_type = %s WHERE tg_id = %s",
            (payment_method_id, tariff, vpn_type, tg_id),
        )
        return {"autopay_enabled": False, "disabled_at": user.get('autopay_disabled_at')}
    else:
        execute_query(
            "UPDATE users SET payment_method_id = %s, autopay_enabled = 1, autopay_disabled_at = NULL, "
            "autopay_tariff = %s, autopay_vpn_type = %s WHERE tg_id = %s",
            (payment_method_id, tariff, vpn_type, tg_id),
        )
        return {"autopay_enabled": True, "disabled_at": None}


def save_user_payment_method_by_id(user_id: int, payment_method_id: str, tariff: str, vpn_type: str = "vless") -> dict:
    """Save payment method by user_id (for web users)."""
    user = execute_query(
        "SELECT autopay_disabled_at, autopay_enabled FROM users WHERE id = %s",
        (user_id,), fetch='one'
    )
    if user and user.get('autopay_disabled_at'):
        execute_query(
            "UPDATE users SET payment_method_id = %s, autopay_tariff = %s, autopay_vpn_type = %s WHERE id = %s",
            (payment_method_id, tariff, vpn_type, user_id),
        )
        return {"autopay_enabled": False, "disabled_at": user.get('autopay_disabled_at')}
    else:
        execute_query(
            "UPDATE users SET payment_method_id = %s, autopay_enabled = 1, autopay_disabled_at = NULL, "
            "autopay_tariff = %s, autopay_vpn_type = %s WHERE id = %s",
            (payment_method_id, tariff, vpn_type, user_id),
        )
        return {"autopay_enabled": True, "disabled_at": None}


def enable_autopay(tg_id: int):
    """Enable autopay for user and clear disabled timestamp."""
    execute_query(
        "UPDATE users SET autopay_enabled = 1, autopay_disabled_at = NULL WHERE tg_id = %s AND payment_method_id IS NOT NULL",
        (tg_id,),
    )


def update_autopay_tariff(tg_id: int, tariff: str):
    """Update autopay tariff for user."""
    execute_query(
        "UPDATE users SET autopay_tariff = %s WHERE tg_id = %s",
        (tariff, tg_id),
    )


def disable_autopay(tg_id: int):
    """Disable autopay for user and record disabled timestamp."""
    execute_query(
        "UPDATE users SET autopay_enabled = 0, autopay_disabled_at = NOW() WHERE tg_id = %s",
        (tg_id,),
    )


def remove_payment_method(tg_id: int):
    """Remove saved card and disable autopay."""
    execute_query(
        "UPDATE users SET payment_method_id = NULL, autopay_enabled = 0, autopay_disabled_at = NOW() WHERE tg_id = %s",
        (tg_id,),
    )


def disable_autopay_by_id(user_id: int):
    execute_query(
        "UPDATE users SET autopay_enabled = 0, autopay_disabled_at = NOW() WHERE id = %s",
        (user_id,),
    )


def get_user_target_charge_time(user_id: int, tg_id: int) -> tuple[int, int]:
    """
    Get (hour, minute) in MSK when user usually pays, or default to 14:00 MSK.
    Ensures target hour is within daytime range [08:00 - 22:00] MSK (Night Guard).
    """
    # 1. Try to get time of last paid payment
    res = execute_query(
        "SELECT created_at FROM payments WHERE (tg_id = %s OR web_user_id = %s) AND status = 'paid' "
        "ORDER BY created_at DESC LIMIT 1",
        (tg_id or 0, user_id), fetch='one'
    )
    if not res:
        # 2. Try user creation time
        res = execute_query(
            "SELECT created_at FROM users WHERE id = %s",
            (user_id,), fetch='one'
        )

    if res and res.get('created_at'):
        dt = res['created_at']
        # created_at in MySQL is UTC -> convert to MSK (+3)
        dt_msk = dt + timedelta(hours=3)
        hour = dt_msk.hour
        minute = dt_msk.minute
        # Night Guard: if between 23:00 and 07:59 MSK, adjust to 12:00 MSK
        if hour >= 23 or hour < 8:
            hour = 12
            minute = 0
        return hour, minute

    return 14, 0


def get_autopay_users_for_phase1() -> list[dict]:
    """
    Users with autopay enabled whose subscription expires within 20 to 28 hours (approx 1 day),
    and who haven't received phase 1 notification in the last 24 hours.
    """
    return execute_query(
        "SELECT u.id, u.tg_id, u.email, u.payment_method_id, u.autopay_tariff, u.autopay_vpn_type, "
        "u.subscription_until, u.permanent_discount "
        "FROM users u "
        "WHERE u.autopay_enabled = 1 AND u.payment_method_id IS NOT NULL "
        "AND u.subscription_until IS NOT NULL AND u.bot_blocked = 0 "
        "AND u.subscription_until BETWEEN NOW() + INTERVAL 20 HOUR AND NOW() + INTERVAL 28 HOUR "
        "AND NOT EXISTS ("
        "  SELECT 1 FROM message_log ml "
        "  WHERE (ml.tg_id = u.tg_id OR (u.tg_id IS NULL AND ml.tg_id = u.id)) "
        "  AND ml.scenario IN ('autopay_reminder', 'autopay_phase1_reminder', 'expiry_1d', 'expiry_0d') "
        "  AND ml.created_at >= NOW() - INTERVAL 24 HOUR"
        ")",
        fetch='all'
    )


def get_autopay_users_for_phase2() -> list[dict]:
    """
    Users with autopay enabled whose subscription expires within 1 to 5 hours (approx 3 hours),
    and who haven't received phase 2 warning in the last 12 hours.
    """
    return execute_query(
        "SELECT u.id, u.tg_id, u.email, u.payment_method_id, u.autopay_tariff, u.autopay_vpn_type, "
        "u.subscription_until, u.permanent_discount "
        "FROM users u "
        "WHERE u.autopay_enabled = 1 AND u.payment_method_id IS NOT NULL "
        "AND u.subscription_until IS NOT NULL AND u.bot_blocked = 0 "
        "AND u.subscription_until BETWEEN NOW() + INTERVAL 1 HOUR AND NOW() + INTERVAL 5 HOUR "
        "AND NOT EXISTS ("
        "  SELECT 1 FROM message_log ml "
        "  WHERE (ml.tg_id = u.tg_id OR (u.tg_id IS NULL AND ml.tg_id = u.id)) "
        "  AND ml.scenario IN ('autopay_phase2_warning') "
        "  AND ml.created_at >= NOW() - INTERVAL 12 HOUR"
        ")",
        fetch='all'
    )


def get_manual_users_for_phase1() -> list[dict]:
    """
    Users WITHOUT autopay enabled whose subscription expires within 20 to 28 hours (approx 1 day),
    and who haven't received phase 1 manual notification in the last 24 hours.
    Trial users (active test without any paid payments) are skipped so they aren't bothered at 24h.
    """
    return execute_query(
        "SELECT u.id, u.tg_id, u.email, u.subscription_until "
        "FROM users u "
        "WHERE (u.autopay_enabled = 0 OR u.autopay_enabled IS NULL OR u.payment_method_id IS NULL) "
        "AND u.subscription_until IS NOT NULL AND u.bot_blocked = 0 "
        "AND u.subscription_until BETWEEN NOW() + INTERVAL 20 HOUR AND NOW() + INTERVAL 28 HOUR "
        "AND NOT ( (u.test_vless_activated = 1 OR u.test_awg_activated = 1) AND NOT EXISTS ("
        "    SELECT 1 FROM payments p WHERE p.tg_id = u.tg_id AND p.status = 'paid' AND p.is_test = 0"
        ")) "
        "AND NOT EXISTS ("
        "  SELECT 1 FROM message_log ml "
        "  WHERE (ml.tg_id = u.tg_id OR (u.tg_id IS NULL AND ml.tg_id = u.id)) "
        "  AND ml.scenario IN ('manual_phase1_reminder', 'autopay_phase1_reminder', 'manual_phase2_warning', 'expiry_1d', 'expiry_0d') "
        "  AND ml.created_at >= NOW() - INTERVAL 24 HOUR"
        ")",
        fetch='all'
    )


def get_manual_users_for_phase2() -> list[dict]:
    """
    Users WITHOUT autopay enabled whose subscription expires within 1 to 5 hours (approx 3 hours),
    and who haven't received phase 2 manual warning in the last 12 hours.
    """
    return execute_query(
        "SELECT u.id, u.tg_id, u.email, u.subscription_until, u.test_vless_activated, u.test_awg_activated "
        "FROM users u "
        "WHERE (u.autopay_enabled = 0 OR u.autopay_enabled IS NULL OR u.payment_method_id IS NULL) "
        "AND u.subscription_until IS NOT NULL AND u.bot_blocked = 0 "
        "AND u.subscription_until BETWEEN NOW() + INTERVAL 1 HOUR AND NOW() + INTERVAL 5 HOUR "
        "AND NOT EXISTS ("
        "  SELECT 1 FROM message_log ml "
        "  WHERE (ml.tg_id = u.tg_id OR (u.tg_id IS NULL AND ml.tg_id = u.id)) "
        "  AND ml.scenario IN ('manual_phase2_warning', 'autopay_phase2_warning') "
        "  AND ml.created_at >= NOW() - INTERVAL 12 HOUR"
        ")",
        fetch='all'
    )


def get_manual_users_expiring_within_24h() -> list[dict]:
    """
    Users WITHOUT autopay enabled whose subscription expires within the next 24 hours.
    Used by daily cache collector.
    """
    return execute_query(
        "SELECT u.id, u.tg_id, u.email, u.subscription_until "
        "FROM users u "
        "WHERE (u.autopay_enabled = 0 OR u.autopay_enabled IS NULL OR u.payment_method_id IS NULL) "
        "AND u.subscription_until IS NOT NULL AND u.bot_blocked = 0 "
        "AND u.subscription_until BETWEEN NOW() AND NOW() + INTERVAL 24 HOUR",
        fetch='all'
    )


def get_autopay_users_due(days_before: int = 1) -> list[dict]:
    """Get users whose subscription expires within `days_before` days and have autopay enabled.
    Excludes users who already have an autopayment (success or pending) for today (prevents duplicate charges)."""
    if days_before == 0:
        where_clause = (
            "WHERE u.autopay_enabled = 1 AND u.payment_method_id IS NOT NULL "
            "AND u.subscription_until IS NOT NULL "
            "AND u.bot_blocked = 0 "
            "AND u.subscription_until <= NOW() + INTERVAL 30 MINUTE "
            "AND NOT EXISTS ("
            "  SELECT 1 FROM autopay_log al "
            "  WHERE al.user_id = u.id "
            "  AND al.status IN ('success', 'pending') "
            "  AND al.created_at >= CURDATE()"
            ")"
        )
        params = ()
    else:
        where_clause = (
            "WHERE u.autopay_enabled = 1 AND u.payment_method_id IS NOT NULL "
            "AND u.subscription_until IS NOT NULL "
            "AND u.bot_blocked = 0 "
            "AND u.subscription_until BETWEEN NOW() AND NOW() + INTERVAL %s DAY "
            "AND NOT EXISTS ("
            "  SELECT 1 FROM autopay_log al "
            "  WHERE al.user_id = u.id "
            "  AND al.status IN ('success', 'pending') "
            "  AND al.created_at >= CURDATE()"
            ")"
        )
        params = (days_before,)
    return execute_query(
        "SELECT u.id, u.tg_id, u.email, u.payment_method_id, u.autopay_tariff, u.autopay_vpn_type, "
        "u.subscription_until, u.permanent_discount "
        "FROM users u " + where_clause,
        params, fetch='all',
    )



def log_autopay(tg_id: int, user_id: int, tariff: str, amount: float,
                payment_id: str = None, status: str = "pending", error: str = None):
    # Normalize status for ENUM('pending','success','failed','disabled','refunded')
    norm_status = (status or "pending").lower()
    if norm_status in ("succeeded", "paid", "success"):
        norm_status = "success"
    elif norm_status in ("canceled", "cancelled", "failed", "error"):
        norm_status = "failed"
    elif norm_status in ("disabled", "refunded", "pending"):
        pass
    else:
        norm_status = "failed"

    execute_query(
        "INSERT INTO autopay_log (tg_id, user_id, tariff, amount, payment_id, status, error_message) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s)",
        (tg_id, user_id, tariff, amount, payment_id, norm_status, error),
    )


def log_message_sent(tg_id: int, source: str, scenario: str = None,
                       message_text: str = None, status: str = 'sent',
                       error_text: str = None):
    """Log a sent Telegram message to message_log table (ignoring test messages and test accounts)."""
    # Skip test/admin accounts and unit-test mock scenarios
    TEST_TG_IDS = {111, 444, 999, 123456, 3, 100, 364224373, 6648354839, 5254383247}
    if tg_id in TEST_TG_IDS:
        return
    if scenario and (scenario.startswith('unit_test') or scenario.startswith('mock_')):
        return

    if message_text and len(message_text) > 500:
        message_text = message_text[:500]
    if error_text and len(error_text) > 255:
        error_text = error_text[:255]
    execute_query(
        "INSERT INTO message_log (tg_id, source, scenario, message_text, status, error_text) "
        "VALUES (%s, %s, %s, %s, %s, %s)",
        (tg_id, source, scenario, message_text, status, error_text),
    )


def log_user_platform(tg_id: int | None, user_id: int | None,
                     platform: str, client_app: str | None = None,
                     client_version: str | None = None,
                     user_agent: str | None = None,
                     ip: str | None = None):
    if user_agent and len(user_agent) > 512:
        user_agent = user_agent[:512]
    execute_query("""
        INSERT INTO user_platforms (tg_id, user_id, platform, client_app, client_version, user_agent, ip, first_seen, last_seen, seen_count)
        VALUES (%s, %s, %s, %s, %s, %s, %s, NOW(), NOW(), 1)
        ON DUPLICATE KEY UPDATE
            last_seen = NOW(),
            seen_count = seen_count + 1,
            user_agent = VALUES(user_agent),
            ip = VALUES(ip),
            client_version = VALUES(client_version)
    """, (tg_id, user_id, platform, client_app, client_version, user_agent, ip))


def get_user_platforms(tg_id: int) -> list[dict]:
    return execute_query(
        "SELECT platform, client_app, client_version, last_seen, seen_count FROM user_platforms WHERE tg_id = %s ORDER BY last_seen DESC",
        (tg_id,), fetch='all'
    )


def get_all_users_platforms() -> list[dict]:
    return execute_query(
        """SELECT up.*, u.first_name, u.last_name
           FROM user_platforms up
           JOIN users u ON u.tg_id = up.tg_id
           ORDER BY up.last_seen DESC""",
        fetch='all'
    )


def cleanup_expired_sessions():
    """Remove expired web auth sessions."""
    deleted = _update_rowcount(
        "DELETE FROM auth_sessions WHERE expires_at <= NOW()",
    )
    if deleted:
        logger.info(f"[SESSION_CLEANUP] Removed {deleted} expired sessions")


# ── Winback Campaign Helpers ──────────────────────────────────────────────────

def has_winback_discount(tg_id: int) -> bool:
    """Check if user has active 20% winback discount."""
    row = execute_query(
        "SELECT winback_discount_active FROM users WHERE tg_id = %s",
        (tg_id,),
        fetch='one'
    )
    return bool(row and row.get('winback_discount_active'))


def set_winback_discount(tg_id: int, active: bool = True):
    """Set or clear active winback discount for user."""
    val = 1 if active else 0
    execute_query(
        "UPDATE users SET winback_discount_active = %s WHERE tg_id = %s",
        (val, tg_id)
    )