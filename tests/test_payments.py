"""Тесты для платежей — DB операции + process_payment логика."""
import sys
import asyncio
from datetime import datetime
from unittest.mock import patch, MagicMock, AsyncMock

# Mock yookassa before importing payment module
sys.modules.setdefault("yookassa", MagicMock())


def _make_mock_pool():
    mock_conn = MagicMock()
    mock_cursor = MagicMock(dictionary=True)
    mock_conn.cursor.return_value = mock_cursor
    mock_pool = MagicMock()
    mock_pool.get_connection.return_value = mock_conn
    return mock_pool, mock_conn, mock_cursor


SAMPLE_PAYMENT = {
    "id": 1,
    "payment_id": "pay-abc-123",
    "tg_id": 123456,
    "tariff": "monthly_30d",
    "amount": 199,
    "status": "pending",
    "vpn_issued": 0,
    "created_at": datetime(2026, 3, 1, 10, 0),
}


# ─────────────────────────────────────────────
#  create_payment
# ─────────────────────────────────────────────

@patch("api.db._get_pool")
def test_create_payment(mock_get_pool):
    mock_pool, mock_conn, mock_cursor = _make_mock_pool()
    mock_get_pool.return_value = mock_pool

    from api.db import create_payment
    create_payment("pay-abc-123", 123456, "monthly_30d", 199, "pending")

    sql = mock_cursor.execute.call_args[0][0]
    params = mock_cursor.execute.call_args[0][1]
    assert "INSERT INTO payments" in sql
    assert params == ("pay-abc-123", 123456, "monthly_30d", 199, "pending", 0)
    mock_conn.commit.assert_called_once()


# ─────────────────────────────────────────────
#  get_payment_by_id
# ─────────────────────────────────────────────

@patch("api.db._get_pool")
def test_get_payment_by_id_found(mock_get_pool):
    mock_pool, mock_conn, mock_cursor = _make_mock_pool()
    mock_get_pool.return_value = mock_pool
    mock_cursor.fetchone.return_value = SAMPLE_PAYMENT

    from api.db import get_payment_by_id
    result = get_payment_by_id("pay-abc-123")

    assert result["payment_id"] == "pay-abc-123"
    assert result["amount"] == 199


@patch("api.db._get_pool")
def test_get_payment_by_id_not_found(mock_get_pool):
    mock_pool, mock_conn, mock_cursor = _make_mock_pool()
    mock_get_pool.return_value = mock_pool
    mock_cursor.fetchone.return_value = None

    from api.db import get_payment_by_id
    assert get_payment_by_id("nonexistent") is None


# ─────────────────────────────────────────────
#  get_payment_status
# ─────────────────────────────────────────────

@patch("api.db._get_pool")
def test_get_payment_status_pending(mock_get_pool):
    mock_pool, mock_conn, mock_cursor = _make_mock_pool()
    mock_get_pool.return_value = mock_pool
    mock_cursor.fetchone.return_value = {"status": "pending"}

    from api.db import get_payment_status
    assert get_payment_status("pay-abc-123") == "pending"


@patch("api.db._get_pool")
def test_get_payment_status_not_found(mock_get_pool):
    mock_pool, mock_conn, mock_cursor = _make_mock_pool()
    mock_get_pool.return_value = mock_pool
    mock_cursor.fetchone.return_value = None

    from api.db import get_payment_status
    assert get_payment_status("nonexistent") is None


# ─────────────────────────────────────────────
#  update_payment_status
# ─────────────────────────────────────────────

@patch("api.db._get_pool")
def test_update_payment_status(mock_get_pool):
    mock_pool, mock_conn, mock_cursor = _make_mock_pool()
    mock_get_pool.return_value = mock_pool

    from api.db import update_payment_status
    update_payment_status("pay-abc-123", "succeeded")

    sql = mock_cursor.execute.call_args[0][0]
    params = mock_cursor.execute.call_args[0][1]
    assert "UPDATE payments SET status" in sql
    assert params == ("succeeded", "pay-abc-123")
    mock_conn.commit.assert_called_once()


# ─────────────────────────────────────────────
#  is_payment_processed
# ─────────────────────────────────────────────

@patch("api.db._get_pool")
def test_is_payment_processed_true(mock_get_pool):
    mock_pool, mock_conn, mock_cursor = _make_mock_pool()
    mock_get_pool.return_value = mock_pool
    mock_cursor.fetchone.return_value = {"status": "paid"}

    from api.db import is_payment_processed
    assert is_payment_processed("pay-abc-123") is True


@patch("api.db._get_pool")
def test_is_payment_processed_false_pending(mock_get_pool):
    mock_pool, mock_conn, mock_cursor = _make_mock_pool()
    mock_get_pool.return_value = mock_pool
    mock_cursor.fetchone.return_value = {"status": "pending"}

    from api.db import is_payment_processed
    assert is_payment_processed("pay-abc-123") is False


