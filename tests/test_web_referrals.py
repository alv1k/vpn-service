"""Тесты для веб-реферальной системы — process_web_referral, награды по user_id."""
from unittest.mock import patch, MagicMock, call
from datetime import datetime


def _make_mock_pool():
    mock_conn = MagicMock()
    mock_cursor = MagicMock(dictionary=True)
    mock_conn.cursor.return_value = mock_cursor
    mock_pool = MagicMock()
    mock_pool.get_connection.return_value = mock_conn
    return mock_pool, mock_conn, mock_cursor


# ─────────────────────────────────────────────
#  process_web_referral — happy path
# ─────────────────────────────────────────────

@patch("api.db._web_referral_promo_days", return_value=0)
@patch("api.db._get_pool")
def test_web_referral_success(mock_get_pool, mock_promo):
    """Valid referrer token + new newcomer → atomic UPDATE succeeds, both rewarded."""
    mock_pool, mock_conn, mock_cursor = _make_mock_pool()
    mock_get_pool.return_value = mock_pool

    # rowcount=1 means atomic UPDATE SET referred_by succeeded
    mock_cursor.rowcount = 1

    mock_cursor.fetchone.side_effect = [
        {"id": 10},                                          # 1. referrer lookup by web_token
        {"cnt": 0},                                          # 2. per-referrer daily limit check
        # Atomic UPDATE (uses _update_rowcount, checks rowcount)
        {"subscription_until": datetime(2026, 4, 1, 10, 0)}, # 3. _round_eod SELECT for referrer
        {"subscription_until": datetime(2026, 4, 1, 10, 0)}, # 4. _round_eod SELECT for newcomer
    ]

    from api.db import process_web_referral
    result = process_web_referral(newcomer_id=20, referrer_web_token="abc123")

    assert result is True

    sqls = [c[0][0] for c in mock_cursor.execute.call_args_list]

    # Verify atomic UPDATE with referred_by IS NULL guard
    assert any("SET referred_by" in s and "referred_by IS NULL" in s for s in sqls)

    # Verify referrer rewarded
    assert any("referral_count = referral_count + 1" in s for s in sqls)

    # Verify both rewarded with subscription days
    reward_sqls = [s for s in sqls if "subscription_until = DATE_ADD" in s]
    assert len(reward_sqls) >= 2


# ─────────────────────────────────────────────
#  process_web_referral — empty/null ref token
# ─────────────────────────────────────────────

@patch("api.db._get_pool")
def test_web_referral_empty_token(mock_get_pool):
    """Empty ref token → returns False, no DB calls."""
    mock_pool, mock_conn, mock_cursor = _make_mock_pool()
    mock_get_pool.return_value = mock_pool

    from api.db import process_web_referral
    assert process_web_referral(newcomer_id=20, referrer_web_token="") is False
    assert process_web_referral(newcomer_id=20, referrer_web_token=None) is False
    mock_cursor.execute.assert_not_called()


# ─────────────────────────────────────────────
#  process_web_referral — referrer not found
# ─────────────────────────────────────────────

@patch("api.db._get_pool")
def test_web_referral_referrer_not_found(mock_get_pool):
    """Invalid web_token → returns False."""
    mock_pool, mock_conn, mock_cursor = _make_mock_pool()
    mock_get_pool.return_value = mock_pool
    mock_cursor.fetchone.return_value = None  # referrer not found

    from api.db import process_web_referral
    assert process_web_referral(newcomer_id=20, referrer_web_token="bad_token") is False


# ─────────────────────────────────────────────
#  process_web_referral — self-referral
# ─────────────────────────────────────────────

@patch("api.db._get_pool")
def test_web_referral_self_referral(mock_get_pool):
    """Referrer is the same user → returns False."""
    mock_pool, mock_conn, mock_cursor = _make_mock_pool()
    mock_get_pool.return_value = mock_pool
    mock_cursor.fetchone.return_value = {"id": 20}  # referrer.id == newcomer_id

    from api.db import process_web_referral
    assert process_web_referral(newcomer_id=20, referrer_web_token="my_token") is False


# ─────────────────────────────────────────────
#  process_web_referral — already referred
# ─────────────────────────────────────────────

@patch("api.db._get_pool")
def test_web_referral_already_referred(mock_get_pool):
    """Newcomer already has referred_by → atomic UPDATE affects 0 rows → no reward."""
    mock_pool, mock_conn, mock_cursor = _make_mock_pool()
    mock_get_pool.return_value = mock_pool

    # rowcount=0 means referred_by was already set (UPDATE WHERE referred_by IS NULL matched nothing)
    mock_cursor.rowcount = 0

    mock_cursor.fetchone.side_effect = [
        {"id": 10},              # referrer found
        {"cnt": 0},              # daily limit check
    ]

    from api.db import process_web_referral
    assert process_web_referral(newcomer_id=20, referrer_web_token="abc123") is False

    # No reward SQLs should have been called
    sqls = [c[0][0] for c in mock_cursor.execute.call_args_list]
    assert not any("referral_count" in s for s in sqls)


# ─────────────────────────────────────────────
#  process_web_referral — daily limit reached
# ─────────────────────────────────────────────

