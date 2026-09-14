"""Tests for scripts/win_back_users.py — classify_funnel and message scenarios."""
import sys
import os
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock

import pytest

sys.modules.setdefault("yookassa", MagicMock())

from scripts.win_back_users import (
    classify_funnel,
    get_buttons_for_scenario,
    NOW,
    MB,
    MESSAGES,
    COOLDOWN_DAYS,
    MAX_MESSAGES,
)
from config import ADMIN_TG_ID


def _make_user(
    tg_id=100,
    first_name="Test",
    sub_until=None,
    test_vless=False,
    test_awg=False,
    created_at=None,
):
    return {
        "tg_id": tg_id,
        "first_name": first_name,
        "subscription_until": sub_until,
        "test_vless_activated": test_vless,
        "test_awg_activated": test_awg,
        "created_at": created_at or (NOW - timedelta(days=10)),
    }


def _make_key(tg_id, vpn_type="vless", expires_at=None, created_at=None):
    if expires_at is None:
        expires_at = NOW + timedelta(days=30)
    return {
        "tg_id": tg_id,
        "client_name": f"{vpn_type}_{tg_id}",
        "vpn_type": vpn_type,
        "expires_at": expires_at,
        "created_at": created_at or (NOW - timedelta(days=5)),
    }


def _make_payment(tg_id, amount=199, tariff="month_1"):
    return {
        "tg_id": tg_id,
        "tariff": tariff,
        "amount": amount,
        "status": "paid",
        "created_at": NOW - timedelta(days=15),
    }


def _make_traffic(tg_id, upload=0, download=0, enabled=True, last_online_ms=0):
    return {
        tg_id: {
            "upload": upload,
            "download": download,
            "enabled": enabled,
            "last_online": last_online_ms,
        }
    }


# ═════════════════════════════════════════════
#  General filters & guards
# ═════════════════════════════════════════════

class TestGeneralFilters:
    def test_admin_ignored(self):
        """Admin user must be ignored by classifier."""
        admin = _make_user(tg_id=ADMIN_TG_ID, created_at=NOW - timedelta(days=5))
        candidates = classify_funnel([admin], {}, {}, {})
        assert len(candidates) == 0

    def test_missing_or_zero_tg_id_ignored(self):
        """Users with no tg_id or tg_id=0 are skipped."""
        u1 = _make_user(tg_id=0)
        u2 = _make_user(tg_id=None)
        candidates = classify_funnel([u1, u2], {}, {}, {})
        assert len(candidates) == 0


# ═════════════════════════════════════════════
#  Scenario: never_activated_3d
# ═════════════════════════════════════════════

class TestNeverActivated3d:
    def test_eligible_after_3_days(self):
        """Registered 3+ days ago, no keys, no payments, test not activated."""
        user = _make_user(tg_id=101, created_at=NOW - timedelta(days=3, hours=1))
        candidates = classify_funnel([user], {}, {}, {})
        assert len(candidates) == 1
        assert candidates[0]["tg_id"] == 101
        assert candidates[0]["scenario"] == "never_activated_3d"
        assert candidates[0]["priority"] == 5

    def test_skip_if_registered_recently(self):
        """Registered less than 3 days ago -> not eligible yet."""
        user = _make_user(tg_id=102, created_at=NOW - timedelta(days=2))
        candidates = classify_funnel([user], {}, {}, {})
        assert len(candidates) == 0

    def test_skip_if_test_was_activated(self):
        """Test was activated -> not never_activated_3d."""
        user = _make_user(tg_id=103, test_vless=True, created_at=NOW - timedelta(days=5))
        candidates = classify_funnel([user], {}, {}, {})
        assert len(candidates) == 0

    def test_skip_if_user_has_keys(self):
        """User has keys -> not never_activated_3d."""
        user = _make_user(tg_id=104, created_at=NOW - timedelta(days=5))
        keys = {104: [_make_key(104)]}
        candidates = classify_funnel([user], keys, {}, {})
        assert len(candidates) == 0

    def test_skip_if_user_paid_before(self):
        """User paid before -> not never_activated_3d."""
        user = _make_user(tg_id=105, created_at=NOW - timedelta(days=5))
        payments = {105: [_make_payment(105)]}
        candidates = classify_funnel([user], {}, payments, {})
        assert len(candidates) == 0


