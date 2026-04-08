"""Tests for api/web_api.py — create_order endpoint."""
import sys
from unittest.mock import patch, MagicMock, AsyncMock

import pytest

sys.modules.setdefault("yookassa", MagicMock())


@pytest.fixture
def client():
    from api.webhook import app
    from fastapi.testclient import TestClient
    return TestClient(app)


FAKE_USER = {"id": 10, "web_token": "tok-abc", "tg_id": None}


# ═════════════════════════════════════════════
#  Validation
# ═════════════════════════════════════════════

class TestCreateOrderValidation:

    @patch("api.notifications.verify_code", return_value=False)
    def test_invalid_code_rejected(self, mock_verify, client):
        """Wrong verification code returns 400."""
        resp = client.post("/api/web/order", json={
            "email": "a@b.com", "code": "111111", "tariff_id": "monthly_30d",
        })
        assert resp.status_code == 400
        assert "код" in resp.json()["detail"].lower()

    @patch("api.notifications.verify_code", return_value=True)
    def test_invalid_tariff_rejected(self, mock_verify, client):
        """Unknown tariff returns 400."""
        resp = client.post("/api/web/order", json={
            "email": "a@b.com", "code": "123456", "tariff_id": "nonexistent",
        })
        assert resp.status_code == 400
        assert "Тариф" in resp.json()["detail"]

    @patch("api.notifications.verify_code", return_value=True)
    def test_test_tariff_rejected(self, mock_verify, client):
        """Test tariffs cannot be ordered."""
        resp = client.post("/api/web/order", json={
            "email": "a@b.com", "code": "123456", "tariff_id": "test_24h",
        })
        assert resp.status_code == 400


# ═════════════════════════════════════════════
#  Production payment flow
# ═════════════════════════════════════════════

class TestCreateOrderProduction:

    @patch("api.db.create_payment")
    @patch("api.web_api.Payment")
    @patch("api.web_api._get_or_create_web_user")
    @patch("api.notifications.verify_code", return_value=True)
    def test_creates_yookassa_payment(self, mock_verify, mock_get_user,
                                      mock_pay_cls, mock_create_pay, client):
        """Valid order creates YooKassa payment and returns URL."""
        mock_get_user.return_value = FAKE_USER.copy()

        mock_payment = MagicMock()
        mock_payment.id = "pay-prod-1"
        mock_payment.confirmation.confirmation_url = "https://yookassa.ru/pay"
        mock_pay_cls.create.return_value = mock_payment

        resp = client.post("/api/web/order", json={
            "email": "user@test.com", "code": "123456", "tariff_id": "monthly_30d",
        })

        assert resp.status_code == 200
        data = resp.json()
        assert data["payment_url"] == "https://yookassa.ru/pay"
        assert data["payment_id"] == "pay-prod-1"
        assert data["web_token"] == "tok-abc"

        mock_create_pay.assert_called_once()

    @patch("api.db.create_payment")
    @patch("api.web_api.Payment")
    @patch("api.db.get_promocode")
    @patch("api.web_api._get_or_create_web_user")
    @patch("api.notifications.verify_code", return_value=True)
    def test_promo_discount_applied(self, mock_verify, mock_get_user,
                                    mock_get_promo, mock_pay_cls,
                                    mock_create_pay, client):
        """Active promo code reduces payment amount."""
        from bot_xui.tariffs import TARIFFS

        mock_get_user.return_value = FAKE_USER.copy()
        mock_get_promo.return_value = {
            "id": 1, "is_active": True, "type": "discount",
            "value": 30, "expires_at": None, "max_uses": None, "used_count": 0,
        }

        mock_payment = MagicMock()
        mock_payment.id = "pay-promo"
        mock_payment.confirmation.confirmation_url = "https://yookassa.ru/pay"
        mock_pay_cls.create.return_value = mock_payment

        resp = client.post("/api/web/order", json={
            "email": "user@test.com", "code": "123456",
            "tariff_id": "monthly_30d", "promo_code": "SALE30",
        })

        assert resp.status_code == 200

        # Check discounted price was sent to YooKassa
        full_price = TARIFFS["monthly_30d"]["price"]
        expected = max(1, round(full_price * 70 / 100))
        charged = mock_pay_cls.create.call_args[0][0]["amount"]["value"]
        assert int(charged) == expected

    @patch("api.db.create_payment")
    @patch("api.web_api.Payment")
    @patch("api.web_api._get_or_create_web_user")
    @patch("api.notifications.verify_code", return_value=True)
    def test_metadata_includes_email_and_tariff(self, mock_verify, mock_get_user,
                                                 mock_pay_cls, mock_create_pay, client):
        """Payment metadata contains email, tariff, and web_token."""
        mock_get_user.return_value = FAKE_USER.copy()

        mock_payment = MagicMock()
        mock_payment.id = "pay-meta"
        mock_payment.confirmation.confirmation_url = "https://pay"
        mock_pay_cls.create.return_value = mock_payment

        client.post("/api/web/order", json={
            "email": "u@b.com", "code": "123456", "tariff_id": "monthly_30d",
        })

        metadata = mock_pay_cls.create.call_args[0][0]["metadata"]
        assert metadata["email"] == "u@b.com"
        assert metadata["tariff"] == "monthly_30d"
        assert metadata["web_token"] == "tok-abc"
        assert metadata["source"] == "web"


# ═════════════════════════════════════════════
#  Test payment flow (code 000000)
# ═════════════════════════════════════════════

class TestCreateOrderTestMode:

    @patch("api.webhook.process_successful_payment", new_callable=AsyncMock)
    @patch("api.db.get_payment_by_id")
    @patch("api.db.claim_payment_for_processing")
    @patch("api.db.create_payment")
    @patch("api.web_api._get_or_create_web_user")
    def test_test_mode_auto_confirms(self, mock_get_user, mock_create_pay,
                                     mock_claim, mock_get_pay,
                                     mock_process, client):
        """Code 000000 creates and auto-confirms test payment."""
        mock_get_user.return_value = FAKE_USER.copy()
        mock_get_pay.return_value = {
            "id": 1, "payment_id": "test_abc", "tariff": "monthly_30d",
            "tg_id": 0, "amount": 199, "status": "pending",
        }

        resp = client.post("/api/web/order", json={
            "email": "admin@test.com", "code": "000000", "tariff_id": "monthly_30d",
        })

        assert resp.status_code == 200
        data = resp.json()
        assert "test_" in data["payment_id"]
        assert data["web_token"] == "tok-abc"
        # Portal URL returned instead of payment URL
        assert "/my/tok-abc" in data["payment_url"]
        mock_process.assert_called_once()
