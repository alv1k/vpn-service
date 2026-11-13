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
@patch("bot_xui.autopay.get_autopay_users_for_phase2", return_value=[])
@patch("bot_xui.autopay.get_autopay_users_for_phase1", return_value=[])
async def test_no_users_due(mock_p1, mock_p2, mock_due):
    """No users due — nothing happens."""
    from bot_xui.autopay import process_autopayments
    bot = MagicMock()
    await process_autopayments(bot)
    mock_p1.assert_called_once()
    mock_p2.assert_called_once()
    mock_due.assert_called_once_with(days_before=0)


# ═════════════════════════════════════════════
#  Phase 1: upcoming notification
# ═════════════════════════════════════════════

@pytest.mark.asyncio
@patch("bot_xui.autopay.get_autopay_users_due", return_value=[])
@patch("bot_xui.autopay.get_autopay_users_for_phase2", return_value=[])
@patch("bot_xui.autopay.get_autopay_users_for_phase1")
async def test_phase1_notify_upcoming(mock_p1, mock_p2, mock_due):
    """Phase 1: user notified about upcoming charge tomorrow."""
    from bot_xui.autopay import process_autopayments

    user = _make_user()
    mock_p1.return_value = [user]

    bot = MagicMock()
    bot.send_message = AsyncMock()

    await process_autopayments(bot)

    # User notified about upcoming charge
    bot.send_message.assert_called_once()
    text = bot.send_message.call_args[1]["text"]
    assert "Автопродление завтра" in text
    assert "будет списано" in text


# ═════════════════════════════════════════════
#  Successful auto-charge (Phase 3)
# ═════════════════════════════════════════════

@pytest.mark.asyncio
@patch("bot_xui.autopay.get_user_target_charge_time", return_value=(14, 0))
@patch("bot_xui.autopay.log_autopay")
@patch("bot_xui.autopay.create_payment")
@patch("bot_xui.autopay.Payment")
@patch("bot_xui.autopay.get_autopay_users_due")
@patch("bot_xui.autopay.get_autopay_users_for_phase2", return_value=[])
@patch("bot_xui.autopay.get_autopay_users_for_phase1", return_value=[])
async def test_successful_charge(mock_p1, mock_p2, mock_due, mock_pay_cls, mock_create, mock_log, mock_target_time):
    """Happy path: user charged, payment logged, notification sent and updated."""
    from bot_xui.autopay import process_autopayments

    user = _make_user()
    mock_due.return_value = [user]

    mock_payment = MagicMock()
    mock_payment.id = "pay-auto-1"
    mock_payment.status = "succeeded"
    mock_pay_cls.create.return_value = mock_payment

    msg = MagicMock()
    msg.message_id = 42
    bot = MagicMock()
    bot.send_message = AsyncMock(return_value=msg)
    bot.edit_message_text = AsyncMock()

    await process_autopayments(bot)

    # Initial message sent
    bot.send_message.assert_called_once()
    assert "Списываем средства" in bot.send_message.call_args[1]["text"]

    # Payment created in YooKassa
    call_args = mock_pay_cls.create.call_args[0][0]
    assert call_args["payment_method_id"] == "pm-saved-card"
    assert call_args["capture"] is True

    # Message updated to success
    bot.edit_message_text.assert_called_once()
    assert "Оплата прошла успешно" in bot.edit_message_text.call_args[1]["text"]


# ═════════════════════════════════════════════
#  Discount applied
# ═════════════════════════════════════════════

