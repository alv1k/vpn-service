import mysql.connector
import os
from config import MYSQL_HOST, MYSQL_USER, MYSQL_PASSWORD, MYSQL_DATABASE

def get_db():
    return mysql.connector.connect(
        host=MYSQL_HOST,
        user=MYSQL_USER,
        password=MYSQL_PASSWORD,
        database=MYSQL_DATABASE
    )

def get_cursor(commit=True):
    """
    Возвращает курсор для работы с БД.
    Если commit=True, вызов cur.connection.commit() после изменений.
    """
    db = get_db()
    cur = db.cursor(dictionary=True)
    try:
        yield cur
        if commit:
            db.commit()
    finally:
        cur.close()
        db.close()

def create_payment(payment_id, tg_id, tariff, amount, status="pending"):
    db = get_db()
    cur = db.cursor()
    try:
        cur.execute(
            "INSERT INTO payments (payment_id, tg_id, tariff, amount, status) VALUES (%s, %s, %s, %s, %s)",
            (payment_id, tg_id, tariff, amount, status)
        )
        db.commit()
    finally:
        cur.close()
        db.close()

def get_payment_status(payment_id: str) -> str | None:
    db = get_db()
    cursor = db.cursor()
    cursor.execute(
        "SELECT status FROM payments WHERE payment_id=%s",
        (payment_id,)
    )
    row = cursor.fetchone()
    cursor.close()
    db.close()
    return row[0] if row else None

def update_payment_status(payment_id, status):
    db = get_db()
    cur = db.cursor()
    try:
        cur.execute(
            "UPDATE payments SET status=%s WHERE payment_id=%s",
            (status, payment_id)
        )
        db.commit()
    finally:
        cur.close()
        db.close()

def get_keys_by_tg_id(tg_id):
    db = get_db()
    cur = db.cursor(dictionary=True)
    try:
        cur.execute(
            "SELECT * FROM vpn_keys WHERE tg_id=%s",
            (tg_id,)
        )
        return cur.fetchall()
    finally:
        cur.close()
        db.close()

def get_payment_by_id(payment_id: str) -> dict | None:
    db = get_db()
    cursor = db.cursor(dictionary=True)
    cursor.execute(
        "SELECT * FROM payments WHERE payment_id=%s",
        (payment_id,)
    )
    result = cursor.fetchone()
    cursor.close()
    db.close()
    return result

def is_payment_processed(payment_id):    
    db = get_db()
    cursor = db.cursor(dictionary=True)
    cursor.execute(
        "SELECT status FROM payments WHERE id=%s",
        (payment_id,)
    )
    result = cursor.fetchone()
    cursor.close()
    db.close()
    return result

def get_user_by_tg_id(tg_id: int):
    db = get_db()
    cursor = db.cursor(dictionary=True)
    cursor.execute(
        "SELECT * FROM users WHERE tg_id=%s",
        (tg_id,)
    )
    user = cursor.fetchone()
    cursor.close()
    db.close()
    return user

def get_all_users_tg_ids():
    db = get_db()
    cursor = db.cursor(dictionary=True)
    cursor.execute(
        "SELECT tg_id FROM users"
    )
    user = cursor.fetchall()
    cursor.close()
    db.close()
    return [u['tg_id'] for u in users]  # список tg_id


def upsert_user_subscription(tg_id: int, subscription_until):
    db = get_db()
    cursor = db.cursor()
    cursor.execute(
        """
        INSERT INTO users (tg_id, subscription_until)
        VALUES (%s, %s)
        ON DUPLICATE KEY UPDATE
            subscription_until = %s
        """,
        (tg_id, subscription_until, subscription_until)
    )
    db.commit()
    cursor.close()
    db.close()


def set_awg_test_activated(tg_id: int, activated: bool = True):
    """Устанавливает статус активации тестового периода"""
    db = get_db()
    cursor = db.cursor()
    cursor.execute(
        """
        UPDATE users
        SET test_awg_activated = %s
        WHERE tg_id = %s
        """,
        (1 if activated else 0, tg_id)
    )
    db.commit()
    cursor.close()
    db.close()


