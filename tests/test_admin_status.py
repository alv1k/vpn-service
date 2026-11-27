import pytest
from unittest.mock import patch, MagicMock
from admin.routes import _get_online_users

# Mocking the dependencies for `offline_users`
@patch("admin.routes.admin_db")
@patch("admin.routes.subprocess")
@patch("admin.routes.time")
def test_offline_user_filtering(mock_time, mock_subprocess, mock_admin_db):
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

        # Mock SQLite for offline VLESS check
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


import time as _time


@patch("admin.routes.admin_db")
@patch("admin.routes.subprocess")
@patch("admin.routes.time")
def test_offline_includes_nl_node_users(mock_time, mock_subprocess, mock_admin_db):
    """
    NL node clients with lastOnline (and not expired/disabled) must appear in offline_users.
    """
    mock_time.time.return_value = _time.time()
    now_ms = int(_time.time() * 1000)
    online_identities = {("vless", "user1@email.com")}

    nl_inbounds = [{
        "id": 8,
        "tag": "inbound-8",
        "port": 443,
        "protocol": "vless",
        "remark": "VLESS-REALITY-APP",
        "clientStats": [
            # Already online via DE — must be skipped
            {"email": "user1@email.com", "enable": True, "lastOnline": now_ms - 1000, "expiryTime": 0},
            # Offline NL client — must appear
            {"email": "user2@email.com", "enable": True, "lastOnline": now_ms - 3600_000, "expiryTime": 0},
            # Expired — must be skipped
            {"email": "user3@email.com", "enable": True, "lastOnline": now_ms - 3600_000, "expiryTime": now_ms - 1000},
            # Disabled — must be skipped
            {"email": "user4@email.com", "enable": False, "lastOnline": now_ms - 3600_000, "expiryTime": 0},
        ],
    }]

    with patch("admin.routes._get_online_users", return_value=([], online_identities)):
        with patch("admin.routes._fetch_ru_inbounds_cached", return_value=[]), \
             patch("admin.routes._fetch_nl_inbounds_cached", return_value=nl_inbounds):
            mock_admin_db.get_expiry_by_client_names.return_value = {}

            with patch("sqlite3.connect") as mock_conn:
                mock_cur = MagicMock()
                mock_conn.return_value.cursor.return_value = mock_cur
                mock_cur.fetchall.side_effect = [
                    [],  # client_traffics
                    [('{"clients": []}',)],  # inbounds
                ]

                from admin.routes import offline_users
                import asyncio
                result = asyncio.run(offline_users())

    all_names = []
    for u in result:
        if u.get("names"):
            all_names.extend(u["names"])
        elif u.get("name"):
            all_names.append(u["name"])
    assert "user1@email.com" not in all_names
    assert "user2@email.com" in all_names
    assert "user3@email.com" not in all_names
    assert "user4@email.com" not in all_names


@patch("admin.routes.admin_db")
@patch("admin.routes.subprocess")
def test_online_includes_nl_node_users(mock_subprocess, mock_admin_db):
    """
    _get_online_users must include NL node clients with fresh lastOnline as is_nl entries.
    """
    now_ms = int(_time.time() * 1000)

    nl_inbounds = [{
        "id": 8,
        "tag": "inbound-8",
        "port": 443,
        "protocol": "vless",
        "remark": "VLESS-REALITY-APP",
        "clientStats": [
            {"email": "nl_user1@email.com", "enable": True, "lastOnline": now_ms - 10_000,
             "expiryTime": 0, "up": 0, "down": 0},
        ],
    }]

    mock_xui = MagicMock()
    mock_xui.get_inbounds.return_value = []

    with patch("admin.routes._get_xui", return_value=mock_xui), \
         patch("admin.routes._fetch_ru_inbounds_cached", return_value=[]), \
         patch("admin.routes._fetch_ru_client_ips_cached", return_value={}), \
         patch("admin.routes._fetch_nl_inbounds_cached", return_value=nl_inbounds), \
         patch("admin.routes._fetch_nl_client_ips_cached", return_value={"nl_user1@email.com": {"1.2.3.4"}}), \
         patch("sqlite3.connect") as mock_conn:

        mock_cur = MagicMock()
        mock_conn.return_value.cursor.return_value = mock_cur
        mock_cur.fetchall.return_value = []  # no local DE clients online

        online, identities = _get_online_users()

    names = [u["names"] for u in online]
    flat = [n for ns in names for n in ns]
    assert "nl_user1@email.com" in flat
    nl_entry = next(u for u in online if "nl_user1@email.com" in u["names"])
    assert any(ib.get("is_nl") for ib in nl_entry["inbounds"])
    assert ("vless", "nl_user1@email.com") in identities

