"""Тесты для api/web_portal.py — XSS protection and injection fixes."""
import sys
from unittest.mock import patch, MagicMock
import pytest

# Mock yookassa before importing
sys.modules.setdefault("yookassa", MagicMock())


@pytest.fixture
def client():
    from api.webhook import app
    from fastapi.testclient import TestClient
    return TestClient(app)


def test_xss_in_first_name_is_escaped():
    """html_mod.escape is called on first_name to prevent XSS."""
    import html as html_mod

    malicious = '<script>alert("xss")</script>'
    escaped = html_mod.escape(malicious)
    assert "<script>" not in escaped
    assert "&lt;script&gt;" in escaped


@patch("api.web_portal.is_vless_test_activated_by_id", return_value=False)
@patch("api.web_portal.get_keys_by_user_id", return_value=[])
@patch("api.web_portal.get_keys_by_tg_id", return_value=[])
@patch("api.web_portal.get_user_by_web_token")
def test_sub_url_empty_is_json_encoded(mock_get_user, mock_keys, mock_keys_uid, mock_test, client):
    """SUB_URL should be JSON-encoded (empty string becomes ""), not raw interpolation."""
    mock_get_user.return_value = {
        "id": 1, "tg_id": 456, "first_name": "Test",
        "subscription_until": None, "email": None,
    }

    response = client.get("/my/some-token")

    assert response.status_code == 200
    html = response.text
    # SUB_URL should be a proper JS value (JSON string ""), not {sub_url} or bare ;
    assert 'const SUB_URL = ""' in html
    assert "const SUB_URL = ;" not in html


@patch("api.web_portal.is_vless_test_activated_by_id", return_value=True)
@patch("api.web_portal.get_keys_by_user_id", return_value=[])
@patch("api.web_portal.get_keys_by_tg_id")
@patch("api.web_portal.get_user_by_web_token")
def test_sub_url_with_value_is_json_string(mock_get_user, mock_keys, mock_keys_uid, mock_test, client):
    """SUB_URL with a real link should be properly JSON-quoted."""
    from datetime import datetime, timedelta
    future = datetime.now() + timedelta(days=30)

    mock_get_user.return_value = {
        "id": 2, "tg_id": 789, "first_name": "User",
        "subscription_until": future, "email": None,
    }
    mock_keys.return_value = [{
        "vpn_type": "vless",
        "subscription_link": "https://example.com/sub/abc",
        "expires_at": future,
    }]

    response = client.get("/my/token-with-sub")

    assert response.status_code == 200
    html = response.text
    assert 'const SUB_URL = "https://example.com/sub/abc"' in html


@patch("api.web_portal.is_vless_test_activated_by_id", return_value=True)
@patch("api.web_portal.get_keys_by_user_id", return_value=[])
@patch("api.web_portal.get_keys_by_tg_id")
@patch("api.web_portal.get_user_by_web_token")
def test_sub_url_injection_is_prevented(mock_get_user, mock_keys, mock_keys_uid, mock_test, client):
    """A sub_url containing quotes should be safely JSON-encoded."""
    from datetime import datetime, timedelta
    future = datetime.now() + timedelta(days=30)

    mock_get_user.return_value = {
        "id": 3, "tg_id": 101, "first_name": "User",
        "subscription_until": future, "email": None,
    }
    mock_keys.return_value = [{
        "vpn_type": "vless",
        "subscription_link": 'http://evil";alert(1)//',
        "expires_at": future,
    }]

    response = client.get("/my/token-inject")

    assert response.status_code == 200
    html = response.text
    # The quote should be escaped by json.dumps
    assert 'alert(1)' in html  # the text is there
    assert 'const SUB_URL = "http://evil";alert' not in html  # but not as executable JS


@patch("api.web_portal.get_user_by_web_token", return_value=None)
def test_invalid_token_returns_404(mock_get_user, client):
    """Invalid token should return 404."""
    response = client.get("/my/bad-token")
    assert response.status_code == 404
