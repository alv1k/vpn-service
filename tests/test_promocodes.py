"""Тесты для промокодов — валидация, использование, деактивация, активация без покупки."""
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock, call


def _make_mock_pool():
    mock_conn = MagicMock()
    mock_cursor = MagicMock(dictionary=True)
    mock_conn.cursor.return_value = mock_cursor
    mock_pool = MagicMock()
    mock_pool.get_connection.return_value = mock_conn
    return mock_pool, mock_conn, mock_cursor


VALID_PROMO = {
    "id": 1,
    "code": "ALENA",
    "type": "discount",
    "value": 50,
    "max_uses": 100,
    "used_count": 5,
    "per_user_limit": 1,
    "expires_at": datetime.now() + timedelta(days=30),
    "is_active": 1,
    "created_at": datetime(2026, 3, 1),
}


# ─────────────────────────────────────────────
#  create_promocode
# ─────────────────────────────────────────────

@patch("api.db._get_pool")
def test_create_promocode(mock_get_pool):
    mock_pool, mock_conn, mock_cursor = _make_mock_pool()
    mock_get_pool.return_value = mock_pool
    mock_cursor.lastrowid = 1

    from api.db import create_promocode
    result = create_promocode("spring", "days", 7, max_uses=50)

    assert result == 1
    args = mock_cursor.execute.call_args[0]
    assert "INSERT INTO promocodes" in args[0]
    # code should be uppercased
    assert args[1][0] == "SPRING"
    mock_conn.commit.assert_called_once()


# ─────────────────────────────────────────────
#  get_promocode
# ─────────────────────────────────────────────

@patch("api.db._get_pool")
def test_get_promocode_found(mock_get_pool):
    mock_pool, mock_conn, mock_cursor = _make_mock_pool()
    mock_get_pool.return_value = mock_pool
    mock_cursor.fetchone.return_value = VALID_PROMO

    from api.db import get_promocode
    result = get_promocode("alena")

    assert result["code"] == "ALENA"
    # code uppercased in query
    args = mock_cursor.execute.call_args[0]
    assert args[1] == ("ALENA",)


@patch("api.db._get_pool")
def test_get_promocode_not_found(mock_get_pool):
    mock_pool, mock_conn, mock_cursor = _make_mock_pool()
    mock_get_pool.return_value = mock_pool
    mock_cursor.fetchone.return_value = None

    from api.db import get_promocode
    assert get_promocode("NONEXISTENT") is None


# ─────────────────────────────────────────────
#  validate_promocode
# ─────────────────────────────────────────────

@patch("api.db._get_pool")
def test_validate_promocode_valid(mock_get_pool):
    mock_pool, mock_conn, mock_cursor = _make_mock_pool()
    mock_get_pool.return_value = mock_pool
    # First call: get_promocode (fetchone), second call: get_user_promo_usage_count (fetchone)
    mock_cursor.fetchone.side_effect = [VALID_PROMO, {"cnt": 0}]

    from api.db import validate_promocode
    promo, error = validate_promocode("ALENA", tg_id=999)

    assert error is None
    assert promo["code"] == "ALENA"


@patch("api.db._get_pool")
def test_validate_promocode_not_found(mock_get_pool):
    mock_pool, mock_conn, mock_cursor = _make_mock_pool()
    mock_get_pool.return_value = mock_pool
    mock_cursor.fetchone.return_value = None

    from api.db import validate_promocode
    promo, error = validate_promocode("FAKE", tg_id=999)

    assert promo is None
    assert error == "Промокод не найден"


@patch("api.db._get_pool")
def test_validate_promocode_inactive(mock_get_pool):
    mock_pool, mock_conn, mock_cursor = _make_mock_pool()
    mock_get_pool.return_value = mock_pool
    inactive_promo = {**VALID_PROMO, "is_active": 0}
    mock_cursor.fetchone.return_value = inactive_promo

    from api.db import validate_promocode
    promo, error = validate_promocode("ALENA", tg_id=999)

    assert promo is None
    assert error == "Промокод неактивен"


@patch("api.db._get_pool")
def test_validate_promocode_expired(mock_get_pool):
    mock_pool, mock_conn, mock_cursor = _make_mock_pool()
    mock_get_pool.return_value = mock_pool
    expired_promo = {**VALID_PROMO, "expires_at": datetime.now() - timedelta(days=1)}
    mock_cursor.fetchone.return_value = expired_promo

    from api.db import validate_promocode
    promo, error = validate_promocode("ALENA", tg_id=999)

    assert promo is None
    assert error == "Промокод истёк"