@pytest.mark.asyncio
@patch("bot_xui.autopay.get_user_target_charge_time", return_value=(14, 0))
@patch("bot_xui.autopay.log_autopay")
@patch("bot_xui.autopay.create_payment")
@patch("bot_xui.autopay.Payment")
@patch("bot_xui.autopay.get_autopay_users_due")
@patch("bot_xui.autopay.get_autopay_users_for_phase2", return_value=[])
@patch("bot_xui.autopay.get_autopay_users_for_phase1", return_value=[])
async def test_discount_applied(mock_p1, mock_p2, mock_due, mock_pay_cls, mock_create, mock_log, mock_target_time):
    """Permanent discount reduces the charge amount."""
    from bot_xui.autopay import process_autopayments
    from bot_xui.tariffs import TARIFFS

    user = _make_user(discount=20)  # 20% off
    mock_due.return_value = [user]

    mock_payment = MagicMock()
    mock_payment.id = "pay-disc"
    mock_pay_cls.create.return_value = mock_payment

    msg = MagicMock()
    msg.message_id = 42
    bot = MagicMock()
    bot.send_message = AsyncMock(return_value=msg)
    bot.edit_message_text = AsyncMock()

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
@patch("bot_xui.autopay.get_autopay_users_due", return_value=[])
@patch("bot_xui.autopay.get_autopay_users_for_phase2", return_value=[])
@patch("bot_xui.autopay.get_autopay_users_for_phase1")
async def test_invalid_tariff_disables_autopay(mock_p1, mock_p2, mock_due, mock_disable):
    """Bad tariff ID disables autopay for that user."""
    from bot_xui.autopay import process_autopayments

    user = _make_user(tariff="nonexistent_tariff")
    mock_p1.return_value = [user]

    bot = MagicMock()
    await process_autopayments(bot)

    mock_disable.assert_called_once_with(1)


# ═════════════════════════════════════════════
#  Payment failure → autopay disabled + notification updated
# ═════════════════════════════════════════════

@pytest.mark.asyncio
@patch("bot_xui.autopay.log_autopay")
@patch("bot_xui.autopay.disable_autopay")
@patch("bot_xui.autopay.Payment")
@patch("bot_xui.autopay.get_autopay_users_due")
@patch("bot_xui.autopay.get_autopay_users_for_phase2", return_value=[])
@patch("bot_xui.autopay.get_autopay_users_for_phase1", return_value=[])
async def test_payment_failure_disables_and_notifies(mock_p1, mock_p2, mock_due, mock_pay_cls,
                                                      mock_disable, mock_log):
    """YooKassa error disables autopay and notifies user via edit_message_text."""
    from bot_xui.autopay import process_autopayments

    user = _make_user()
    mock_due.return_value = [user]
    mock_pay_cls.create.side_effect = Exception("Card declined")

    msg = MagicMock()
    msg.message_id = 42
    bot = MagicMock()
    bot.send_message = AsyncMock(return_value=msg)
    bot.edit_message_text = AsyncMock()

    await process_autopayments(bot)

    # Autopay disabled
    mock_disable.assert_called_once_with(100)

    # Failure logged
    mock_log.assert_called_once()
    assert mock_log.call_args[0][5] == "failed"

    # User notified about failure by editing message
    bot.edit_message_text.assert_called_once()
    assert "не удалась" in bot.edit_message_text.call_args[1]["text"]


# ═════════════════════════════════════════════
#  Web user (tg_id=0) failure → disable by id
# ═════════════════════════════════════════════

@pytest.mark.asyncio
@patch("bot_xui.autopay.log_autopay")
@patch("bot_xui.autopay.disable_autopay_by_id")
@patch("bot_xui.autopay.Payment")
@patch("bot_xui.autopay.get_autopay_users_due")
@patch("bot_xui.autopay.get_autopay_users_for_phase2", return_value=[])
@patch("bot_xui.autopay.get_autopay_users_for_phase1", return_value=[])
async def test_web_user_failure_disables_by_id(mock_p1, mock_p2, mock_due, mock_pay_cls,
                                                mock_disable_id, mock_log):
    """Web user (no tg_id) failure uses disable_autopay_by_id."""
    from bot_xui.autopay import process_autopayments

    user = _make_user(tg_id=0)
    mock_due.return_value = [user]
    mock_pay_cls.create.side_effect = Exception("fail")

    bot = MagicMock()
    await process_autopayments(bot)

    mock_disable_id.assert_called_once_with(1)


