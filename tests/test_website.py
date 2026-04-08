"""Тесты для website — VPN-подключение через сайт (web API flow)."""
import sys
from unittest.mock import patch, MagicMock
import pytest

# Mock yookassa before importing
_yookassa_mock = MagicMock()
sys.modules.setdefault("yookassa", _yookassa_mock)


@pytest.fixture
def client():
    from api.webhook import app
    from fastapi.testclient import TestClient
    return TestClient(app)


# ─────────────────────────────────────────────
#  Landing page
# ─────────────────────────────────────────────

def test_landing_page_loads(client):
    """Главная страница отдаётся как HTML."""
    response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers.get("content-type", "")
    assert "TIIN" in response.text


def test_landing_has_support_link(client):
    """Footer содержит ссылку на страницу поддержки."""
    response = client.get("/")
    assert "Поддержка" in response.text
    assert "showPage('support')" in response.text


# ─────────────────────────────────────────────
#  Tariffs
# ─────────────────────────────────────────────

def test_tariffs_returns_list(client):
    """GET /api/web/tariffs возвращает непустой список."""
    response = client.get("/api/web/tariffs")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) > 0


def test_tariffs_have_required_fields(client):
    """Каждый тариф содержит обязательные поля."""
    response = client.get("/api/web/tariffs")
    data = response.json()
    for t in data:
        assert "id" in t
        assert "name" in t
        assert "price" in t
        assert "days" in t
        assert t["price"] >= 0


def test_tariffs_exclude_test(client):
    """Тестовые тарифы не отдаются на сайт."""
    response = client.get("/api/web/tariffs")
    data = response.json()
    ids = [t["id"] for t in data]
    assert "admin_test" not in ids


# ─────────────────────────────────────────────
#  Order flow
# ─────────────────────────────────────────────

def test_order_rejects_without_code(client):
    """Заказ без верного кода верификации — 400."""
    response = client.post("/api/web/order", json={
        "email": "test@example.com",
        "tariff_id": "monthly_30d",
        "code": "999999",
    })
    assert response.status_code == 400


def test_order_rejects_invalid_tariff(client):
    """Заказ с несуществующим тарифом — 400."""
    with patch("api.notifications.verify_code", return_value=True):
        response = client.post("/api/web/order", json={
            "email": "test@example.com",
            "tariff_id": "nonexistent_tariff",
            "code": "123456",
        })
    assert response.status_code == 400


@patch("api.db.create_payment")
@patch("api.web_api.Payment")
@patch("api.web_api._get_or_create_web_user")
def test_order_creates_payment(mock_user, mock_payment_cls, mock_create_pay, client):
    """Полный flow: код верен → создаётся платёж YooKassa."""
    mock_user.return_value = {"id": 1, "web_token": "tok-abc", "tg_id": None}

    mock_payment = MagicMock()
    mock_payment.id = "pay-test-123"
    mock_payment.confirmation.confirmation_url = "https://yookassa.ru/pay/123"
    mock_payment_cls.create.return_value = mock_payment

    with patch("api.notifications.verify_code", return_value=True):
        response = client.post("/api/web/order", json={
            "email": "buyer@example.com",
            "tariff_id": "monthly_30d",
            "code": "123456",
        })

    assert response.status_code == 200
    data = response.json()
    assert "payment_url" in data
    assert "payment_id" in data
    assert data["payment_id"] == "pay-test-123"
    mock_create_pay.assert_called_once()


@patch("api.db.create_payment")
@patch("api.web_api.Payment")
@patch("api.web_api._get_or_create_web_user")
def test_order_with_promo_applies_discount(mock_user, mock_payment_cls, mock_create_pay, client):
    """Промокод применяется к цене заказа."""
    mock_user.return_value = {"id": 1, "web_token": "tok-abc", "tg_id": None}

    mock_payment = MagicMock()
    mock_payment.id = "pay-promo-1"
    mock_payment.confirmation.confirmation_url = "https://yookassa.ru/pay/p1"
    mock_payment_cls.create.return_value = mock_payment

    fake_promo = {
        "is_active": True, "type": "discount", "value": 50,
        "expires_at": None, "max_uses": None, "used_count": 0,
    }

    with patch("api.notifications.verify_code", return_value=True), \
         patch("api.db.get_promocode", return_value=fake_promo):
        response = client.post("/api/web/order", json={
            "email": "buyer@example.com",
            "tariff_id": "monthly_30d",
            "code": "123456",
            "promo_code": "HALF",
        })

    assert response.status_code == 200
    # Check the amount sent to YooKassa is discounted
    create_call = mock_payment_cls.create.call_args[0][0]
    amount = float(create_call["amount"]["value"])
    assert amount < 199  # 1month is 199 RUB, with 50% should be ~100


# ─────────────────────────────────────────────
#  Payment status polling
# ─────────────────────────────────────────────

@patch("api.db.get_payment_by_id")
def test_status_pending(mock_get, client):
    """Платёж в статусе pending."""
    mock_get.return_value = {"status": "pending", "tg_id": 0}
    response = client.get("/api/web/status/pay-123")
    assert response.status_code == 200
    assert response.json()["status"] == "pending"


@patch("api.db.get_payment_by_id")
def test_status_paid_returns_status_only(mock_get, client):
    """Оплаченный платёж возвращает только статус (без portal_url — IDOR fix)."""
    mock_get.return_value = {"status": "paid", "tg_id": 12345}
    response = client.get("/api/web/status/pay-456")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "paid"
    assert "portal_url" not in data


@patch("api.db.get_payment_by_id", return_value=None)
def test_status_unknown_payment(mock_get, client):
    """Несуществующий payment_id — 404."""
    response = client.get("/api/web/status/nonexistent")
    assert response.status_code == 404


# ─────────────────────────────────────────────
#  Email verification (send-code)
# ─────────────────────────────────────────────

@patch("api.notifications.create_auth_code", return_value="123456")
def test_send_code_ok(mock_code, client):
    """Отправка кода верификации — 200."""
    response = client.post("/api/web/order/send-code", json={
        "email": "user@example.com",
    })
    assert response.status_code == 200
    assert response.json()["ok"] is True


@patch("api.notifications.create_auth_code", return_value=None)
def test_send_code_rate_limited(mock_code, client):
    """Rate limit на отправку кода — 429."""
    response = client.post("/api/web/order/send-code", json={
        "email": "user@example.com",
    })
    assert response.status_code == 429