# ═════════════════════════════════════════════
#  Scenario: test_0mb_2d & test_ended_2d
# ═════════════════════════════════════════════

class TestTestEndedScenarios:
    def test_test_0mb_2d_when_zero_traffic(self):
        """Test ended 2+ days ago, no payments, 0 MB traffic -> test_0mb_2d."""
        user = _make_user(tg_id=201, test_vless=True, sub_until=NOW - timedelta(days=2))
        test_expires = NOW - timedelta(days=2, hours=1)
        keys = {201: [_make_key(201, expires_at=test_expires)]}
        traffic = _make_traffic(201, upload=0, download=0)

        candidates = classify_funnel([user], keys, {}, traffic)
        assert len(candidates) == 1
        assert candidates[0]["tg_id"] == 201
        assert candidates[0]["scenario"] == "test_0mb_2d"
        assert candidates[0]["priority"] == 2
        assert candidates[0]["total_mb"] == 0.0

    def test_test_ended_2d_when_traffic_exists(self):
        """Test ended 2+ days ago, traffic > 0, no payments -> test_ended_2d."""
        user = _make_user(tg_id=202, test_awg=True, sub_until=NOW - timedelta(days=2))
        test_expires = NOW - timedelta(days=2, hours=1)
        keys = {202: [_make_key(202, expires_at=test_expires)]}
        traffic = _make_traffic(202, upload=10 * MB, download=50 * MB)

        candidates = classify_funnel([user], keys, {}, traffic)
        assert len(candidates) == 1
        assert candidates[0]["tg_id"] == 202
        assert candidates[0]["scenario"] == "test_ended_2d"
        assert candidates[0]["priority"] == 2
        assert candidates[0]["total_mb"] == 60.0

    def test_test_ended_skip_if_less_than_2_days(self):
        """Test ended only 1 day ago -> not triggered yet."""
        user = _make_user(tg_id=203, test_vless=True, sub_until=NOW - timedelta(days=1))
        test_expires = NOW - timedelta(days=1)
        keys = {203: [_make_key(203, expires_at=test_expires)]}

        candidates = classify_funnel([user], keys, {}, {})
        assert len(candidates) == 0

    def test_test_ended_skip_if_user_has_active_sub(self):
        """Test was used, but user currently has active subscription -> skip."""
        user = _make_user(tg_id=204, test_vless=True, sub_until=NOW + timedelta(days=10))
        test_expires = NOW - timedelta(days=5)
        keys = {204: [_make_key(204, expires_at=test_expires)]}

        candidates = classify_funnel([user], keys, {}, {})
        assert len(candidates) == 0

    def test_test_ended_skip_if_user_paid_before(self):
        """User already has payments -> handled by expired flow, not test flow."""
        user = _make_user(tg_id=205, test_vless=True, sub_until=NOW - timedelta(days=3))
        test_expires = NOW - timedelta(days=3)
        keys = {205: [_make_key(205, expires_at=test_expires)]}
        payments = {205: [_make_payment(205)]}

        candidates = classify_funnel([user], keys, payments, {})
        assert len(candidates) == 1
        assert candidates[0]["scenario"] == "expired_2d"


# ═════════════════════════════════════════════
#  Scenario: expired_2d
# ═════════════════════════════════════════════

