"""Тесты для api/subscriptions.py — логика активации подписки."""
from unittest.mock import patch
from datetime import datetime, timedelta, timezone

TZ_TOKYO = timezone(timedelta(hours=9))


def _expected_eod_tokyo(dt):
    """Округление до 23:59:59 Tokyo → naive UTC (как в продакшене)."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    dt_tokyo = dt.astimezone(TZ_TOKYO)
    eod = dt_tokyo.replace(hour=23, minute=59, second=59, microsecond=0)
    return eod.astimezone(timezone.utc).replace(tzinfo=None)


@patch("api.subscriptions.upsert_user_subscription")
@patch("api.subscriptions.get_user_by_tg_id")
@patch("api.subscriptions.get_payment_by_id")
def test_activate_new_user(mock_get_payment, mock_get_user, mock_upsert):
    """Новый пользователь — подписка от момента оплаты."""
    mock_get_payment.return_value = {
        "tg_id": 123, "tariff": "monthly_30d",
        "created_at": datetime(2026, 3, 1, 10, 0),
    }
    mock_get_user.return_value = None

    from api.subscriptions import activate_subscription
    result = activate_subscription("pay-123")

    expected = _expected_eod_tokyo(datetime(2026, 3, 1, 10, 0) + timedelta(days=30))
    mock_upsert.assert_called_once_with(tg_id=123, subscription_until=expected)
    assert result == expected


@patch("api.subscriptions.upsert_user_subscription")
@patch("api.subscriptions.get_user_by_tg_id")
@patch("api.subscriptions.get_payment_by_id")
def test_activate_extends_active_subscription(mock_get_payment, mock_get_user, mock_upsert):
    """Активная подписка — продлеваем от текущего конца."""
    paid_at = datetime(2026, 3, 1, 10, 0)
    current_until = datetime(2026, 3, 20, 10, 0)  # ещё активна

    mock_get_payment.return_value = {
        "tg_id": 123, "tariff": "weekly_7d",
        "created_at": paid_at,
    }
    mock_get_user.return_value = {
        "id": 1, "tg_id": 123, "subscription_until": current_until,
    }

    from api.subscriptions import activate_subscription
    result = activate_subscription("pay-123")

    expected = _expected_eod_tokyo(current_until + timedelta(days=7))
    mock_upsert.assert_called_once_with(tg_id=123, subscription_until=expected)
    assert result == expected


@patch("api.subscriptions.upsert_user_subscription")
@patch("api.subscriptions.get_user_by_tg_id")
@patch("api.subscriptions.get_payment_by_id")
def test_activate_expired_subscription_starts_from_payment(mock_get_payment, mock_get_user, mock_upsert):
    """Истёкшая подписка — начинаем от даты оплаты."""
    paid_at = datetime(2026, 3, 10, 10, 0)
    expired_until = datetime(2026, 3, 5, 10, 0)  # уже истекла

    mock_get_payment.return_value = {
        "tg_id": 123, "tariff": "trial_1d",
        "created_at": paid_at,
    }
    mock_get_user.return_value = {
        "id": 1, "tg_id": 123, "subscription_until": expired_until,
    }

    from api.subscriptions import activate_subscription
    result = activate_subscription("pay-123")

    expected = _expected_eod_tokyo(paid_at + timedelta(days=1))
    mock_upsert.assert_called_once_with(tg_id=123, subscription_until=expected)
    assert result == expected


@patch("api.subscriptions.get_payment_by_id")
def test_activate_unknown_payment_raises(mock_get_payment):
    """Несуществующий платёж — ValueError."""
    mock_get_payment.return_value = None

    from api.subscriptions import activate_subscription
    import pytest
    with pytest.raises(ValueError, match="Paid payment not found"):
        activate_subscription("nonexistent")


@patch("api.subscriptions.get_payment_by_id")
def test_activate_unknown_tariff_raises(mock_get_payment):
    """Неизвестный тариф — ValueError."""
    mock_get_payment.return_value = {
        "tg_id": 123, "tariff": "nonexistent_tariff",
        "created_at": datetime(2026, 3, 1),
    }

    from api.subscriptions import activate_subscription
    import pytest
    with pytest.raises(ValueError, match="Unknown tariff"):
        activate_subscription("pay-123")
