"""Database operations for AWG clients and server config."""
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

import mysql.connector

from .config import MYSQL_HOST, MYSQL_PORT, MYSQL_USER, MYSQL_PASSWORD, MYSQL_DATABASE

logger = logging.getLogger(__name__)

INIT_SQL = """
CREATE TABLE IF NOT EXISTS awg_clients (
    id VARCHAR(36) PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    address VARCHAR(15) NOT NULL,
    private_key VARCHAR(64) NOT NULL,
    public_key VARCHAR(64) NOT NULL,
    preshared_key VARCHAR(64) NOT NULL,
    enabled TINYINT(1) NOT NULL DEFAULT 1,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY idx_address (address),
    UNIQUE KEY idx_public_key (public_key)
);

CREATE TABLE IF NOT EXISTS awg_server (
    id INT PRIMARY KEY DEFAULT 1,
    private_key VARCHAR(64) NOT NULL,
    public_key VARCHAR(64) NOT NULL,
    listen_port INT NOT NULL DEFAULT 51888,
    jc INT NOT NULL DEFAULT 5,
    jmin INT NOT NULL DEFAULT 50,
    jmax INT NOT NULL DEFAULT 1000,
    s1 INT NOT NULL DEFAULT 89,
    s2 INT NOT NULL DEFAULT 121,
    h1 BIGINT NOT NULL DEFAULT 0,
    h2 BIGINT NOT NULL DEFAULT 0,
    h3 BIGINT NOT NULL DEFAULT 0,
    h4 BIGINT NOT NULL DEFAULT 0,
    i1 TEXT DEFAULT NULL,
    i2 TEXT DEFAULT NULL,
    i3 TEXT DEFAULT NULL,
    i4 TEXT DEFAULT NULL,
    i5 TEXT DEFAULT NULL
);
"""


def _get_conn():
    return mysql.connector.connect(
        host=MYSQL_HOST, port=MYSQL_PORT,
        user=MYSQL_USER, password=MYSQL_PASSWORD,
        database=MYSQL_DATABASE,
    )


def init_db():
    """Create tables if not exist."""
    conn = _get_conn()
    cur = conn.cursor()
    for stmt in INIT_SQL.strip().split(";"):
        stmt = stmt.strip()
        if stmt:
            cur.execute(stmt)
    conn.commit()
    cur.close()
    conn.close()
    logger.info("AWG DB tables initialized")


def get_server_config() -> Optional[dict]:
    conn = _get_conn()
    cur = conn.cursor(dictionary=True)
    cur.execute("SELECT * FROM awg_server WHERE id=1")
    row = cur.fetchone()
    cur.close()
    conn.close()
    return row


def save_server_config(cfg: dict):
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO awg_server (id, private_key, public_key, listen_port,
            jc, jmin, jmax, s1, s2, h1, h2, h3, h4,
            i1, i2, i3, i4, i5)
        VALUES (1, %(private_key)s, %(public_key)s, %(listen_port)s,
            %(jc)s, %(jmin)s, %(jmax)s, %(s1)s, %(s2)s,
            %(h1)s, %(h2)s, %(h3)s, %(h4)s,
            %(i1)s, %(i2)s, %(i3)s, %(i4)s, %(i5)s)
        ON DUPLICATE KEY UPDATE
            private_key=VALUES(private_key), public_key=VALUES(public_key),
            listen_port=VALUES(listen_port),
            jc=VALUES(jc), jmin=VALUES(jmin), jmax=VALUES(jmax),
            s1=VALUES(s1), s2=VALUES(s2),
            h1=VALUES(h1), h2=VALUES(h2), h3=VALUES(h3), h4=VALUES(h4),
            i1=VALUES(i1), i2=VALUES(i2), i3=VALUES(i3), i4=VALUES(i4), i5=VALUES(i5)
    """, cfg)
    conn.commit()
    cur.close()
    conn.close()


def list_clients() -> list[dict]:
    conn = _get_conn()
    cur = conn.cursor(dictionary=True)
    cur.execute("SELECT * FROM awg_clients ORDER BY created_at")
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows


def get_client(client_id: str) -> Optional[dict]:
    conn = _get_conn()
    cur = conn.cursor(dictionary=True)
    cur.execute("SELECT * FROM awg_clients WHERE id=%s", (client_id,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    return row


def create_client(name: str, address: str, private_key: str, public_key: str, preshared_key: str) -> dict:
    client_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO awg_clients (id, name, address, private_key, public_key, preshared_key, enabled, created_at, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, 1, %s, %s)
    """, (client_id, name, address, private_key, public_key, preshared_key, now, now))
    conn.commit()
    cur.close()
    conn.close()
    return {
        "id": client_id, "name": name, "address": address,
        "private_key": private_key, "public_key": public_key,
        "preshared_key": preshared_key, "enabled": True,
        "created_at": now, "updated_at": now,
    }


def delete_client(client_id: str) -> bool:
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM awg_clients WHERE id=%s", (client_id,))
    affected = cur.rowcount
    conn.commit()
    cur.close()
    conn.close()
    return affected > 0


def update_client_enabled(client_id: str, enabled: bool) -> bool:
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute("UPDATE awg_clients SET enabled=%s WHERE id=%s", (int(enabled), client_id))
    affected = cur.rowcount
    conn.commit()
    cur.close()
    conn.close()
    return affected > 0


def update_client_name(client_id: str, name: str) -> bool:
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute("UPDATE awg_clients SET name=%s WHERE id=%s", (name, client_id))
    affected = cur.rowcount
    conn.commit()
    cur.close()
    conn.close()
    return affected > 0


def update_client_address(client_id: str, address: str) -> bool:
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute("UPDATE awg_clients SET address=%s WHERE id=%s", (address, client_id))
    affected = cur.rowcount
    conn.commit()
    cur.close()
    conn.close()
    return affected > 0


def next_free_address() -> str:
    """Find next free IP in 10.10.0.0/24 (skip .1 = server)."""
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute("SELECT address FROM awg_clients")
    used = {r[0] for r in cur.fetchall()}
    cur.close()
    conn.close()
    for i in range(2, 255):
        addr = f"10.10.0.{i}"
        if addr not in used:
            return addr
    raise RuntimeError("No free addresses in 10.10.0.0/24")
