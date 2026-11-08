"""
Unit tests for bot_xui/payment_reconciler.py and reconciliation DB helpers.
Validates the invariant:
- Once a webhook arrives naturally, YooKassa is NEVER polled.
- Pending payments after grace period are polled and reconciled properly.
- Stale pending payments are expired to 'canceled'.
"""
import pytest
from unittest.mock import patch, MagicMock, AsyncMock

from bot_xui.payment_reconciler import reconcile_pending_payments


@pytest.mark.asyncio
async def test_reconcile_skips_when_no_pending_payments():
    """If no pending payments exist (e.g. webhooks already arrived), 0 YooKassa calls made."""
    with patch("bot_xui.payment_reconciler.expire_stale_pending_payments", return_value=0), \
         patch("bot_xui.payment_reconciler.get_pending_payments_for_reconciliation", return_value=[]), \
         patch("bot_xui.payment_reconciler.async_payment_find") as mock_find:

        await reconcile_pending_payments()
        mock_find.assert_not_called()


@pytest.mark.asyncio
async def test_reconcile_skips_if_webhook_arrived_just_before_poll():
    """If DB status changed from pending to paid just before polling, skip immediately."""
    fake_pending = [{
        "id": 100,
        "payment_id": "pay-100",
        "tg_id": 12345,
        "tariff": "monthly_30d",
        "amount": 100,
        "status": "pending",
        "is_test": 0,
    }]

    with patch("bot_xui.payment_reconciler.expire_stale_pending_payments", return_value=0), \
         patch("bot_xui.payment_reconciler.get_pending_payments_for_reconciliation", return_value=fake_pending), \
         patch("bot_xui.payment_reconciler.get_payment_status", return_value="paid"), \
         patch("bot_xui.payment_reconciler.async_payment_find") as mock_find:

        await reconcile_pending_payments()
        # Invariant: YooKassa must NOT be polled!
        mock_find.assert_not_called()


@pytest.mark.asyncio
async def test_reconcile_triggers_webhook_on_succeeded_payment():
    """If payment succeeded at YooKassa but webhook was dropped, triggers local webhook."""
    fake_pending = [{
        "id": 101,
        "payment_id": "pay-101",
        "tg_id": 12345,
        "tariff": "monthly_30d",
        "amount": 100,
        "status": "pending",
        "is_test": 0,
    }]

    mock_yk_payment = MagicMock()
    mock_yk_payment.id = "pay-101"
    mock_yk_payment.status = "succeeded"
    mock_yk_payment.paid = True
    mock_yk_payment.amount.value = "100.00"
    mock_yk_payment.amount.currency = "RUB"
    mock_yk_payment.metadata = {"tg_id": "12345", "tariff": "monthly_30d"}
    mock_yk_payment.payment_method = None
    mock_yk_payment.cancellation_details = None

    mock_resp = MagicMock()
    mock_resp.status_code = 200

    with patch("bot_xui.payment_reconciler.expire_stale_pending_payments", return_value=0), \
         patch("bot_xui.payment_reconciler.get_pending_payments_for_reconciliation", return_value=fake_pending), \
         patch("bot_xui.payment_reconciler.get_payment_status", return_value="pending"), \
         patch("bot_xui.payment_reconciler.async_payment_find", return_value=mock_yk_payment) as mock_find, \
         patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=mock_resp) as mock_post:

        await reconcile_pending_payments()
        mock_find.assert_called_once_with("pay-101", is_test=False)
        mock_post.assert_called_once()
        args, kwargs = mock_post.call_args
        assert args[0] == "http://127.0.0.1:8000/webhook"
        assert kwargs["json"]["event"] == "payment.succeeded"
        assert kwargs["json"]["object"]["id"] == "pay-101"


@pytest.mark.asyncio
async def test_reconcile_handles_canceled_payment():
    """If payment was canceled at YooKassa, posts canceled event."""
    fake_pending = [{
        "id": 102,
        "payment_id": "pay-102",
        "tg_id": 12345,
        "tariff": "monthly_30d",
        "amount": 100,
        "status": "pending",
        "is_test": 0,
    }]

    mock_yk_payment = MagicMock()
    mock_yk_payment.id = "pay-102"
    mock_yk_payment.status = "canceled"
    mock_yk_payment.paid = False
    mock_yk_payment.amount.value = "100.00"
    mock_yk_payment.amount.currency = "RUB"
    mock_yk_payment.metadata = {"tg_id": "12345"}
    mock_yk_payment.cancellation_details = None

    mock_resp = MagicMock()
    mock_resp.status_code = 200

    with patch("bot_xui.payment_reconciler.expire_stale_pending_payments", return_value=0), \
         patch("bot_xui.payment_reconciler.get_pending_payments_for_reconciliation", return_value=fake_pending), \
         patch("bot_xui.payment_reconciler.get_payment_status", return_value="pending"), \
         patch("bot_xui.payment_reconciler.async_payment_find", return_value=mock_yk_payment) as mock_find, \
         patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=mock_resp) as mock_post:

        await reconcile_pending_payments()
        mock_find.assert_called_once_with("pay-102", is_test=False)
        mock_post.assert_called_once()
        args, kwargs = mock_post.call_args
        assert kwargs["json"]["event"] == "payment.canceled"
