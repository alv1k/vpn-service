import unittest
from unittest.mock import patch, MagicMock
from decimal import Decimal
import admin.db as admin_db

class TestAdminStars(unittest.TestCase):

    @patch("admin.db._get_conn")
    def test_payment_stats_with_stars(self, mock_get_conn):
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.cursor.return_value = mock_cur
        mock_get_conn.return_value = mock_conn

        mock_cur.fetchone.return_value = {
            "total": 100,
            "paid": 50,
            "revenue_rub": Decimal("15000.00"),
            "revenue_stars": Decimal("240.00"),
            "paid_rub": 48,
            "paid_stars": 2,
            "raw_total_amount": Decimal("15240.00"),
        }

        stats = admin_db.payment_stats()
        self.assertEqual(stats["revenue"], 15000.0)
        self.assertEqual(stats["revenue_rub"], 15000.0)
        self.assertEqual(stats["revenue_stars"], 240.0)
        self.assertEqual(stats["paid_rub"], 48)
        self.assertEqual(stats["paid_stars"], 2)

    @patch("admin.db._get_conn")
    def test_recent_payments_stars_currency(self, mock_get_conn):
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.cursor.return_value = mock_cur
        mock_get_conn.return_value = mock_conn

        mock_cur.fetchall.return_value = [
            {
                "payment_id": "stars_charge_test",
                "tg_id": 999,
                "tariff": "monthly_30d",
                "amount": Decimal("120.00"),
                "status": "paid",
                "is_test": 0,
                "currency": "XTR",
                "provider": "stars",
                "first_name": "TestUser",
                "last_name": None,
            },
            {
                "payment_id": "yoo_123",
                "tg_id": 888,
                "tariff": "weekly_7d",
                "amount": Decimal("50.00"),
                "status": "paid",
                "is_test": 0,
                "currency": "RUB",
                "provider": "yookassa",
                "first_name": "RubUser",
                "last_name": None,
            }
        ]

        recent = admin_db.recent_payments(2)
        self.assertEqual(len(recent), 2)
        self.assertEqual(recent[0]["currency"], "XTR")
        self.assertEqual(recent[0]["provider"], "stars")
        self.assertEqual(recent[1]["currency"], "RUB")
        self.assertEqual(recent[1]["provider"], "yookassa")

if __name__ == "__main__":
    unittest.main()
