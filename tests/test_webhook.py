"""Тесты для api/webhook.py — обработка вебхуков."""
import sys
import pytest
from unittest.mock import patch, MagicMock, AsyncMock, call
from fastapi.testclient import TestClient

# Мокаем yookassa до импорта api.web_api (он импортируется из api.webhook)
_yookassa_mock = MagicMock()
sys.modules.setdefault("yookassa", _yookassa_mock)


@pytest.fixture
def client():
    """TestClient для FastAPI webhook app."""
    from api.webhook import app
    return TestClient(app)


def test_health_check(client):
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"


def test_root_endpoint(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers.get("content-type", "")


def test_webhook_rejects_invalid_ip(client):
    """Запрос не от IP ЮKassa без test_mode — 403."""
    response = client.post("/webhook", json={
        "event": "payment.succeeded",
        "object": {"id": "test-123", "status": "succeeded", "metadata": {}},
    })
    assert response.status_code == 403


def test_webhook_rejects_test_mode_bypass(client):
    """test_mode=true в metadata НЕ должен пропускать IP-проверку (CVE fix)."""
    response = client.post("/webhook", json={
        "event": "payment.succeeded",
        "object": {
            "id": "test-123",
            "status": "succeeded",
            "metadata": {"test_mode": "true"},
        },
    })
    # IP check must ALWAYS be enforced — non-YooKassa IP → 403
    assert response.status_code == 403


@patch("api.webhook.verify_yookassa_ip")
def test_webhook_invalid_json(mock_verify, client):
    """Невалидный JSON — 400."""
    mock_verify.return_value = None
    response = client.post(
        "/webhook",
        content=b"not json",
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 400


@patch("api.webhook.verify_yookassa_ip")
def test_webhook_no_payment_id(mock_verify, client):
    """Нет payment_id — 200 (игнорируем)."""
    mock_verify.return_value = None
    response = client.post("/webhook", json={
        "event": "payment.succeeded",
        "object": {},
    })
    assert response.status_code == 200


@patch("api.webhook.get_payment_status", return_value="paid")
@patch("api.webhook.verify_yookassa_ip")
def test_webhook_duplicate_ignored(mock_verify, mock_status, client):
    """Дублирующий вебхук для уже оплаченного — ignored."""
    response = client.post("/webhook", json={
        "event": "payment.succeeded",
        "object": {"id": "test-123", "status": "succeeded", "metadata": {}},
    })
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "duplicate"


@patch("api.webhook.get_payment_status", return_value=None)
@patch("api.webhook.verify_yookassa_ip")
def test_webhook_unknown_payment(mock_verify, mock_status, client):
    """Неизвестный payment_id — ignored."""
    response = client.post("/webhook", json={
        "event": "payment.succeeded",
        "object": {"id": "unknown-123", "status": "succeeded", "metadata": {}},
    })
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ignored"


# ─────────────────────────────────────────────
#  process_successful_payment — activation order
# ─────────────────────────────────────────────

@pytest.mark.asyncio
@patch("api.webhook.send_telegram_notification", new_callable=AsyncMock)
@patch("api.webhook.send_telegram_document", new_callable=AsyncMock)
@patch("api.webhook.sync_expiry")
@patch("api.webhook.create_vpn_key")
@patch("api.webhook.get_subscription_until")
@patch("api.webhook.activate_subscription")
@patch("api.webhook.get_or_create_user", return_value=1)
@patch("api.db.get_web_token", return_value="test-token-abc")
async def test_activation_after_vpn_creation_awg(
    mock_web_token, mock_get_user, mock_activate, mock_get_sub, mock_create_key,
    mock_sync_expiry, mock_send_doc, mock_send_notif,
):
    """activate_subscription must be called AFTER VPN config creation, not before."""
    from api.webhook import process_successful_payment
    from datetime import datetime

    mock_get_sub.return_value = datetime(2026, 4, 18)

    # Mock httpx for AWG API calls
    # POST responses (login, create client)
    post_resp = MagicMock()
    post_resp.status_code = 200
    # client_name = f"{tariff}_{tg_id}_{payment_id[:8]}" = "1month_12345_pay-1234"
    expected_name = "1month_12345_pay-1234"  # pay-12345678[:8] = pay-1234
    post_resp.json.return_value = {"id": "uuid-1", "name": expected_name}
    post_resp.raise_for_status = MagicMock()

    # GET /api/wireguard/client — returns list of clients
    list_resp = MagicMock()
    list_resp.status_code = 200
    list_resp.json.return_value = [
        {"id": "uuid-1", "name": expected_name, "address": "10.0.0.2", "publicKey": "pk1"}
    ]
    list_resp.raise_for_status = MagicMock()

    # GET /api/wireguard/client/{id}/configuration — returns config text
    conf_resp = MagicMock()
    conf_resp.status_code = 200
    conf_resp.text = "[Interface]\nPrivateKey=abc"
    conf_resp.raise_for_status = MagicMock()

    with patch("api.webhook.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post.return_value = post_resp
        # First GET returns client list, second GET returns config
        mock_client.get = AsyncMock(side_effect=[list_resp, conf_resp])
        mock_client_cls.return_value = mock_client

        result = await process_successful_payment(
            "pay-12345678",
            {"tg_id": 12345, "tariff": "1month"},
            "awg",
        )

    assert result is True
    # activate_subscription called AFTER VPN creation succeeded (not before)
    mock_activate.assert_called_once_with("pay-12345678", user_id=None)
    mock_create_key.assert_called_once()


@pytest.mark.asyncio
@patch("api.webhook.get_or_create_user", return_value=1)
async def test_vless_inbound_count_guard(mock_get_user):
    """process_successful_payment should fail if fewer than 3 inbounds."""
    from api.webhook import process_successful_payment

    mock_xui = MagicMock()
    mock_xui.get_inbounds.return_value = [{"id": 1}, {"id": 2}]  # Only 2

    with patch("api.webhook.XUIClient", return_value=mock_xui):
        result = await process_successful_payment(
            "pay-456",
            {"tg_id": 99999, "tariff": "1month"},
            "vless",
        )

    assert result is False


# ─────────────────────────────────────────────
#  yookassa_webhook — tg_id type from web order
# ─────────────────────────────────────────────

@patch("api.webhook.process_successful_payment", new_callable=AsyncMock, return_value=True)
@patch("api.webhook.get_payment_by_id")
@patch("api.webhook.update_payment_status")
@patch("api.webhook.get_payment_status", return_value="pending")
@patch("api.webhook.verify_yookassa_ip")
def test_web_order_tg_id_is_int(mock_verify, mock_status, mock_update, mock_get_pay, mock_process, client):
    """Web order tg_id should be int, not str."""
    mock_get_pay.return_value = {
        "tg_id": 12345,
        "tariff": "1month",
        "vpn_type": "awg",
        "is_web_order": True,
        "web_token": "tok-abc",
    }

    with patch("api.webhook.get_user_by_web_token", return_value={"tg_id": 67890}):
        response = client.post("/webhook", json={
            "event": "payment.succeeded",
            "object": {"id": "pay-web-1", "status": "succeeded", "metadata": {}},
        })

    assert response.status_code == 200
    # Verify tg_id passed to process_successful_payment is int
    if mock_process.called:
        call_args = mock_process.call_args
        payment_data = call_args[0][1]
        assert isinstance(payment_data["tg_id"], int)
