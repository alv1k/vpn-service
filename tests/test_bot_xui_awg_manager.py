"""
Unit tests for bot_xui/awg_manager.py (3x-ui Inbound 17 AmneziaWG manager).
Verifies synchronization with inbounds.settings, clients, and client_inbounds tables.
"""
import json
import sqlite3
import pytest
from unittest.mock import patch

from bot_xui import awg_manager


@pytest.fixture
def mock_awg_db(tmp_path):
    """Creates a temporary SQLite database mimicking /etc/x-ui/x-ui.db schema."""
    db_file = tmp_path / "x-ui-test.db"
    conn = sqlite3.connect(db_file)
    cur = conn.cursor()

    # inbounds table
    cur.execute("""
        CREATE TABLE inbounds (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            up INTEGER,
            down INTEGER,
            total INTEGER,
            remark TEXT,
            enable INTEGER,
            expiry_time INTEGER,
            listen TEXT,
            port INTEGER,
            protocol TEXT,
            tag TEXT,
            stream_settings TEXT,
            sniffing TEXT,
            allocate TEXT,
            settings TEXT
        )
    """)

    # clients table
    cur.execute("""
        CREATE TABLE clients (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT NOT NULL,
            sub_id TEXT,
            uuid TEXT,
            password TEXT,
            auth TEXT,
            flow TEXT,
            security TEXT,
            reverse TEXT,
            limit_ip INTEGER DEFAULT 0,
            total_gb INTEGER DEFAULT 0,
            expiry_time INTEGER DEFAULT 0,
            enable NUMERIC DEFAULT 1,
            tg_id INTEGER,
            group_name TEXT DEFAULT '',
            comment TEXT,
            reset INTEGER DEFAULT 0,
            created_at INTEGER,
            updated_at INTEGER,
            wg_private_key TEXT,
            wg_public_key TEXT,
            wg_allowed_ips TEXT,
            wg_pre_shared_key TEXT,
            wg_keep_alive INTEGER DEFAULT 0,
            secret TEXT,
            ad_tag TEXT DEFAULT '',
            wg_forwarded_ports TEXT,
            limit_hwid INTEGER DEFAULT 0,
            reset_day INTEGER DEFAULT 0,
            reset_max INTEGER DEFAULT 0,
            traffic_reset TEXT DEFAULT 'never',
            traffic_reset_day INTEGER DEFAULT 1,
            sync_orphaned_at INTEGER DEFAULT 0,
            reset_weekday INTEGER DEFAULT 0,
            wg_keep_alive_range TEXT DEFAULT '',
            client_host_rule_ids TEXT
        )
    """)

    # client_inbounds table
    cur.execute("""
        CREATE TABLE client_inbounds (
            client_id INTEGER,
            inbound_id INTEGER,
            flow_override TEXT,
            created_at INTEGER,
            PRIMARY KEY (client_id, inbound_id)
        )
    """)

    # client_traffics table
    cur.execute("""
        CREATE TABLE client_traffics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            inbound_id INTEGER,
            enable INTEGER,
            email TEXT,
            up INTEGER,
            down INTEGER,
            expiry_time INTEGER,
            total INTEGER,
            reset INTEGER
        )
    """)

    # Populate inbound 17
    server_settings = {
        "server": {
            "publicKey": "server_public_key_abc123=",
            "jc": 5, "jmin": 50, "jmax": 1000,
            "s1": 50, "s2": 124,
            "h1": "536125", "h2": "7458971", "h3": "69317959", "h4": "672102228"
        },
        "clients": []
    }
    cur.execute(
        "INSERT INTO inbounds (id, port, protocol, tag, settings) VALUES (?, ?, ?, ?, ?)",
        (17, 443, "amneziawg", "in-51822-udp", json.dumps(server_settings))
    )

    conn.commit()
    conn.close()
    return str(db_file)


