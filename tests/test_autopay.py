"""Tests for bot_xui/autopay.py — automatic payment renewal."""
import sys
from unittest.mock import patch, MagicMock, AsyncMock

import pytest

sys.modules.setdefault("yookassa", MagicMock())


def _make_user(user_id=1, tg_id=100, pm_id="pm-saved-card",
               tariff="monthly_30d", vpn_type="vless", discount=0):
    return {
        "id": user_id, "tg_id": tg_id,
        "payment_method_id": pm_id,
        "autopay_tariff": tariff,
        "autopay_vpn_type": vpn_type,
        "permanent_discount": discount,
    }


# ═════════════════════════════════════════════
#  No users due
# ═════════════════════════════════════════════

@pytest.mark.asyncio
@patch("bot_xui.autopay.get_autopay_users_due", return_value=[])
async def test_no_users_due(mock_due):
    """No users due — nothing happens."""
    from bot_xui.autopay import process_autopayments
    bot = MagicMock()
    await process_autopayments(bot)
    mock_due.assert_called_once_with(days_before=1)


# ═════════════════════════════════════════════
#  Successful auto-charge
# ═════════════════════════════════════════════

@pytest.mark.asyncio
@patch("bot_xui.autopay.log_autopay")
@patch("bot_xui.autopay.create_payment")
@patch("bot_xui.autopay.Payment")
@patch("bot_xui.autopay.get_autopay_users_due")
async def test_successful_charge(mock_due, mock_pay_cls, mock_create, mock_log):
    """Happy path: user charged, payment logged, notification sent."""
    from bot_xui.autopay import process_autopayments

    user = _make_user()
    mock_due.return_value = [user]

    mock_payment = MagicMock()
    mock_payment.id = "pay-auto-1"
    mock_pay_cls.create.return_value = mock_payment

    bot = MagicMock()
    bot.send_message = AsyncMock()

    await process_autopayments(bot)

    # Payment created in YooKassa
    call_args = mock_pay_cls.create.call_args[0][0]
    assert call_args["payment_method_id"] == "pm-saved-card"
    assert call_args["capture"] is True

    # Payment stored in DB
    mock_create.assert_called_once()
    assert mock_create.call_args[1]["payment_id"] == "pay-auto-1"
    assert mock_create.call_args[1]["status"] == "pending"

    # Log entry created
    mock_log.assert_called_once()
    assert mock_log.call_args[0][4] == "pay-auto-1"

    # Telegram notification sent
    bot.send_message.assert_called_once()
    assert "Автопродление" in bot.send_message.call_args[1]["text"]


# ═════════════════════════════════════════════
#  Discount applied
# ═════════════════════════════════════════════

@pytest.mark.asyncio
@patch("bot_xui.autopay.log_autopay")
@patch("bot_xui.autopay.create_payment")
@patch("bot_xui.autopay.Payment")
@patch("bot_xui.autopay.get_autopay_users_due")
async def test_discount_applied(mock_due, mock_pay_cls, mock_create, mock_log):
    """Permanent discount reduces the charge amount."""
    from bot_xui.autopay import process_autopayments
    from bot_xui.tariffs import TARIFFS

    user = _make_user(discount=20)  # 20% off
    mock_due.return_value = [user]

    mock_payment = MagicMock()
    mock_payment.id = "pay-disc"
    mock_pay_cls.create.return_value = mock_payment

    bot = MagicMock()
    bot.send_message = AsyncMock()

    await process_autopayments(bot)

    full_price = TARIFFS["monthly_30d"]["price"]
    expected = max(1, round(full_price * 80 / 100))
    charged = int(mock_pay_cls.create.call_args[0][0]["amount"]["value"])
    assert charged == expected


# ═════════════════════════════════════════════
#  Invalid tariff → autopay disabled
# ═════════════════════════════════════════════

@pytest.mark.asyncio
@patch("bot_xui.autopay.disable_autopay_by_id")
@patch("bot_xui.autopay.get_autopay_users_due")
async def test_invalid_tariff_disables_autopay(mock_due, mock_disable):
    """Bad tariff ID disables autopay for that user."""
    from bot_xui.autopay import process_autopayments

    user = _make_user(tariff="nonexistent_tariff")
    mock_due.return_value = [user]

    bot = MagicMock()
    await process_autopayments(bot)

    mock_disable.assert_called_once_with(1)


# ═════════════════════════════════════════════
#  Payment failure → autopay disabled + notification
# ═════════════════════════════════════════════

@pytest.mark.asyncio
@patch("bot_xui.autopay.log_autopay")
@patch("bot_xui.autopay.disable_autopay")
@patch("bot_xui.autopay.Payment")
@patch("bot_xui.autopay.get_autopay_users_due")
async def test_payment_failure_disables_and_notifies(mock_due, mock_pay_cls,
                                                      mock_disable, mock_log):
    """YooKassa error disables autopay and notifies user."""
    from bot_xui.autopay import process_autopayments

    user = _make_user()
    mock_due.return_value = [user]
    mock_pay_cls.create.side_effect = Exception("Card declined")

    bot = MagicMock()
    bot.send_message = AsyncMock()

    await process_autopayments(bot)

    # Autopay disabled
    mock_disable.assert_called_once_with(100)

    # Failure logged
    mock_log.assert_called_once()
    assert mock_log.call_args[0][5] == "failed"

    # User notified about failure
    bot.send_message.assert_called_once()
    assert "не удалось" in bot.send_message.call_args[1]["text"]


# ═════════════════════════════════════════════
#  Web user (tg_id=0) failure → disable by id
# ═════════════════════════════════════════════

@pytest.mark.asyncio
@patch("bot_xui.autopay.log_autopay")
@patch("bot_xui.autopay.disable_autopay_by_id")
@patch("bot_xui.autopay.Payment")
@patch("bot_xui.autopay.get_autopay_users_due")
async def test_web_user_failure_disables_by_id(mock_due, mock_pay_cls,
                                                mock_disable_id, mock_log):
    """Web user (no tg_id) failure uses disable_autopay_by_id."""
    from bot_xui.autopay import process_autopayments

    user = _make_user(tg_id=0)
    mock_due.return_value = [user]
    mock_pay_cls.create.side_effect = Exception("fail")

    bot = MagicMock()
    await process_autopayments(bot)

    mock_disable_id.assert_called_once_with(1)