@patch("api.db._get_pool")
def test_web_referral_daily_limit_reached(mock_get_pool):
    """Referrer already has 5 referrals today → returns False."""
    mock_pool, mock_conn, mock_cursor = _make_mock_pool()
    mock_get_pool.return_value = mock_pool
    mock_cursor.fetchone.side_effect = [
        {"id": 10},              # referrer found
        {"cnt": 5},              # daily limit reached
    ]

    from api.db import process_web_referral
    assert process_web_referral(newcomer_id=20, referrer_web_token="abc123") is False

    sqls = [c[0][0] for c in mock_cursor.execute.call_args_list]
    assert not any("referral_count" in s for s in sqls)
    assert not any("SET referred_by" in s for s in sqls)


# ─────────────────────────────────────────────
#  reward_referrer_by_id
# ─────────────────────────────────────────────

@patch("api.db._get_pool")
def test_reward_referrer_by_id(mock_get_pool):
    """Referrer gets REFERRAL_REWARD_DAYS added, referral_count incremented."""
    mock_pool, mock_conn, mock_cursor = _make_mock_pool()
    mock_get_pool.return_value = mock_pool
    mock_cursor.fetchone.return_value = {"subscription_until": datetime(2026, 4, 1, 10, 0)}

    from api.db import reward_referrer_by_id
    reward_referrer_by_id(10)

    reward_sql = mock_cursor.execute.call_args_list[0][0][0]
    assert "referral_count = referral_count + 1" in reward_sql
    assert "WHERE id = %s" in reward_sql
    assert mock_conn.commit.call_count >= 1


# ─────────────────────────────────────────────
#  reward_newcomer_by_id — normal (no promo)
# ─────────────────────────────────────────────

@patch("api.db._web_referral_promo_days", return_value=0)
@patch("api.db._get_pool")
def test_reward_newcomer_by_id_normal(mock_get_pool, mock_promo):
    """Outside promo → newcomer gets REFERRAL_NEWCOMER_DAYS (3)."""
    mock_pool, mock_conn, mock_cursor = _make_mock_pool()
    mock_get_pool.return_value = mock_pool
    mock_cursor.fetchone.return_value = {"subscription_until": datetime(2026, 4, 1, 10, 0)}

    from api.db import reward_newcomer_by_id
    from config import REFERRAL_NEWCOMER_DAYS
    reward_newcomer_by_id(20)

    reward_sql = mock_cursor.execute.call_args_list[0][0][0]
    assert "WHERE id = %s" in reward_sql
    # Check the days param passed
    days_param = mock_cursor.execute.call_args_list[0][0][1][0]
    assert days_param == REFERRAL_NEWCOMER_DAYS


# ─────────────────────────────────────────────
#  reward_newcomer_by_id — promo active (20 days)
# ─────────────────────────────────────────────

@patch("api.db._web_referral_promo_days", return_value=20)
@patch("api.db._get_pool")
def test_reward_newcomer_by_id_promo(mock_get_pool, mock_promo):
    """During promo → newcomer gets 20 days."""
    mock_pool, mock_conn, mock_cursor = _make_mock_pool()
    mock_get_pool.return_value = mock_pool
    mock_cursor.fetchone.return_value = {"subscription_until": datetime(2026, 4, 1, 10, 0)}

    from api.db import reward_newcomer_by_id
    reward_newcomer_by_id(20)

    days_param = mock_cursor.execute.call_args_list[0][0][1][0]
    assert days_param == 20


# ─────────────────────────────────────────────
#  _web_referral_promo_days
# ─────────────────────────────────────────────

@patch("api.db._get_pool")
@patch("api.db.datetime")
def test_promo_days_active_under_limit(mock_dt, mock_get_pool):
    """On promo date, within time window, < 5 used → returns 20."""
    from datetime import timezone, timedelta
    TZ = timezone(timedelta(hours=9))
    mock_dt.now.return_value = datetime(2026, 3, 30, 12, 0, tzinfo=TZ)
    mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)

    mock_pool, _, mock_cursor = _make_mock_pool()
    mock_get_pool.return_value = mock_pool
    mock_cursor.fetchone.return_value = {"cnt": 3}

    from api.db import _web_referral_promo_days
    assert _web_referral_promo_days() == 20


@patch("api.db._get_pool")
@patch("api.db.datetime")
def test_promo_days_limit_reached(mock_dt, mock_get_pool):
    """On promo date but 5 already used → returns 0."""
    from datetime import timezone, timedelta
    TZ = timezone(timedelta(hours=9))
    mock_dt.now.return_value = datetime(2026, 3, 30, 12, 0, tzinfo=TZ)
    mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)

    mock_pool, _, mock_cursor = _make_mock_pool()
    mock_get_pool.return_value = mock_pool
    mock_cursor.fetchone.return_value = {"cnt": 5}

    from api.db import _web_referral_promo_days
    assert _web_referral_promo_days() == 0


@patch("api.db.datetime")
def test_promo_days_wrong_date(mock_dt):
    """Not promo date → returns 0, no DB call."""
    from datetime import timezone, timedelta
    TZ = timezone(timedelta(hours=9))
    mock_dt.now.return_value = datetime(2026, 3, 31, 12, 0, tzinfo=TZ)
    mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)

    from api.db import _web_referral_promo_days
    assert _web_referral_promo_days() == 0


@patch("api.db.datetime")
def test_promo_days_before_start_time(mock_dt):
    """Promo date but before 9:00 → returns 0."""
    from datetime import timezone, timedelta
    TZ = timezone(timedelta(hours=9))
    mock_dt.now.return_value = datetime(2026, 3, 30, 8, 59, tzinfo=TZ)
    mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)

    from api.db import _web_referral_promo_days
    assert _web_referral_promo_days() == 0
