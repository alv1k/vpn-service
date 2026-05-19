import pytest
from unittest.mock import patch, MagicMock
from admin.routes import _get_online_users

# Mocking the dependencies for `offline_users`
@patch("admin.routes.awg_db")
@patch("admin.routes.admin_db")
@patch("admin.routes.subprocess")
@patch("admin.routes.time")
def test_offline_user_filtering(mock_time, mock_subprocess, mock_admin_db, mock_awg_db):
    """
    Test that offline_users correctly filters out users present in online_identities.
    """
    # 1. Setup mocks
    # Online user: (type='vless', name='user1@email.com')
    # Offline user: (type='vless', name='user2@email.com')
    online_identities = {("vless", "user1@email.com")}
    
    # Mock _get_online_users to return our identities
    with patch("admin.routes._get_online_users", return_value=([], online_identities)):
        # Mock database call to return empty expiry for simplicity
        mock_admin_db.get_expiry_by_client_names.return_value = {}

        # Mock SoftEther list_users to return an empty list so it doesn't pollute the test
        with patch("bot_xui.softether.list_users", return_value=[]):
            # 2. Mock SQLite for offline VLESS check
            # We need to simulate the sqlite connection
            with patch("sqlite3.connect") as mock_conn:
                mock_cur = MagicMock()
                mock_conn.return_value.cursor.return_value = mock_cur

                # Simulate one online user and one offline user in the DB
                mock_cur.fetchall.side_effect = [
                    [], # client_traffics
                    [('{"clients": [{"email": "user1@email.com"}, {"email": "user2@email.com"}]}',)] # inbounds
                ]

                from admin.routes import offline_users

                # This requires an async context if called directly, but we are in a test
                import asyncio
                result = asyncio.run(offline_users())

                # 3. Assertions — offline users are merged by tg_id/user_id
                # Each entry has "names" array; check that online user is excluded
                all_names = []
                for u in result:
                    if u.get("names"):
                        all_names.extend(u["names"])
                    elif u.get("name"):
                        all_names.append(u["name"])
                assert "user1@email.com" not in all_names
                assert "user2@email.com" in all_names
                assert len(result) == 1
