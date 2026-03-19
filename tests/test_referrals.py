"""Тесты для реферальной системы — регистрация, награды, подсчёт."""
from unittest.mock import patch, MagicMock
from datetime import datetime


def _make_mock_pool():
    mock_conn = MagicMock()
    mock_cursor = MagicMock(dictionary=True)
    mock_conn.cursor.return_value = mock_cursor
    mock_pool = MagicMock()
    mock_pool.get_connection.return_value = mock_conn
    return mock_pool, mock_conn, mock_cursor


# ─────────────────────────────────────────────
#  register_user_with_referral
# ─────────────────────────────────────────────

@patch("api.db._get_pool")
def test_register_new_user_with_valid_referrer(mock_get_pool):
    """New user + existing referrer → both get rewarded, returns True."""
    mock_pool, mock_conn, mock_cursor = _make_mock_pool()
    mock_get_pool.return_value = mock_pool

    # 1st fetchone: get_user_by_tg_id(new) → None
    # INSERT IGNORE → uses lastrowid, no fetchone
    # 2nd fetchone: get_user_by_tg_id(referrer) → exists
    # 3rd fetchone: _round_subscription_eod SELECT for referrer
    # 4th fetchone: _round_subscription_eod SELECT for newcomer
    mock_cursor.fetchone.side_effect = [
        None,
        {"id": 10, "tg_id": 111},
        {"subscription_until": datetime(2026, 4, 1, 10, 0)},
        {"subscription_until": datetime(2026, 4, 1, 10, 0)},
    ]
    mock_cursor.lastrowid = 1

    from api.db import register_user_with_referral
    result = register_user_with_referral(
        new_tg_id=222, referrer_tg_id=111, first_name="Test", last_name="User"
    )

    assert result is True


@patch("api.db._get_pool")
def test_register_existing_user_returns_false(mock_get_pool):
    """Already registered user → returns False, no rewards."""
    mock_pool, mock_conn, mock_cursor = _make_mock_pool()
    mock_get_pool.return_value = mock_pool
    mock_cursor.fetchone.return_value = {"id": 5, "tg_id": 222}

    from api.db import register_user_with_referral
    result = register_user_with_referral(new_tg_id=222, referrer_tg_id=111)

    assert result is False


@patch("api.db._get_pool")
def test_register_without_referrer(mock_get_pool):
    """New user without referrer → created but returns False (no reward)."""
    mock_pool, mock_conn, mock_cursor = _make_mock_pool()
    mock_get_pool.return_value = mock_pool
    mock_cursor.fetchone.return_value = None
    mock_cursor.lastrowid = 1

    from api.db import register_user_with_referral
    result = register_user_with_referral(new_tg_id=222, referrer_tg_id=None)

    assert result is False


@patch("api.db._get_pool")
def test_register_self_referral_rejected(mock_get_pool):
    """User referring themselves → created but no reward."""
    mock_pool, mock_conn, mock_cursor = _make_mock_pool()
    mock_get_pool.return_value = mock_pool
    mock_cursor.fetchone.return_value = None
    mock_cursor.lastrowid = 1

    from api.db import register_user_with_referral
    result = register_user_with_referral(new_tg_id=222, referrer_tg_id=222)

    assert result is False


@patch("api.db._get_pool")
def test_register_with_nonexistent_referrer(mock_get_pool):
    """Referrer doesn't exist in DB → user created, no reward."""
    mock_pool, mock_conn, mock_cursor = _make_mock_pool()
    mock_get_pool.return_value = mock_pool
    # 1st: get_user_by_tg_id(new) → None
    # 2nd: INSERT → lastrowid = 1
    # 3rd: get_user_by_tg_id(referrer) → None
    mock_cursor.fetchone.side_effect = [None, None, None]
    mock_cursor.lastrowid = 1

    from api.db import register_user_with_referral
    result = register_user_with_referral(new_tg_id=222, referrer_tg_id=999)

    assert result is False


@patch("api.db._get_pool")
def test_register_race_condition_insert_ignore(mock_get_pool):
    """Concurrent registration — INSERT IGNORE returns 0 → False."""
    mock_pool, mock_conn, mock_cursor = _make_mock_pool()
    mock_get_pool.return_value = mock_pool
    mock_cursor.fetchone.return_value = None  # user not found
    mock_cursor.lastrowid = 0  # INSERT IGNORE did nothing (race)

    from api.db import register_user_with_referral
    result = register_user_with_referral(new_tg_id=222, referrer_tg_id=111)

    assert result is False


# ─────────────────────────────────────────────
#  reward_referrer
# ─────────────────────────────────────────────

@patch("api.db._get_pool")
def test_reward_referrer_updates_subscription(mock_get_pool):
    mock_pool, mock_conn, mock_cursor = _make_mock_pool()
    mock_get_pool.return_value = mock_pool
    mock_cursor.fetchone.return_value = {"subscription_until": datetime(2026, 4, 1, 10, 0)}

    from api.db import reward_referrer
    reward_referrer(111)

    # First execute call is the reward SQL, subsequent calls are from _round_subscription_eod
    reward_sql = mock_cursor.execute.call_args_list[0][0][0]
    assert "referral_count = referral_count + 1" in reward_sql
    assert "subscription_until = DATE_ADD" in reward_sql
    assert mock_conn.commit.call_count >= 1


# ─────────────────────────────────────────────
#  reward_newcomer
# ─────────────────────────────────────────────

@patch("api.db._get_pool")
def test_reward_newcomer_sets_subscription(mock_get_pool):
    mock_pool, mock_conn, mock_cursor = _make_mock_pool()
    mock_get_pool.return_value = mock_pool
    mock_cursor.fetchone.return_value = {"subscription_until": datetime(2026, 4, 1, 10, 0)}

    from api.db import reward_newcomer
    reward_newcomer(222)

    # First execute call is the reward SQL, subsequent calls are from _round_subscription_eod
    reward_sql = mock_cursor.execute.call_args_list[0][0][0]
    assert "subscription_until = DATE_ADD(NOW()" in reward_sql
    assert mock_conn.commit.call_count >= 1


# ─────────────────────────────────────────────
#  get_referral_count
# ─────────────────────────────────────────────

@patch("api.db._get_pool")
def test_get_referral_count_with_referrals(mock_get_pool):
    mock_pool, mock_conn, mock_cursor = _make_mock_pool()
    mock_get_pool.return_value = mock_pool
    mock_cursor.fetchone.return_value = {"referral_count": 5}

    from api.db import get_referral_count
    assert get_referral_count(111) == 5


@patch("api.db._get_pool")
def test_get_referral_count_no_user(mock_get_pool):
    mock_pool, mock_conn, mock_cursor = _make_mock_pool()
    mock_get_pool.return_value = mock_pool
    mock_cursor.fetchone.return_value = None

    from api.db import get_referral_count
    assert get_referral_count(999) == 0