def test_get_or_create_new_client(mock_awg_db):
    """Test creating a brand new AWG client populates inbounds, clients, and client_inbounds."""
    with patch.object(awg_manager, "DB_PATH", mock_awg_db):
        res = awg_manager.get_or_create_3xui_awg_client(tg_id=12345, expiry_ms=1800000000000)

        assert res["client_name"] == "awg_12345"
        assert res["client_ip"] == "10.8.1.2/32"
        assert "server_public_key_abc123=" in res["config"]
        assert "PrivateKey = " in res["config"]

        # Verify DB contents
        conn = sqlite3.connect(mock_awg_db)
        cur = conn.cursor()

        # Inbounds settings
        cur.execute("SELECT settings FROM inbounds WHERE id = 17")
        settings = json.loads(cur.fetchone()[0])
        assert len(settings["clients"]) == 1
        assert settings["clients"][0]["email"] == "tiin_12345"

        # Clients table
        cur.execute("SELECT id, email, wg_public_key, wg_allowed_ips, expiry_time FROM clients WHERE email = 'tiin_12345'")
        crow = cur.fetchone()
        assert crow is not None
        cid, email, pub, ips, exp = crow
        assert email == "tiin_12345"
        assert pub == res["public_key"]
        assert ips == "10.8.1.2/32"
        assert exp == 1800000000000

        # Client_inbounds table
        cur.execute("SELECT client_id, inbound_id FROM client_inbounds WHERE client_id = ?", (cid,))
        ci = cur.fetchone()
        assert ci == (cid, 17)

        # Client_traffics table
        cur.execute("SELECT email, inbound_id FROM client_traffics WHERE email = 'tiin_12345'")
        ct = cur.fetchone()
        assert ct == ("tiin_12345", 17)

        conn.close()


def test_existing_client_updates_expiry_and_syncs_inbound(mock_awg_db):
    """Test existing client retrieval and expiry update preserves keys and updates client_inbounds."""
    with patch.object(awg_manager, "DB_PATH", mock_awg_db):
        res1 = awg_manager.get_or_create_3xui_awg_client(tg_id=999, expiry_ms=1700000000000)
        orig_priv = res1["private_key"]
        orig_pub = res1["public_key"]

        # Re-fetch with new expiry
        res2 = awg_manager.get_or_create_3xui_awg_client(tg_id=999, expiry_ms=1900000000000)

        assert res2["private_key"] == orig_priv
        assert res2["public_key"] == orig_pub
        assert res2["client_ip"] == "10.8.1.2/32"

        conn = sqlite3.connect(mock_awg_db)
        cur = conn.cursor()
        cur.execute("SELECT expiry_time FROM clients WHERE email = 'tiin_999'")
        assert cur.fetchone()[0] == 1900000000000

        cur.execute("SELECT inbound_id FROM client_inbounds WHERE client_id = (SELECT id FROM clients WHERE email = 'tiin_999')")
        assert cur.fetchall() == [(17,)]
        conn.close()


def test_existing_vless_user_gets_awg_attached(mock_awg_db):
    """Test that a user who already had a VLESS profile in clients table gets AWG keys updated on that row."""
    # Pre-insert existing user into clients (as created by VLESS Reality)
    conn = sqlite3.connect(mock_awg_db)
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO clients (id, email, sub_id, uuid, password, auth, enable, expiry_time, tg_id)
        VALUES (42, 'tiin_88888', 'existing-sub-id', 'existing-uuid', 'pass', 'auth', 1, 1750000000000, 88888)
    """)
    conn.commit()
    conn.close()

    with patch.object(awg_manager, "DB_PATH", mock_awg_db):
        res = awg_manager.get_or_create_3xui_awg_client(tg_id=88888, expiry_ms=1850000000000)

        conn = sqlite3.connect(mock_awg_db)
        cur = conn.cursor()

        # Should update row 42, NOT create a duplicate
        cur.execute("SELECT id, sub_id, wg_public_key, wg_allowed_ips, expiry_time FROM clients WHERE email = 'tiin_88888'")
        rows = cur.fetchall()
        assert len(rows) == 1
        cid, sub_id, pub, ips, exp = rows[0]
        assert cid == 42
        assert sub_id == "existing-sub-id"
        assert pub == res["public_key"]
        assert ips == "10.8.1.2/32"
        assert exp == 1850000000000

        # client_inbounds attached
        cur.execute("SELECT client_id, inbound_id FROM client_inbounds WHERE client_id = 42")
        assert cur.fetchone() == (42, 17)
        conn.close()
