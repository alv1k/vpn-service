"""Тесты для bot_xui/utils.py — XUIClient, generate_vless_link, format_bytes."""
import sys
import json
from unittest.mock import patch, MagicMock
import pytest

sys.modules.setdefault("yookassa", MagicMock())


# ─────────────────────────────────────────────
#  format_bytes
# ─────────────────────────────────────────────

def test_format_bytes_zero():
    from bot_xui.utils import format_bytes
    assert format_bytes(0) == "0.00 B"


def test_format_bytes_bytes():
    from bot_xui.utils import format_bytes
    assert format_bytes(500) == "500.00 B"


def test_format_bytes_kb():
    from bot_xui.utils import format_bytes
    result = format_bytes(1024)
    assert "KB" in result
    assert "1.00" in result


def test_format_bytes_mb():
    from bot_xui.utils import format_bytes
    result = format_bytes(1024 * 1024)
    assert "MB" in result


def test_format_bytes_gb():
    from bot_xui.utils import format_bytes
    result = format_bytes(1024 ** 3)
    assert "GB" in result


def test_format_bytes_tb():
    from bot_xui.utils import format_bytes
    result = format_bytes(1024 ** 4)
    assert "TB" in result


# ─────────────────────────────────────────────
#  generate_vless_link
# ─────────────────────────────────────────────

def test_generate_vless_link_format():
    from bot_xui.utils import generate_vless_link
    link = generate_vless_link(
        client_id="uuid-123",
        domain="example.com",
        port=443,
        path="/",
        client_name="test-client",
        pbk="publickey",
        sid="ab",
        sni="www.google.com",
    )
    assert link.startswith("vless://uuid-123@example.com:443?")
    assert "encryption=none" in link
    assert "flow=xtls-rprx-vision" in link
    assert "type=tcp" in link
    assert "security=reality" in link
    assert "pbk=publickey" in link
    assert "sid=ab" in link
    assert "sni=www.google.com" in link
    assert "#test-client" in link


def test_generate_vless_link_special_chars_in_name():
    from bot_xui.utils import generate_vless_link
    link = generate_vless_link(
        client_id="id", domain="d.com", port=443, path="/",
        client_name="user with spaces", pbk="pk", sid="s", sni="sni.com",
    )
    assert "user%20with%20spaces" in link


def test_generate_vless_link_custom_fp():
    from bot_xui.utils import generate_vless_link
    link = generate_vless_link(
        client_id="id", domain="d.com", port=443, path="/",
        client_name="c", pbk="pk", sid="s", sni="sni.com", fp="firefox",
    )
    assert "fp=firefox" in link


# ─────────────────────────────────────────────
#  XUIClient
# ─────────────────────────────────────────────

def test_xui_client_init():
    from bot_xui.utils import XUIClient
    c = XUIClient("https://panel.example.com", "admin", "pass")
    assert c.host == "https://panel.example.com"
    assert c._logged_in is False


@patch("bot_xui.utils.requests.Session")
def test_xui_client_login(mock_session_cls):
    from bot_xui.utils import XUIClient
    mock_session = MagicMock()
    mock_session.post.return_value.json.return_value = {"success": True}
    mock_session_cls.return_value = mock_session

    c = XUIClient("https://panel", "admin", "pass")
    c.login()
    assert c._logged_in is True
    mock_session.post.assert_called_once()


@patch("bot_xui.utils.requests.Session")
def test_xui_client_login_failure(mock_session_cls):
    from bot_xui.utils import XUIClient
    mock_session = MagicMock()
    mock_session.post.return_value.json.return_value = {"success": False, "msg": "wrong password"}
    mock_session_cls.return_value = mock_session

    c = XUIClient("https://panel", "admin", "wrong")
    with pytest.raises(Exception, match="Failed to login"):
        c.login()


@patch("bot_xui.utils.requests.Session")
def test_xui_get_inbounds(mock_session_cls):
    from bot_xui.utils import XUIClient
    mock_session = MagicMock()
    # login
    mock_session.post.return_value.json.return_value = {"success": True}
    # get inbounds
    inbound_data = [{"id": 5, "settings": '{"clients":[]}'}]
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"success": True, "obj": inbound_data}
    mock_resp.headers = {"content-type": "application/json"}
    mock_resp.status_code = 200
    mock_session.request.return_value = mock_resp
    mock_session_cls.return_value = mock_session

    c = XUIClient("https://panel", "admin", "pass")
    result = c.get_inbounds()
    assert len(result) == 1
    assert result[0]["id"] == 5


