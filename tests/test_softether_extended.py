"""Тесты для bot_xui/softether.py — disable_user, list_sessions, list_users."""
from unittest.mock import patch, MagicMock
import pytest


# ─────────────────────────────────────────────
#  disable_user
# ─────────────────────────────────────────────

@patch("bot_xui.softether._run")
def test_disable_user_success(mock_run):
    from bot_xui.softether import disable_user
    mock_run.return_value = "OK"
    assert disable_user("testuser") is True
    mock_run.assert_called_once_with("UserExpiresSet", "testuser", "/EXPIRES:2000/01/01 00:00:00")


@patch("bot_xui.softether._run", side_effect=RuntimeError("vpncmd failed"))
def test_disable_user_failure(mock_run):
    from bot_xui.softether import disable_user
    assert disable_user("testuser") is False


# ─────────────────────────────────────────────
#  list_sessions
# ─────────────────────────────────────────────

@patch("bot_xui.softether._run")
def test_list_sessions_parses_output(mock_run):
    from bot_xui.softether import list_sessions
    mock_run.return_value = (
        "Session Name|SES-1\n"
        "User Name|alice\n"
        "Source Host Name|192.168.1.1\n"
        "Transfer Bytes|1,234,567\n"
        "Session Name|SES-2\n"
        "User Name|bob\n"
        "Source Host Name|10.0.0.1\n"
        "Transfer Bytes|999\n"
    )
    sessions = list_sessions()
    assert len(sessions) == 2
    assert sessions[0]["username"] == "alice"
    assert sessions[0]["transfer_bytes"] == 1234567
    assert sessions[1]["username"] == "bob"


@patch("bot_xui.softether._run")
def test_list_sessions_filters_securnat(mock_run):
    from bot_xui.softether import list_sessions
    mock_run.return_value = (
        "Session Name|SES-NAT\n"
        "User Name|SecureNAT\n"
        "Source Host Name|localhost\n"
        "Session Name|SES-1\n"
        "User Name|real_user\n"
        "Source Host Name|1.1.1.1\n"
    )
    sessions = list_sessions()
    assert len(sessions) == 1
    assert sessions[0]["username"] == "real_user"


@patch("bot_xui.softether._run", side_effect=RuntimeError("fail"))
def test_list_sessions_error(mock_run):
    from bot_xui.softether import list_sessions
    assert list_sessions() == []


# ─────────────────────────────────────────────
#  list_users
# ─────────────────────────────────────────────

@patch("bot_xui.softether._run")
def test_list_users_parses_output(mock_run):
    from bot_xui.softether import list_users
    mock_run.return_value = (
        "User Name|admin\n"
        "Auth Method|Password Authentication\n"
        "Num Logins|5\n"
        "Last Login|2026-03-28\n"
        "Expiration Date|2026-12-31\n"
        "Transfer Bytes|1,000,000\n"
        "User Name|guest\n"
        "Auth Method|Password Authentication\n"
        "Num Logins|0\n"
        "Last Login|(None)\n"
        "Expiration Date|No Expiration\n"
        "Transfer Bytes|0\n"
    )
    users = list_users()
    assert len(users) == 2
    assert users[0]["username"] == "admin"
    assert users[0]["num_logins"] == 5
    assert users[0]["expires"] == "2026-12-31"
    assert users[1]["last_login"] is None
    assert users[1]["expires"] is None


@patch("bot_xui.softether._run", side_effect=RuntimeError("fail"))
def test_list_users_error(mock_run):
    from bot_xui.softether import list_users
    assert list_users() == []