def is_awg_test_activated(tg_id: int) -> bool:
    """Проверяет, активирован ли тестовый период"""
    db = get_db()
    cursor = db.cursor()
    cursor.execute(
        """
        SELECT test_awg_activated
        FROM users
        WHERE tg_id = %s
        """,
        (tg_id,)
    )
    result = cursor.fetchone()
    cursor.close()
    db.close()
    
    if result is None:
        return False
    
    return bool(result[0])

    
def set_vless_test_activated(tg_id: int, activated: bool = True):
    """Устанавливает статус активации тестового периода"""
    db = get_db()
    cursor = db.cursor()
    cursor.execute(
        """
        UPDATE users
        SET test_vless_activated = %s
        WHERE tg_id = %s
        """,
        (1 if activated else 0, tg_id)
    )
    db.commit()
    cursor.close()
    db.close()


def is_vless_test_activated(tg_id: int) -> bool:
    """Проверяет, активирован ли тестовый период"""
    db = get_db()
    cursor = db.cursor()
    cursor.execute(
        """
        SELECT test_vless_activated
        FROM users
        WHERE tg_id = %s
        """,
        (tg_id,)
    )
    result = cursor.fetchone()
    cursor.close()
    db.close()
    
    if result is None:
        return False
    
    return bool(result[0])


def get_or_create_user(tg_id: int) -> int:
    db = get_db()
    cursor = db.cursor(dictionary=True)

    cursor.execute("SELECT id FROM users WHERE tg_id=%s", (tg_id,))
    user = cursor.fetchone()

    if user:
        user_id = user["id"]
    else:
        cursor.execute(
            "INSERT INTO users (tg_id) VALUES (%s)",
            (tg_id,)
        )
        db.commit()
        user_id = cursor.lastrowid

    cursor.close()
    db.close()
    return user_id


def create_vpn_key(tg_id, payment_id, client_id, client_name, client_ip, client_public_key, config, expires_at, vpn_type='awg'): 
    print(f"[DB] create_vpn_key called, tg_id={tg_id}")
    db = get_db()
    cursor = db.cursor()

    cursor.execute(
        """
        INSERT INTO vpn_keys (tg_id, payment_id, client_id, client_name, client_ip, client_public_key, config, expires_at, vpn_type)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            payment_id = %s,
            client_id = %s,
            client_name = %s,
            client_ip = %s,
            client_public_key = %s,
            config = %s,
            expires_at = %s,
            vpn_type = %s
        """,
        (
            tg_id, 
            payment_id, 
            client_id,
            client_name,
            client_ip, 
            client_public_key, 
            config, 
            expires_at, 
            vpn_type,
            payment_id,
            client_id,
            client_name,
            client_ip, 
            client_public_key, 
            config, 
            expires_at,
            vpn_type
        )
    )
    print(f"[DB] rowcount: {cursor.rowcount}, lastrowid: {cursor.lastrowid}")
    db.commit()
    print("[DB] after commit")
    cursor.close()
    db.close()


def get_subscription_until(tg_id: int):    
    db = get_db()
    cursor = db.cursor()

    cursor.execute(
        """
        SELECT subscription_until FROM users
        WHERE tg_id = %s
        """,
        (tg_id,)
    )

    row = cursor.fetchone()
    cursor.close()
    db.close()
    return row[0] if row else None

    

def get_used_client_ips() -> set[str]:
    db = get_db()
    cursor = db.cursor()

    cursor.execute(
        "SELECT client_ip FROM vpn_keys WHERE expires_at > NOW()"
    )

    ips = {row[0] for row in cursor.fetchall()}

    cursor.close()
    db.close()
    return ips



def get_user_email(tg_id: int):
    """Получить email пользователя"""
    db = get_db()
    cursor = db.cursor()
    
    cursor.execute(
        """
        SELECT client_name FROM vpn_keys 
        WHERE tg_id = %s
        """,
        (tg_id,)
    )
    result = cursor.fetchone()
    db.close()
    return result[0] if result else None

def deactivate_key_by_payment(payment_id: str):
    """Деактивирует ключ по payment_id"""
    execute_query("""
        UPDATE vpn_keys 
        SET expires_at = NOW()
        WHERE payment_id = %s
    """, (payment_id,))

def get_last_paid_payment(tg_id: int):
    """
    Возвращает последний оплаченный платёж пользователя
    """

def mark_vpn_issued(payment_id: str):
    """
    Помечает, что VPN выдан
    """