@patch("bot_xui.utils.requests.Session")
def test_xui_get_client_by_email(mock_session_cls):
    from bot_xui.utils import XUIClient
    mock_session = MagicMock()
    mock_session.post.return_value.json.return_value = {"success": True}
    inbound = {
        "id": 5,
        "settings": json.dumps({"clients": [
            {"id": "uuid-1", "email": "client@test", "tgId": 123},
        ]}),
    }
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"success": True, "obj": [inbound]}
    mock_resp.headers = {"content-type": "application/json"}
    mock_resp.status_code = 200
    mock_session.request.return_value = mock_resp
    mock_session_cls.return_value = mock_session

    c = XUIClient("https://panel", "admin", "pass")
    result = c.get_client_by_email("client@test")
    assert result is not None
    assert result["client"]["email"] == "client@test"
    assert result["inbound_id"] == 5


@patch("bot_xui.utils.requests.Session")
def test_xui_get_client_by_email_not_found(mock_session_cls):
    from bot_xui.utils import XUIClient
    mock_session = MagicMock()
    mock_session.post.return_value.json.return_value = {"success": True}
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"success": True, "obj": [
        {"id": 5, "settings": json.dumps({"clients": []})}
    ]}
    mock_resp.headers = {"content-type": "application/json"}
    mock_resp.status_code = 200
    mock_session.request.return_value = mock_resp
    mock_session_cls.return_value = mock_session

    c = XUIClient("https://panel", "admin", "pass")
    assert c.get_client_by_email("nobody@test") is None


@patch("bot_xui.utils.requests.Session")
def test_xui_deactivate_client(mock_session_cls):
    from bot_xui.utils import XUIClient
    mock_session = MagicMock()
    mock_session.post.return_value.json.return_value = {"success": True}
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"success": True}
    mock_resp.headers = {"content-type": "application/json"}
    mock_resp.status_code = 200
    mock_session.request.return_value = mock_resp
    mock_session_cls.return_value = mock_session

    c = XUIClient("https://panel", "admin", "pass")
    client = {"id": "uuid-1", "email": "test", "enable": True}
    result = c.deactivate_client(5, client)
    assert result is True


@patch("bot_xui.utils.requests.Session")
def test_xui_extend_client_expiry(mock_session_cls):
    from bot_xui.utils import XUIClient
    import time
    mock_session = MagicMock()
    mock_session.post.return_value.json.return_value = {"success": True}
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"success": True}
    mock_resp.headers = {"content-type": "application/json"}
    mock_resp.status_code = 200
    mock_session.request.return_value = mock_resp
    mock_session_cls.return_value = mock_session

    c = XUIClient("https://panel", "admin", "pass")
    now_ms = int(time.time() * 1000)
    client = {"id": "uuid-1", "email": "test", "expiryTime": now_ms + 86400000}
    result = c.extend_client_expiry(5, client, 86400000 * 30)
    assert isinstance(result, int)
    assert result > now_ms


@patch("bot_xui.utils.requests.Session")
def test_xui_delete_client(mock_session_cls):
    from bot_xui.utils import XUIClient
    mock_session = MagicMock()
    mock_session.post.return_value.json.return_value = {"success": True}
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"success": True}
    mock_resp.headers = {"content-type": "application/json"}
    mock_resp.status_code = 200
    mock_session.request.return_value = mock_resp
    mock_session_cls.return_value = mock_session

    c = XUIClient("https://panel", "admin", "pass")
    assert c.delete_client(5, "test-email") is True


@patch("bot_xui.utils.requests.Session")
def test_xui_reset_client_traffic(mock_session_cls):
    from bot_xui.utils import XUIClient
    mock_session = MagicMock()
    mock_session.post.return_value.json.return_value = {"success": True}
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"success": True}
    mock_resp.headers = {"content-type": "application/json"}
    mock_resp.status_code = 200
    mock_session.request.return_value = mock_resp
    mock_session_cls.return_value = mock_session

    c = XUIClient("https://panel", "admin", "pass")
    assert c.reset_client_traffic(5, "test-email") is True
