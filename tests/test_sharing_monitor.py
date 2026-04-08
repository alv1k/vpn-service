"""Tests for bot_xui/sharing_monitor.py — stale IP cleanup."""
import json
import sqlite3
import time
import tempfile
import os
from unittest.mock import patch

import pytest


def _create_test_db(path: str, rows: list):
    """Create a test x-ui SQLite DB with inbound_client_ips table."""
    conn = sqlite3.connect(path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS inbound_client_ips (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            client_email TEXT,
            ips TEXT
        )
    """)
    for client_email, ips_data in rows:
        conn.execute(
            "INSERT INTO inbound_client_ips (client_email, ips) VALUES (?, ?)",
            (client_email, json.dumps(ips_data)),
        )
    conn.commit()
    conn.close()


def _read_ips(path: str, client_email: str) -> list:
    conn = sqlite3.connect(path)
    row = conn.execute(
        "SELECT ips FROM inbound_client_ips WHERE client_email = ?",
        (client_email,),
    ).fetchone()
    conn.close()
    return json.loads(row[0]) if row else []


class TestCleanupStaleIps:

    def test_removes_stale_keeps_fresh(self):
        """IPs older than IP_MAX_AGE are removed, fresh ones kept."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        try:
            now = time.time()
            _create_test_db(db_path, [
                ("tiin_100", [
                    {"ip": "1.2.3.4", "timestamp": now - 10000},  # stale (>2h)
                    {"ip": "5.6.7.8", "timestamp": now - 100},     # fresh
                    {"ip": "9.0.1.2", "timestamp": now - 8000},    # stale
                ]),
            ])

            with patch("bot_xui.sharing_monitor.XUI_DB_PATH", db_path):
                from bot_xui.sharing_monitor import cleanup_stale_ips
                cleanup_stale_ips()

            remaining = _read_ips(db_path, "tiin_100")
            assert len(remaining) == 1
            assert remaining[0]["ip"] == "5.6.7.8"
        finally:
            os.unlink(db_path)

    def test_no_stale_ips_no_changes(self):
        """All fresh IPs → nothing removed, no commit."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        try:
            now = time.time()
            _create_test_db(db_path, [
                ("tiin_200", [
                    {"ip": "1.1.1.1", "timestamp": now - 60},
                    {"ip": "2.2.2.2", "timestamp": now - 120},
                ]),
            ])

            with patch("bot_xui.sharing_monitor.XUI_DB_PATH", db_path):
                from bot_xui.sharing_monitor import cleanup_stale_ips
                cleanup_stale_ips()

            remaining = _read_ips(db_path, "tiin_200")
            assert len(remaining) == 2
        finally:
            os.unlink(db_path)

    def test_empty_ips_skipped(self):
        """Rows with empty or null ips are skipped without error."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        try:
            _create_test_db(db_path, [
                ("tiin_300", []),
                ("tiin_301", None),
            ])
            # Fix: None can't be json.dumps'd — insert raw
            conn = sqlite3.connect(db_path)
            conn.execute(
                "UPDATE inbound_client_ips SET ips = 'null' WHERE client_email = 'tiin_301'"
            )
            conn.commit()
            conn.close()

            with patch("bot_xui.sharing_monitor.XUI_DB_PATH", db_path):
                from bot_xui.sharing_monitor import cleanup_stale_ips
                cleanup_stale_ips()  # should not raise
        finally:
            os.unlink(db_path)

    def test_multiple_clients(self):
        """Handles multiple clients in one pass."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        try:
            now = time.time()
            _create_test_db(db_path, [
                ("tiin_400", [
                    {"ip": "1.1.1.1", "timestamp": now - 10000},
                    {"ip": "2.2.2.2", "timestamp": now - 10},
                ]),
                ("tiin_401", [
                    {"ip": "3.3.3.3", "timestamp": now - 10000},
                ]),
            ])

            with patch("bot_xui.sharing_monitor.XUI_DB_PATH", db_path):
                from bot_xui.sharing_monitor import cleanup_stale_ips
                cleanup_stale_ips()

            assert len(_read_ips(db_path, "tiin_400")) == 1
            assert len(_read_ips(db_path, "tiin_401")) == 0
        finally:
            os.unlink(db_path)

    def test_missing_db_does_not_crash(self):
        """Missing DB file is caught gracefully."""
        with patch("bot_xui.sharing_monitor.XUI_DB_PATH", "/nonexistent/path/x-ui.db"):
            from bot_xui.sharing_monitor import cleanup_stale_ips
            cleanup_stale_ips()  # should not raise
