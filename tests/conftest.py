import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime


@pytest.fixture
def mock_db():
    """Mock MySQL connection pool."""
    with patch("api.db._get_pool") as mock_pool:
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_pool.return_value.get_connection.return_value = mock_conn
        yield {
            "connection": mock_conn,
            "cursor": mock_cursor,
        }


@pytest.fixture
def sample_payment():
    return {
        "id": 1,
        "payment_id": "test-payment-123",
        "tg_id": 123456789,
        "tariff": "monthly_30d",
        "amount": 199,
        "status": "pending",
        "vpn_issued": 0,
        "created_at": datetime(2026, 3, 1, 10, 0),
    }