@patch("api.db._get_pool")
def test_validate_promocode_exhausted(mock_get_pool):
    mock_pool, mock_conn, mock_cursor = _make_mock_pool()
    mock_get_pool.return_value = mock_pool
    exhausted_promo = {**VALID_PROMO, "max_uses": 10, "used_count": 10}
    mock_cursor.fetchone.return_value = exhausted_promo

    from api.db import validate_promocode
    promo, error = validate_promocode("ALENA", tg_id=999)

    assert promo is None
    assert error == "Промокод исчерпан"


@patch("api.db._get_pool")
def test_validate_promocode_already_used_by_user(mock_get_pool):
    mock_pool, mock_conn, mock_cursor = _make_mock_pool()
    mock_get_pool.return_value = mock_pool
    # First call: get_promocode, second call: usage count = 1 (already used)
    mock_cursor.fetchone.side_effect = [VALID_PROMO, {"cnt": 1}]

    from api.db import validate_promocode
    promo, error = validate_promocode("ALENA", tg_id=999)

    assert promo is None
    assert error == "Вы уже использовали этот промокод"


@patch("api.db._get_pool")
def test_validate_promocode_no_expiry(mock_get_pool):
    """Promo with expires_at=None should be valid regardless of date."""
    mock_pool, mock_conn, mock_cursor = _make_mock_pool()
    mock_get_pool.return_value = mock_pool
    no_expiry_promo = {**VALID_PROMO, "expires_at": None}
    mock_cursor.fetchone.side_effect = [no_expiry_promo, {"cnt": 0}]

    from api.db import validate_promocode
    promo, error = validate_promocode("ALENA", tg_id=999)

    assert error is None
    assert promo is not None


@patch("api.db._get_pool")
def test_validate_promocode_unlimited_uses(mock_get_pool):
    """Promo with max_uses=None should never be exhausted."""
    mock_pool, mock_conn, mock_cursor = _make_mock_pool()
    mock_get_pool.return_value = mock_pool
    unlimited_promo = {**VALID_PROMO, "max_uses": None, "used_count": 9999}
    mock_cursor.fetchone.side_effect = [unlimited_promo, {"cnt": 0}]

    from api.db import validate_promocode
    promo, error = validate_promocode("ALENA", tg_id=999)

    assert error is None
    assert promo is not None


# ─────────────────────────────────────────────
#  use_promocode
# ─────────────────────────────────────────────

@patch("api.db._get_pool")
def test_use_promocode(mock_get_pool):
    mock_pool, mock_conn, mock_cursor = _make_mock_pool()
    mock_get_pool.return_value = mock_pool

    from api.db import use_promocode
    use_promocode(promo_id=1, tg_id=999)

    # Should make 2 queries: INSERT usage + UPDATE used_count
    assert mock_cursor.execute.call_count == 2
    insert_sql = mock_cursor.execute.call_args_list[0][0][0]
    update_sql = mock_cursor.execute.call_args_list[1][0][0]
    assert "INSERT INTO promocode_usages" in insert_sql
    assert "used_count = used_count + 1" in update_sql


# ─────────────────────────────────────────────
#  deactivate_promocode
# ─────────────────────────────────────────────

@patch("api.db._get_pool")
def test_deactivate_promocode(mock_get_pool):
    mock_pool, mock_conn, mock_cursor = _make_mock_pool()
    mock_get_pool.return_value = mock_pool

    from api.db import deactivate_promocode
    result = deactivate_promocode("alena")

    assert result is True
    args = mock_cursor.execute.call_args[0]
    assert "is_active = 0" in args[0]
    assert args[1] == ("ALENA",)


# ─────────────────────────────────────────────
#  list_active_promocodes
# ─────────────────────────────────────────────

@patch("api.db._get_pool")
def test_list_active_promocodes(mock_get_pool):
    mock_pool, mock_conn, mock_cursor = _make_mock_pool()
    mock_get_pool.return_value = mock_pool
    mock_cursor.fetchall.return_value = [VALID_PROMO]

    from api.db import list_active_promocodes
    result = list_active_promocodes()

    assert len(result) == 1
    assert result[0]["code"] == "ALENA"


@patch("api.db._get_pool")
def test_list_active_promocodes_empty(mock_get_pool):
    mock_pool, mock_conn, mock_cursor = _make_mock_pool()
    mock_get_pool.return_value = mock_pool
    mock_cursor.fetchall.return_value = []

    from api.db import list_active_promocodes
    result = list_active_promocodes()

    assert result == []


# ─────────────────────────────────────────────
#  log_promo_activation
# ─────────────────────────────────────────────