@patch("api.db._get_pool")
def test_is_payment_processed_not_found(mock_get_pool):
    mock_pool, mock_conn, mock_cursor = _make_mock_pool()
    mock_get_pool.return_value = mock_pool
    mock_cursor.fetchone.return_value = None

    from api.db import is_payment_processed
    assert is_payment_processed("nonexistent") is False


# ─────────────────────────────────────────────
#  get_last_paid_payment
# ─────────────────────────────────────────────

@patch("api.db._get_pool")
def test_get_last_paid_payment_found(mock_get_pool):
    mock_pool, mock_conn, mock_cursor = _make_mock_pool()
    mock_get_pool.return_value = mock_pool
    paid = {**SAMPLE_PAYMENT, "status": "paid"}
    mock_cursor.fetchone.return_value = paid

    from api.db import get_last_paid_payment
    result = get_last_paid_payment(123456)

    assert result["status"] == "paid"
    sql = mock_cursor.execute.call_args[0][0]
    assert "ORDER BY created_at DESC" in sql


@patch("api.db._get_pool")
def test_get_last_paid_payment_none(mock_get_pool):
    mock_pool, mock_conn, mock_cursor = _make_mock_pool()
    mock_get_pool.return_value = mock_pool
    mock_cursor.fetchone.return_value = None

    from api.db import get_last_paid_payment
    assert get_last_paid_payment(999) is None


# ─────────────────────────────────────────────
#  mark_vpn_issued
# ─────────────────────────────────────────────

@patch("api.db._get_pool")
def test_mark_vpn_issued(mock_get_pool):
    mock_pool, mock_conn, mock_cursor = _make_mock_pool()
    mock_get_pool.return_value = mock_pool

    from api.db import mark_vpn_issued
    mark_vpn_issued("pay-abc-123")

    sql = mock_cursor.execute.call_args[0][0]
    assert "vpn_issued = 1" in sql
    mock_conn.commit.assert_called_once()


# ─────────────────────────────────────────────
#  process_payment (payment.py) — discount logic
# ─────────────────────────────────────────────

def _make_mock_query(user_id=123456, username="testuser"):
    query = AsyncMock()
    query.from_user.id = user_id
    query.from_user.username = username
    query.edit_message_text = AsyncMock()
    query.message.reply_text = AsyncMock()
    return query


@patch("bot_xui.payment.create_payment")
@patch("bot_xui.payment.Payment")
@patch("bot_xui.payment.is_test_mode", return_value=False)
def test_process_payment_with_promo_discount(mock_test_mode, mock_payment_cls, mock_create):
    """Promo discount should reduce price in Payment.create and mark promo used."""
    from bot_xui.payment import process_payment

    mock_payment_obj = MagicMock()
    mock_payment_obj.id = "pay-xyz"
    mock_payment_obj.confirmation.confirmation_url = "https://yookassa.ru/pay"
    mock_payment_cls.create.return_value = mock_payment_obj

    query = _make_mock_query()
    promo = {"id": 1, "code": "SALE50", "value": 50}

    asyncio.get_event_loop().run_until_complete(
        process_payment(query, "monthly_30d", "vless", promo=promo)
    )

    # Payment.create called with discounted price (199 * 50% = 100)
    create_args = mock_payment_cls.create.call_args[0][0]
    assert create_args["amount"]["value"] == "100"

    # Promo is now consumed in webhook handler, not at payment creation time

    # DB payment created with discounted amount
    mock_create.assert_called_once()
    assert mock_create.call_args[1]["amount"] == 100

    # Message shows strikethrough price
    text = query.edit_message_text.call_args[0][0]
    assert "<s>199 ₽</s>" in text
    assert "100 ₽" in text
    assert "SALE50" in text


@patch("bot_xui.payment.create_payment")
@patch("bot_xui.payment.Payment")
@patch("bot_xui.payment.is_test_mode", return_value=False)
def test_process_payment_without_promo(mock_test_mode, mock_payment_cls, mock_create):
    """Without promo, full price is charged."""
    from bot_xui.payment import process_payment

    mock_payment_obj = MagicMock()
    mock_payment_obj.id = "pay-xyz"
    mock_payment_obj.confirmation.confirmation_url = "https://yookassa.ru/pay"
    mock_payment_cls.create.return_value = mock_payment_obj

    query = _make_mock_query()

    asyncio.get_event_loop().run_until_complete(
        process_payment(query, "monthly_30d", "vless")
    )

    create_args = mock_payment_cls.create.call_args[0][0]
    assert create_args["amount"]["value"] == "199"
    assert mock_create.call_args[1]["amount"] == 199

    text = query.edit_message_text.call_args[0][0]
    assert "<s>" not in text


@patch("bot_xui.payment.is_test_mode", return_value=False)
def test_process_payment_invalid_tariff(mock_test_mode):
    """Invalid tariff → error message."""
    from bot_xui.payment import process_payment

    query = _make_mock_query()

    asyncio.get_event_loop().run_until_complete(
        process_payment(query, "nonexistent_tariff", "vless")
    )

    query.edit_message_text.assert_called_once_with("❌ Тариф не найден")
