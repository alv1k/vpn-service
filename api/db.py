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


def create_vpn_key(user_id, payment_id, client_id, client_name, client_ip, client_public_key, config, expires_at): 
    db = get_db()
    cursor = db.cursor()

    cursor.execute(
        """
        INSERT INTO vpn_keys (user_id, payment_id, client_id, client_name, client_ip, client_public_key, config, expires_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            payment_id = %s,
            client_id = %s,
            client_name = %s,
            client_ip = %s,
            client_public_key = %s,
            config = %s,
            expires_at = %s
        """,
        (
            user_id, 
            payment_id, 
            client_id,
            client_name,
            client_ip, 
            client_public_key, 
            config, 
            expires_at, 
            payment_id,
            client_id,
            client_name,
            client_ip, 
            client_public_key, 
            config, 
            expires_at
        )
    )

    db.commit()
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


def get_last_paid_payment(tg_id: int):
    """
    Возвращает последний оплаченный платёж пользователя
    """

def mark_vpn_issued(payment_id: str):
    """
    Помечает, что VPN выдан
    """
