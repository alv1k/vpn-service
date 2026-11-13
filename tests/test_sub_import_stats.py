import pytest
from unittest.mock import patch, MagicMock
from admin.db import get_sub_import_stats


def test_get_sub_import_stats_structure():
    """Verify that get_sub_import_stats returns the expected dictionary structure."""
    with patch("admin.db._get_conn") as mock_conn:
        mock_cur = MagicMock()
        mock_conn.return_value.cursor.return_value = mock_cur

        mock_cur.fetchone.return_value = {
            "de_clicks_total": 5,
            "ru_clicks_total": 3,
            "de_fetches_total": 12,
            "ru_fetches_total": 8,
            "de_clicks_today": 2,
            "ru_clicks_today": 1,
            "de_fetches_today": 4,
            "ru_fetches_today": 3,
        }
        mock_cur.fetchall.side_effect = [
            [
                {
                    "tg_id": 12345,
                    "web_token": "token123",
                    "de_clicks": 3,
                    "ru_clicks": 1,
                    "de_fetches": 5,
                    "ru_fetches": 2,
                    "last_choice": "de",
                    "last_activity": "2026-11-02T09:00:00",
                }
            ],
            [
                {
                    "tg_id": 12345,
                    "web_token": "token123",
                    "first_name": "TestUser",
                    "last_name": "Tester",
                }
            ]
        ]

        res = get_sub_import_stats(days=30)
        assert "summary" in res
        assert "users" in res
        assert res["days"] == 30
        assert res["summary"]["de_clicks_total"] == 5
        assert len(res["users"]) == 1
        assert res["users"][0]["first_name"] == "TestUser"
        assert res["users"][0]["last_choice"] == "de"
