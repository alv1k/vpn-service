"""Тесты для SoftEther VPN — CLI wrapper (bot_xui/softether.py) и фабрика конфигов."""
import json
from unittest.mock import patch, MagicMock
from subprocess import CompletedProcess


# ─────────────────────────────────────────────
#  softether._run
# ─────────────────────────────────────────────

@patch("bot_xui.softether.subprocess.run")
def test_run_success(mock_run):
    mock_run.return_value = CompletedProcess(args=[], returncode=0, stdout="OK\n", stderr="")

    from bot_xui.softether import _run
    result = _run("UserList")

    assert result == "OK\n"
    mock_run.assert_called_once()
    cmd = mock_run.call_args[0][0]
    assert "UserList" in cmd


@patch("bot_xui.softether.subprocess.run")
def test_run_failure_raises(mock_run):
    mock_run.return_value = CompletedProcess(args=[], returncode=1, stdout="", stderr="Error")

    import pytest
    from bot_xui.softether import _run

    with pytest.raises(RuntimeError, match="vpncmd failed"):
        _run("BadCommand")


# ─────────────────────────────────────────────
#  softether.create_user
# ─────────────────────────────────────────────

@patch("bot_xui.softether._run")
def test_create_user_success(mock_run):
    mock_run.return_value = "OK"

    from bot_xui.softether import create_user
    result = create_user("testuser", "testpass")

    assert result is True
    assert mock_run.call_count == 2
    # First call: UserCreate
    assert "UserCreate" in mock_run.call_args_list[0][0][0]
    # Second call: UserPasswordSet
    assert "UserPasswordSet" in mock_run.call_args_list[1][0][0]


@patch("bot_xui.softether._run", side_effect=RuntimeError("fail"))
def test_create_user_failure(mock_run):
    from bot_xui.softether import create_user
    result = create_user("testuser", "testpass")

    assert result is False


# ─────────────────────────────────────────────
#  softether.set_user_expiry
# ─────────────────────────────────────────────

@patch("bot_xui.softether._run")
def test_set_user_expiry_success(mock_run):
    mock_run.return_value = "OK"

    from bot_xui.softether import set_user_expiry
    result = set_user_expiry("testuser", "2026/04/01")

    assert result is True
    args = mock_run.call_args[0]
    assert "UserExpiresSet" in args
    assert "/EXPIRES:2026/04/01 23:59:59" in args


@patch("bot_xui.softether._run", side_effect=RuntimeError("fail"))
def test_set_user_expiry_failure(mock_run):
    from bot_xui.softether import set_user_expiry
    assert set_user_expiry("testuser", "2026/04/01") is False


# ─────────────────────────────────────────────
#  softether.delete_user
# ─────────────────────────────────────────────

@patch("bot_xui.softether._run")
def test_delete_user_success(mock_run):
    mock_run.return_value = "OK"

    from bot_xui.softether import delete_user
    assert delete_user("testuser") is True
    assert "UserDelete" in mock_run.call_args[0][0]


@patch("bot_xui.softether._run", side_effect=RuntimeError("fail"))
def test_delete_user_failure(mock_run):
    from bot_xui.softether import delete_user
    assert delete_user("testuser") is False


# ─────────────────────────────────────────────
#  softether.disable_user
# ─────────────────────────────────────────────

@patch("bot_xui.softether._run")
def test_disable_user_sets_past_expiry(mock_run):
    mock_run.return_value = "OK"

    from bot_xui.softether import disable_user
    assert disable_user("testuser") is True

    args = mock_run.call_args[0]
    assert "/EXPIRES:2000/01/01 00:00:00" in args


@patch("bot_xui.softether._run", side_effect=RuntimeError("fail"))
def test_disable_user_failure(mock_run):
    from bot_xui.softether import disable_user
    assert disable_user("testuser") is False


# ─────────────────────────────────────────────
#  vpn_factory._make_softether_vpn_file
# ─────────────────────────────────────────────

def test_make_vpn_file_content():
    from bot_xui.vpn_factory import _make_softether_vpn_file

    bio = _make_softether_vpn_file("myuser", "mypass")

    content = bio.getvalue().decode("utf-8")
    assert "Username myuser" in content
    assert "PlainPassword mypass" in content
    assert "HashedPassword" in content
    assert bio.name == "tiin_vpn_myuser.vpn"


def test_make_vpn_file_contains_server_info():
    from bot_xui.vpn_factory import _make_softether_vpn_file, SOFTETHER_CONNECT_HOST, SOFTETHER_HUB

    bio = _make_softether_vpn_file("u", "p")
    content = bio.getvalue().decode("utf-8")

    assert f"Hostname {SOFTETHER_CONNECT_HOST}" in content
    assert f"HubName {SOFTETHER_HUB}" in content


# ─────────────────────────────────────────────
#  vpn_factory.create_softether_config
# ─────────────────────────────────────────────

