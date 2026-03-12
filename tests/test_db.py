"""Тесты для api/db.py — базовые операции с БД."""
from unittest.mock import patch, MagicMock


def _make_mock_pool():
    """Создаёт мок пула с курсором."""
    mock_conn = MagicMock()
    mock_cursor = MagicMock(dictionary=True)
    mock_conn.cursor.return_value = mock_cursor
    mock_pool = MagicMock()
    mock_pool.get_connection.return_value = mock_conn
    return mock_pool, mock_conn, mock_cursor


@patch("api.db._get_pool")
def test_execute_query_insert(mock_get_pool):
    mock_pool, mock_conn, mock_cursor = _make_mock_pool()
    mock_get_pool.return_value = mock_pool
    mock_cursor.lastrowid = 42

    from api.db import execute_query
    result = execute_query("INSERT INTO users (tg_id) VALUES (%s)", (123,))

    assert result == 42
    mock_cursor.execute.assert_called_once_with("INSERT INTO users (tg_id) VALUES (%s)", (123,))
    mock_conn.commit.assert_called_once()
    mock_cursor.close.assert_called_once()
    mock_conn.close.assert_called_once()


@patch("api.db._get_pool")
def test_execute_query_fetch_one(mock_get_pool):
    mock_pool, mock_conn, mock_cursor = _make_mock_pool()
    mock_get_pool.return_value = mock_pool
    mock_cursor.fetchone.return_value = {"tg_id": 123, "first_name": "Test"}

    from api.db import execute_query
    result = execute_query("SELECT * FROM users WHERE tg_id = %s", (123,), fetch='one')

    assert result == {"tg_id": 123, "first_name": "Test"}
    mock_conn.commit.assert_not_called()


@patch("api.db._get_pool")
def test_execute_query_fetch_all(mock_get_pool):
    mock_pool, mock_conn, mock_cursor = _make_mock_pool()
    mock_get_pool.return_value = mock_pool
    mock_cursor.fetchall.return_value = [{"tg_id": 1}, {"tg_id": 2}]

    from api.db import execute_query
    result = execute_query("SELECT tg_id FROM users", fetch='all')

    assert len(result) == 2


@patch("api.db._get_pool")
def test_get_or_create_user_existing(mock_get_pool):
    mock_pool, mock_conn, mock_cursor = _make_mock_pool()
    mock_get_pool.return_value = mock_pool
    # Первый вызов — SELECT (fetchone), второй — не должен быть
    mock_cursor.fetchone.return_value = {"id": 5, "tg_id": 123}

    from api.db import get_or_create_user
    result = get_or_create_user(123, "Test", "User")

    assert result == 5


@patch("api.db._get_pool")
def test_create_payment(mock_get_pool):
    mock_pool, mock_conn, mock_cursor = _make_mock_pool()
    mock_get_pool.return_value = mock_pool
    mock_cursor.lastrowid = 10

    from api.db import create_payment
    create_payment("pay-123", 456, "monthly_30d", 199, "pending")

    mock_cursor.execute.assert_called_once()
    mock_conn.commit.assert_called_once()


@patch("api.db._get_pool")
def test_is_payment_processed_false(mock_get_pool):
    mock_pool, mock_conn, mock_cursor = _make_mock_pool()
    mock_get_pool.return_value = mock_pool
    mock_cursor.fetchone.return_value = {"status": "pending"}

    from api.db import is_payment_processed
    assert is_payment_processed("pay-123") is False


@patch("api.db._get_pool")
def test_is_payment_processed_true(mock_get_pool):
    mock_pool, mock_conn, mock_cursor = _make_mock_pool()
    mock_get_pool.return_value = mock_pool
    mock_cursor.fetchone.return_value = {"status": "succeeded"}

    from api.db import is_payment_processed
    assert is_payment_processed("pay-123") is True


@patch("api.db._get_pool")
def test_deactivate_key_by_payment(mock_get_pool):
    mock_pool, mock_conn, mock_cursor = _make_mock_pool()
    mock_get_pool.return_value = mock_pool

    from api.db import deactivate_key_by_payment
    deactivate_key_by_payment("pay-123")

    args = mock_cursor.execute.call_args
    assert "expires_at = NOW()" in args[0][0]
    mock_conn.commit.assert_called_once()
