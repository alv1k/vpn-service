"""Tests for api/webhook.py — process_refund and deactivate_xui_client."""
import sys
from unittest.mock import patch, MagicMock

import pytest

sys.modules.setdefault("yookassa", MagicMock())


# ═════════════════════════════════════════════
#  deactivate_xui_client
# ═════════════════════════════════════════════

class TestDeactivateXuiClient:

    @patch("api.webhook.XUIClient")
    def test_success(self, mock_xui_cls):
        """Deactivates client via XUI panel."""
        from api.webhook import deactivate_xui_client

        xui = MagicMock()
        xui.get_client_by_email.return_value = {
            "inbound_id": 1, "client": {"email": "tiin_100"}
        }
        xui.deactivate_client.return_value = True
        mock_xui_cls.return_value = xui

        assert deactivate_xui_client("tiin_100") is True
        xui.deactivate_client.assert_called_once()

    @patch("api.webhook.XUIClient")
    def test_client_not_found(self, mock_xui_cls):
        """Returns False if client not found in XUI."""
        from api.webhook import deactivate_xui_client

        xui = MagicMock()
        xui.get_client_by_email.return_value = None
        mock_xui_cls.return_value = xui

        assert deactivate_xui_client("nonexistent") is False

    @patch("api.webhook.XUIClient")
    def test_xui_exception(self, mock_xui_cls):
        """Returns False on XUI error (doesn't crash)."""
        from api.webhook import deactivate_xui_client

        mock_xui_cls.side_effect = ConnectionError("panel down")

        assert deactivate_xui_client("tiin_100") is False


# ═════════════════════════════════════════════
#  process_refund
# ═════════════════════════════════════════════

class TestProcessRefund:

    @pytest.mark.asyncio
    @patch("api.webhook.deactivate_key_by_payment")
    @patch("api.webhook.deactivate_xui_client", return_value=True)
    @patch("api.webhook.get_user_email", return_value="tiin_100")
    @patch("api.webhook.get_payment_by_id")
    async def test_success(self, mock_get_pay, mock_get_email,
                           mock_deactivate, mock_deactivate_key):
        """Happy path: payment found, XUI deactivated, key deactivated."""
        from api.webhook import process_refund

        mock_get_pay.return_value = {"tg_id": 100, "tariff": "monthly_30d"}

        result = await process_refund("pay-123")

        assert result is True
        mock_deactivate.assert_called_once_with("tiin_100")
        mock_deactivate_key.assert_called_once_with("pay-123")

    @pytest.mark.asyncio
    @patch("api.webhook.get_payment_by_id", return_value=None)
    async def test_payment_not_found(self, mock_get):
        """Returns False if payment not in DB."""
        from api.webhook import process_refund

        result = await process_refund("missing-pay")
        assert result is False

    @pytest.mark.asyncio
    @patch("api.webhook.get_user_email", return_value=None)
    @patch("api.webhook.get_payment_by_id")
    async def test_no_client_name(self, mock_get_pay, mock_get_email):
        """Returns False if no client_name found for payment."""
        from api.webhook import process_refund

        mock_get_pay.return_value = {"tg_id": 100}

        result = await process_refund("pay-456")
        assert result is False

    @pytest.mark.asyncio
    @patch("api.webhook.deactivate_xui_client", return_value=False)
    @patch("api.webhook.get_user_email", return_value="tiin_100")
    @patch("api.webhook.get_payment_by_id")
    async def test_xui_deactivation_fails(self, mock_get_pay, mock_get_email,
                                          mock_deactivate):
        """Returns False if XUI deactivation fails."""
        from api.webhook import process_refund

        mock_get_pay.return_value = {"tg_id": 100}

        result = await process_refund("pay-789")
        assert result is False
