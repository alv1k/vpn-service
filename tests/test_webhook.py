"""Тесты для api/webhook.py — обработка вебхуков."""
import sys
import pytest
from unittest.mock import patch, MagicMock, AsyncMock
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


def test_webhook_skips_ip_check_for_test_payment(client):
    """Тестовый платёж (test_mode=true в metadata) — IP-проверка пропускается."""
    response = client.post("/webhook", json={
        "event": "payment.succeeded",
        "object": {
            "id": "test-123",
            "status": "succeeded",
            "metadata": {"test_mode": "true"},
        },
    })
    # Должен пройти мимо IP-проверки (не 403)
    assert response.status_code != 403


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