@patch("api.db._get_pool")
def test_log_promo_activation(mock_get_pool):
    mock_pool, mock_conn, mock_cursor = _make_mock_pool()
    mock_get_pool.return_value = mock_pool

    from api.db import log_promo_activation
    log_promo_activation(promo_id=5, tg_id=12345)

    args = mock_cursor.execute.call_args[0]
    assert "INSERT IGNORE INTO promo_activations" in args[0]
    assert args[1] == (5, 12345)
    mock_conn.commit.assert_called_once()


@patch("api.db._get_pool")
def test_log_promo_activation_idempotent(mock_get_pool):
    """INSERT IGNORE should not fail on duplicate activation."""
    mock_pool, mock_conn, mock_cursor = _make_mock_pool()
    mock_get_pool.return_value = mock_pool

    from api.db import log_promo_activation
    # Should not raise even if called twice
    log_promo_activation(promo_id=5, tg_id=12345)
    log_promo_activation(promo_id=5, tg_id=12345)

    assert mock_cursor.execute.call_count == 2


# ─────────────────────────────────────────────
#  get_users_with_unused_activated_promos
# ─────────────────────────────────────────────

@patch("api.db._get_pool")
def test_get_users_with_unused_activated_promos_basic(mock_get_pool):
    mock_pool, mock_conn, mock_cursor = _make_mock_pool()
    mock_get_pool.return_value = mock_pool
    mock_cursor.fetchall.return_value = [
        {
            "tg_id": 100,
            "promocode_id": 1,
            "activated_at": datetime.now() - timedelta(days=5),
            "code": "WIN100_ABC123",
            "value": 15,
            "expires_at": datetime.now() + timedelta(days=9),
        }
    ]

    from api.db import get_users_with_unused_activated_promos
    result = get_users_with_unused_activated_promos(min_days=2)

    assert len(result) == 1
    assert result[0]["tg_id"] == 100
    assert result[0]["code"] == "WIN100_ABC123"
    assert result[0]["value"] == 15
    # Verify SQL contains key clauses
    sql = mock_cursor.execute.call_args[0][0]
    assert "promo_activations" in sql
    assert "promocode_usages" in sql
    assert "payments" in sql
    assert "is_active = 1" in sql


@patch("api.db._get_pool")
def test_get_users_with_unused_activated_promos_empty(mock_get_pool):
    mock_pool, mock_conn, mock_cursor = _make_mock_pool()
    mock_get_pool.return_value = mock_pool
    mock_cursor.fetchall.return_value = []

    from api.db import get_users_with_unused_activated_promos
    result = get_users_with_unused_activated_promos(min_days=2)

    assert result == []


@patch("api.db._get_pool")
def test_get_users_with_unused_activated_promos_none_result(mock_get_pool):
    """fetchall returns None → should return empty list."""
    mock_pool, mock_conn, mock_cursor = _make_mock_pool()
    mock_get_pool.return_value = mock_pool
    mock_cursor.fetchall.return_value = None

    from api.db import get_users_with_unused_activated_promos
    result = get_users_with_unused_activated_promos(min_days=2)

    assert result == []


@patch("api.db._get_pool")
def test_get_users_with_unused_activated_promos_min_days_param(mock_get_pool):
    """min_days parameter should be passed to SQL."""
    mock_pool, mock_conn, mock_cursor = _make_mock_pool()
    mock_get_pool.return_value = mock_pool
    mock_cursor.fetchall.return_value = []

    from api.db import get_users_with_unused_activated_promos
    get_users_with_unused_activated_promos(min_days=7)

    args = mock_cursor.execute.call_args[0]
    assert args[1] == (7,)


@patch("api.db._get_pool")
def test_get_users_with_unused_activated_promos_excludes_used(mock_get_pool):
    """Users who already used the promo (in promocode_usages) should be excluded."""
    mock_pool, mock_conn, mock_cursor = _make_mock_pool()
    mock_get_pool.return_value = mock_pool
    # Empty result — the SQL LEFT JOIN + IS NULL should filter out used promos
    mock_cursor.fetchall.return_value = []

    from api.db import get_users_with_unused_activated_promos
    result = get_users_with_unused_activated_promos(min_days=2)

    assert result == []
    sql = mock_cursor.execute.call_args[0][0]
    # Verify the query checks for unused promos
    assert "pu.promocode_id IS NULL" in sql


@patch("api.db._get_pool")
def test_get_users_with_unused_activated_promos_excludes_paid(mock_get_pool):
    """Users who already paid should be excluded."""
    mock_pool, mock_conn, mock_cursor = _make_mock_pool()
    mock_get_pool.return_value = mock_pool
    mock_cursor.fetchall.return_value = []

    from api.db import get_users_with_unused_activated_promos
    result = get_users_with_unused_activated_promos(min_days=2)

    assert result == []
    sql = mock_cursor.execute.call_args[0][0]
    assert "pay.tg_id IS NULL" in sql