class TestExpired2d:
    def test_expired_in_window(self):
        """Subscription expired 2 to 9 days ago -> expired_2d."""
        user = _make_user(tg_id=301, sub_until=NOW - timedelta(days=3))
        keys = {301: [_make_key(301, expires_at=NOW - timedelta(days=3))]}
        payments = {301: [_make_payment(301)]}

        candidates = classify_funnel([user], keys, payments, {})
        assert len(candidates) == 1
        assert candidates[0]["tg_id"] == 301
        assert candidates[0]["scenario"] == "expired_2d"
        assert candidates[0]["days_expired"] == 3
        assert candidates[0]["priority"] == 1

    def test_expired_fresh_1_day_skip(self):
        """Subscription expired only 1 day ago -> not in [2, 10) window."""
        user = _make_user(tg_id=302, sub_until=NOW - timedelta(days=1))
        keys = {302: [_make_key(302, expires_at=NOW - timedelta(days=1))]}
        payments = {302: [_make_payment(302)]}

        candidates = classify_funnel([user], keys, payments, {})
        assert len(candidates) == 0

    def test_expired_10_days_skip_for_funnel(self):
        """Expired 10+ days ago -> moves to loyalty campaign, not expired_2d."""
        user = _make_user(tg_id=303, sub_until=NOW - timedelta(days=10))
        keys = {303: [_make_key(303, expires_at=NOW - timedelta(days=10))]}
        payments = {303: [_make_payment(303)]}

        candidates = classify_funnel([user], keys, payments, {})
        assert len(candidates) == 0


# ═════════════════════════════════════════════
#  Scenario: protocol_inactive_7d
# ═════════════════════════════════════════════

class TestProtocolInactive7d:
    def test_active_sub_offline_7_days(self):
        """Active subscription but offline for ≥7 days -> protocol_inactive_7d."""
        user = _make_user(tg_id=401, sub_until=NOW + timedelta(days=20))
        keys = {401: [_make_key(401)]}
        last_online = (NOW - timedelta(days=7, hours=2)).timestamp() * 1000
        traffic = _make_traffic(401, upload=10 * MB, download=10 * MB, last_online_ms=last_online)

        candidates = classify_funnel([user], keys, {}, traffic)
        assert len(candidates) == 1
        assert candidates[0]["tg_id"] == 401
        assert candidates[0]["scenario"] == "protocol_inactive_7d"
        assert candidates[0]["days_offline"] >= 7
        assert candidates[0]["priority"] == 4

    def test_active_sub_offline_less_than_7_days(self):
        """Active subscription offline 5 days -> skip."""
        user = _make_user(tg_id=402, sub_until=NOW + timedelta(days=20))
        keys = {402: [_make_key(402)]}
        last_online = (NOW - timedelta(days=5)).timestamp() * 1000
        traffic = _make_traffic(402, upload=10 * MB, download=10 * MB, last_online_ms=last_online)

        candidates = classify_funnel([user], keys, {}, traffic)
        assert len(candidates) == 0

    def test_active_sub_never_connected_last_online_zero(self):
        """Active subscription with last_online = 0 -> skip."""
        user = _make_user(tg_id=403, sub_until=NOW + timedelta(days=20))
        keys = {403: [_make_key(403)]}
        traffic = _make_traffic(403, upload=0, download=0, last_online_ms=0)

        candidates = classify_funnel([user], keys, {}, traffic)
        assert len(candidates) == 0


# ═════════════════════════════════════════════
#  Buttons & Messages verification
# ═════════════════════════════════════════════

class TestButtonsAndMessages:
    def test_messages_contain_required_placeholders(self):
        """Check template formatting."""
        assert "{promo_code}" in MESSAGES["expired_2d"]
        assert "{promo_code}" in MESSAGES["test_ended_2d"]
        assert "3 дня бесплатного доступа" in MESSAGES["loyalty_gift_10d"]

    @patch("api.db.get_web_token", return_value="token123")
    def test_get_buttons_with_token(self, mock_token):
        """Test buttons rendering with web token."""
        b_test0 = get_buttons_for_scenario("test_0mb_2d", tg_id=501)
        assert any("token123" in btn.get("url", "") for row in b_test0 for btn in row)

        b_expired = get_buttons_for_scenario("expired_2d", tg_id=501)
        assert b_expired[0][0]["callback_data"] == "tariffs"

        b_loyalty = get_buttons_for_scenario("loyalty_survey", tg_id=501)
        assert len(b_loyalty[0]) == 3  # good, normal, bad ratings

    @patch("api.db.get_web_token", return_value=None)
    def test_get_buttons_without_token(self, mock_token):
        """Test buttons fallback when no web token."""
        b_test0 = get_buttons_for_scenario("test_0mb_2d", tg_id=502)
        assert any("https://344988.snk.wtf/my/" in btn.get("url", "") for row in b_test0 for btn in row)