# ═════════════════════════════════════════════
#  Manual Opt-Out Preservation
# ═════════════════════════════════════════════

def test_save_user_payment_method_first_time():
    """When user has never disabled autopay, save_user_payment_method enables autopay."""
    from api.db import save_user_payment_method

    with patch("api.db.execute_query") as mock_eq:
        # User has no autopay_disabled_at
        mock_eq.side_effect = [
            {"autopay_disabled_at": None, "autopay_enabled": 0},  # SELECT
            1  # UPDATE
        ]
        res = save_user_payment_method(12345, "pm-123", "monthly_30d", "vless")
        assert res["autopay_enabled"] is True
        assert res["disabled_at"] is None
        # Verify UPDATE sets autopay_enabled = 1
        update_call = mock_eq.call_args_list[1]
        assert "autopay_enabled = 1" in update_call[0][0]


def test_save_user_payment_method_preserves_manual_opt_out():
    """When user previously disabled autopay, save_user_payment_method keeps autopay_enabled = 0."""
    from api.db import save_user_payment_method
    from datetime import datetime

    disabled_dt = datetime(2026, 9, 1, 12, 0, 0)
    with patch("api.db.execute_query") as mock_eq:
        # User has autopay_disabled_at set
        mock_eq.side_effect = [
            {"autopay_disabled_at": disabled_dt, "autopay_enabled": 0},  # SELECT
            1  # UPDATE
        ]
        res = save_user_payment_method(12345, "pm-123", "monthly_30d", "vless")
        assert res["autopay_enabled"] is False
        assert res["disabled_at"] == disabled_dt
        # Verify UPDATE does NOT set autopay_enabled = 1
        update_call = mock_eq.call_args_list[1]
        assert "autopay_enabled = 1" not in update_call[0][0]


def test_enable_autopay_clears_disabled_at():
    """Explicitly enabling autopay via /autopay clears autopay_disabled_at."""
    from api.db import enable_autopay

    with patch("api.db.execute_query") as mock_eq:
        enable_autopay(12345)
        mock_eq.assert_called_once()
        sql = mock_eq.call_args[0][0]
        assert "autopay_disabled_at = NULL" in sql
        assert "autopay_enabled = 1" in sql


def test_disable_autopay_sets_disabled_at():
    """Disabling autopay via /autopay records timestamp in autopay_disabled_at."""
    from api.db import disable_autopay

    with patch("api.db.execute_query") as mock_eq:
        disable_autopay(12345)
        mock_eq.assert_called_once()
        sql = mock_eq.call_args[0][0]
        assert "autopay_disabled_at = NOW()" in sql
        assert "autopay_enabled = 0" in sql


# ═════════════════════════════════════════════
#  Last-Minute Warning (0-30 min) Tests
# ═════════════════════════════════════════════

@pytest.mark.asyncio
async def test_last_minute_warning_flow():
    from bot_xui import autopay
    from datetime import datetime, timedelta

    bot = MagicMock()
    bot.send_message = AsyncMock()

    # 1. Refresh cache
    test_user = {
        "id": 999,
        "tg_id": 999000,
        "email": "test@test.com",
        "subscription_until": datetime.utcnow() + timedelta(minutes=15),
    }

    with patch("api.db.get_manual_users_expiring_within_24h", return_value=[test_user]):
        await autopay.refresh_today_expiring_cache()
        assert len(autopay._TODAY_EXPIRING_MANUAL_USERS) == 1

    # 2. Trigger check
    with patch("bot_xui.autopay.execute_query", return_value=None), \
         patch("bot_xui.autopay.log_message_sent") as mock_log, \
         patch("bot_xui.views._build_tariff_text_and_keyboard", return_value=("text", MagicMock())):
        await autopay.check_last_minute_warnings(bot)

        bot.send_message.assert_called_once()
        sent_text = bot.send_message.call_args[1]["text"]
        assert "VPN отключится через" in sent_text
        assert "15 минут" in sent_text
        mock_log.assert_called_once()


