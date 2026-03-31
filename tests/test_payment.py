"""Тесты для bot_xui/payment.py — process_payment logic."""
import sys
from unittest.mock import patch, MagicMock, AsyncMock
import pytest

sys.modules.setdefault("yookassa", MagicMock())


def _make_query(user_id=12345, username="testuser"):
    query = MagicMock()
    query.from_user.id = user_id
    query.from_user.username = username
    query.edit_message_text = AsyncMock()
    query.message.reply_text = AsyncMock()
    return query


# ─────────────────────────────────────────────
#  Tariff not found
# ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_process_payment_invalid_tariff():
    from bot_xui.payment import process_payment
    query = _make_query()
    await process_payment(query, "nonexistent", "vless")
    query.edit_message_text.assert_called_once()
    assert "не найден" in query.edit_message_text.call_args[0][0]


# ─────────────────────────────────────────────
#  Discount logic
# ─────────────────────────────────────────────

@pytest.mark.asyncio
@patch("bot_xui.payment.create_payment")
@patch("bot_xui.payment.Payment")
@patch("bot_xui.payment.is_test_mode", return_value=False)
@patch("bot_xui.payment.get_permanent_discount", return_value=0)
async def test_process_payment_no_discount(mock_disc, mock_test, mock_pay_cls, mock_create):
    from bot_xui.payment import process_payment
    mock_payment = MagicMock()
    mock_payment.id = "pay-1"
    mock_payment.confirmation.confirmation_url = "https://pay.test/1"
    mock_pay_cls.create.return_value = mock_payment

    query = _make_query()
    await process_payment(query, "monthly_30d", "vless")

    # Check payment created with full price
    create_call = mock_pay_cls.create.call_args[0][0]
    assert create_call["amount"]["value"] == "199"
    mock_create.assert_called_once()


@pytest.mark.asyncio
@patch("bot_xui.payment.create_payment")
@patch("bot_xui.payment.Payment")
@patch("bot_xui.payment.is_test_mode", return_value=False)
@patch("bot_xui.payment.get_permanent_discount", return_value=50)
async def test_process_payment_permanent_discount(mock_disc, mock_test, mock_pay_cls, mock_create):
    from bot_xui.payment import process_payment
    mock_payment = MagicMock()
    mock_payment.id = "pay-2"
    mock_payment.confirmation.confirmation_url = "https://pay.test/2"
    mock_pay_cls.create.return_value = mock_payment

    query = _make_query()
    await process_payment(query, "monthly_30d", "vless")

    create_call = mock_pay_cls.create.call_args[0][0]
    assert int(create_call["amount"]["value"]) == 100  # 199 * 50% ≈ 100


@pytest.mark.asyncio
@patch("bot_xui.payment.create_payment")
@patch("bot_xui.payment.Payment")
@patch("bot_xui.payment.is_test_mode", return_value=False)
@patch("bot_xui.payment.get_permanent_discount", return_value=20)
async def test_process_payment_promo_beats_permanent(mock_disc, mock_test, mock_pay_cls, mock_create):
    from bot_xui.payment import process_payment
    mock_payment = MagicMock()
    mock_payment.id = "pay-3"
    mock_payment.confirmation.confirmation_url = "https://pay.test/3"
    mock_pay_cls.create.return_value = mock_payment

    promo = {"id": 1, "code": "BIG50", "value": 50, "type": "discount"}
    query = _make_query()
    await process_payment(query, "monthly_30d", "vless", promo=promo)

    # Promo (50%) wins over permanent (20%)
    create_call = mock_pay_cls.create.call_args[0][0]
    assert int(create_call["amount"]["value"]) == 100
    # Promo is now consumed in webhook handler, not at payment creation


@pytest.mark.asyncio
@patch("bot_xui.payment.create_payment")
@patch("bot_xui.payment.Payment")
@patch("bot_xui.payment.is_test_mode", return_value=False)
@patch("bot_xui.payment.get_permanent_discount", return_value=80)
async def test_process_payment_permanent_beats_promo(mock_disc, mock_test, mock_pay_cls, mock_create):
    from bot_xui.payment import process_payment
    mock_payment = MagicMock()
    mock_payment.id = "pay-4"
    mock_payment.confirmation.confirmation_url = "https://pay.test/4"
    mock_pay_cls.create.return_value = mock_payment

    promo = {"id": 2, "code": "SMALL10", "value": 10, "type": "discount"}
    query = _make_query()
    await process_payment(query, "monthly_30d", "vless", promo=promo)

    # Permanent (80%) wins over promo (10%)
    create_call = mock_pay_cls.create.call_args[0][0]
    price = int(create_call["amount"]["value"])
    assert price == 40  # 199 * 20% ≈ 40


# ─────────────────────────────────────────────
#  Renew mode
# ─────────────────────────────────────────────

@pytest.mark.asyncio
@patch("bot_xui.payment.create_payment")
@patch("bot_xui.payment.Payment")
@patch("bot_xui.payment.is_test_mode", return_value=False)
@patch("bot_xui.payment.get_permanent_discount", return_value=0)
async def test_process_payment_renew_mode(mock_disc, mock_test, mock_pay_cls, mock_create):
    from bot_xui.payment import process_payment
    mock_payment = MagicMock()
    mock_payment.id = "pay-5"
    mock_payment.confirmation.confirmation_url = "https://pay.test/5"
    mock_pay_cls.create.return_value = mock_payment

    query = _make_query()
    await process_payment(query, "monthly_30d", "vless", is_renew=True, client_name="cli1", inbound_id=5)

    # Check metadata includes renew info
    meta = mock_pay_cls.create.call_args[0][0]["metadata"]
    assert meta["is_renew"] == "true"
    assert meta["client_name"] == "cli1"
    assert meta["inbound_id"] == "5"
    # Description says "Продление"
    assert "Продление" in mock_pay_cls.create.call_args[0][0]["description"]


# ─────────────────────────────────────────────
#  Error handling
# ─────────────────────────────────────────────

@pytest.mark.asyncio
@patch("bot_xui.payment.Payment")
@patch("bot_xui.payment.is_test_mode", return_value=False)
@patch("bot_xui.payment.get_permanent_discount", return_value=0)
async def test_process_payment_yookassa_error(mock_disc, mock_test, mock_pay_cls):
    from bot_xui.payment import process_payment
    mock_pay_cls.create.side_effect = Exception("YooKassa error")

    query = _make_query()
    await process_payment(query, "monthly_30d", "vless")

    query.message.reply_text.assert_called_once()
    assert "Ошибка" in query.message.reply_text.call_args[0][0]


# ─────────────────────────────────────────────
#  Minimum price (never 0)
# ─────────────────────────────────────────────

@pytest.mark.asyncio
@patch("bot_xui.payment.create_payment")
@patch("bot_xui.payment.Payment")
@patch("bot_xui.payment.is_test_mode", return_value=False)
@patch("bot_xui.payment.get_permanent_discount", return_value=99)
async def test_process_payment_min_price_is_one(mock_disc, mock_test, mock_pay_cls, mock_create):
    from bot_xui.payment import process_payment
    mock_payment = MagicMock()
    mock_payment.id = "pay-min"
    mock_payment.confirmation.confirmation_url = "https://pay.test/min"
    mock_pay_cls.create.return_value = mock_payment

    query = _make_query()
    await process_payment(query, "trial_1d", "vless")  # 10 RUB * 1% = 0.1 → max(1, 0) = 1

    create_call = mock_pay_cls.create.call_args[0][0]
    assert int(create_call["amount"]["value"]) >= 1