@patch("bot_xui.vpn_factory.softether")
def test_create_softether_config_with_days(mock_se):
    mock_se.create_user.return_value = True
    mock_se.set_user_expiry.return_value = True

    from bot_xui.vpn_factory import create_softether_config
    result = create_softether_config(tg_id=123456, days=30)

    assert result["username"].startswith("se_123456_")
    assert len(result["password"]) > 0
    assert result["expires_at"] is not None

    config = json.loads(result["config"])
    assert config["username"] == result["username"]
    assert config["password"] == result["password"]
    assert "vpn_file" in result

    mock_se.create_user.assert_called_once()
    mock_se.set_user_expiry.assert_called_once()


@patch("bot_xui.vpn_factory.softether")
def test_create_softether_config_with_hours(mock_se):
    mock_se.create_user.return_value = True
    mock_se.set_user_expiry.return_value = True

    from bot_xui.vpn_factory import create_softether_config
    result = create_softether_config(tg_id=999, hours=24)

    assert result["username"].startswith("se_999_")
    assert result["expires_at"] is not None


@patch("bot_xui.vpn_factory.softether")
def test_create_softether_config_default_30_days(mock_se):
    mock_se.create_user.return_value = True
    mock_se.set_user_expiry.return_value = True

    from bot_xui.vpn_factory import create_softether_config
    result = create_softether_config(tg_id=111)

    # Should work without days/hours (defaults to 30 days)
    assert result["username"].startswith("se_111_")


@patch("bot_xui.vpn_factory.softether")
def test_create_softether_config_failure(mock_se):
    mock_se.create_user.return_value = False

    import pytest
    from bot_xui.vpn_factory import create_softether_config

    with pytest.raises(RuntimeError, match="Failed to create SoftEther user"):
        create_softether_config(tg_id=123456, days=30)


# ─────────────────────────────────────────────
#  vpn_factory._softether_credentials_text
# ─────────────────────────────────────────────

def test_credentials_text_contains_data():
    from bot_xui.vpn_factory import _softether_credentials_text

    text = _softether_credentials_text("myuser", "mypass")

    assert "myuser" in text
    assert "mypass" in text
    assert "Сервер" in text
    assert "Порт" in text
    assert "Hub" in text


# ─────────────────────────────────────────────
#  softether.list_sessions
# ─────────────────────────────────────────────

@patch("bot_xui.softether._run")
def test_list_sessions_parses_output(mock_run):
    mock_run.return_value = """SessionList command - List sessions
---
Session Name                     |SES-LAPTOP-PC
User Name                        |john
Source Host Name                  |192.168.1.50
Transfer Bytes                   |12,345,678
---
Session Name                     |SES-PHONE
User Name                        |jane
Source Host Name                  |10.0.0.5
Transfer Bytes                   |500,000
---
Session Name                     |SES-NAT
User Name                        |SecureNAT
Source Host Name                  |localhost
Transfer Bytes                   |0
"""

    from bot_xui.softether import list_sessions
    sessions = list_sessions()

    assert len(sessions) == 2  # SecureNAT filtered out
    assert sessions[0]["username"] == "john"
    assert sessions[0]["source"] == "192.168.1.50"
    assert sessions[0]["transfer_bytes"] == 12345678
    assert sessions[1]["username"] == "jane"


@patch("bot_xui.softether._run")
def test_list_sessions_empty(mock_run):
    mock_run.return_value = "SessionList command - List sessions\n"

    from bot_xui.softether import list_sessions
    sessions = list_sessions()

    assert sessions == []


@patch("bot_xui.softether._run", side_effect=RuntimeError("fail"))
def test_list_sessions_error_returns_empty(mock_run):
    from bot_xui.softether import list_sessions
    assert list_sessions() == []


# ─────────────────────────────────────────────
#  softether.list_users
# ─────────────────────────────────────────────

@patch("bot_xui.softether._run")
def test_list_users_parses_output(mock_run):
    mock_run.return_value = """UserList command - List users
---
User Name                        |se_123_abc
Auth Method                      |Password Authentication
Num Logins                       |5
Last Login                       |2026-03-18 14:30:00
Expiration Date                  |2026-04-18
Transfer Bytes                   |1,000,000
---
User Name                        |se_456_def
Auth Method                      |Password Authentication
Num Logins                       |0
Last Login                       |(None)
Expiration Date                  |No Expiration
Transfer Bytes                   |0
"""

    from bot_xui.softether import list_users
    users = list_users()

    assert len(users) == 2
    assert users[0]["username"] == "se_123_abc"
    assert users[0]["auth"] == "Password Authentication"
    assert users[0]["num_logins"] == 5
    assert users[0]["last_login"] == "2026-03-18 14:30:00"
    assert users[0]["expires"] == "2026-04-18"
    assert users[0]["transfer_bytes"] == 1000000
    assert users[1]["last_login"] is None
    assert users[1]["expires"] is None
    assert users[1]["num_logins"] == 0


@patch("bot_xui.softether._run")
def test_list_users_empty(mock_run):
    mock_run.return_value = "UserList command - List users\n"

    from bot_xui.softether import list_users
    assert list_users() == []


@patch("bot_xui.softether._run", side_effect=RuntimeError("fail"))
def test_list_users_error_returns_empty(mock_run):
    from bot_xui.softether import list_users
    assert list_users() == []
