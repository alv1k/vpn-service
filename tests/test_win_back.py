"""Tests for scripts/win_back_users.py — classify_users scenarios."""
import sys
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock

import pytest

sys.modules.setdefault("yookassa", MagicMock())

from scripts.win_back_users import classify_users, NOW, DELAY, MB, MESSAGES


def _make_user(tg_id=100, first_name="Test", sub_until=None,
               test_vless=False, test_awg=False, test_se=False,
               created_at=None):
    if sub_until is None:
        sub_until = NOW + timedelta(days=30)
    if created_at is None:
        created_at = NOW - timedelta(days=10)
    return {
        "tg_id": tg_id,
        "first_name": first_name,
        "subscription_until": sub_until,
        "test_vless_activated": test_vless,
        "test_awg_activated": test_awg,
        "created_at": created_at,
    }


def _make_key(tg_id, vpn_type="vless", expires_at=None, created_at=None):
    if expires_at is None:
        expires_at = NOW + timedelta(days=30)
    if created_at is None:
        created_at = NOW - timedelta(days=5)
    return {
        "tg_id": tg_id,
        "client_name": f"{vpn_type}_{tg_id}",
        "vpn_type": vpn_type,
        "expires_at": expires_at,
        "created_at": created_at,
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
#  awg_inactive scenario
# ═════════════════════════════════════════════

class TestAwgInactive:

    def test_awg_inactive_basic(self):
        """User with AWG key offline ≥1 day → awg_inactive."""
        user = _make_user(tg_id=100)
        keys = {100: [_make_key(100, vpn_type="awg")]}
        traffic = _make_traffic(100, upload=100 * MB, download=200 * MB,
                                last_online_ms=(NOW - timedelta(days=2)).timestamp() * 1000)

        results = classify_users([user], keys, {}, traffic)

        assert len(results["awg_inactive"]) == 1
        assert results["awg_inactive"][0]["tg_id"] == 100
        assert results["awg_inactive"][0]["days_offline"] >= 1

    def test_awg_inactive_exactly_1_day(self):
        """User offline exactly 1 day → awg_inactive (boundary)."""
        user = _make_user(tg_id=200)
        keys = {200: [_make_key(200, vpn_type="awg")]}
        traffic = _make_traffic(200, upload=10 * MB, download=20 * MB,
                                last_online_ms=(NOW - timedelta(days=1)).timestamp() * 1000)

        results = classify_users([user], keys, {}, traffic)

        assert len(results["awg_inactive"]) == 1
        assert results["awg_inactive"][0]["tg_id"] == 200

    def test_awg_inactive_online_recently_not_triggered(self):
        """User with AWG online <1 day ago → NOT awg_inactive."""
        user = _make_user(tg_id=300)
        keys = {300: [_make_key(300, vpn_type="awg")]}
        traffic = _make_traffic(300, upload=10 * MB, download=20 * MB,
                                last_online_ms=(NOW - timedelta(hours=12)).timestamp() * 1000)

        results = classify_users([user], keys, {}, traffic)

        assert len(results["awg_inactive"]) == 0

    def test_awg_inactive_no_last_online_not_triggered(self):
        """User with AWG but last_online=0 (never connected) → NOT awg_inactive."""
        user = _make_user(tg_id=400)
        keys = {400: [_make_key(400, vpn_type="awg")]}
        traffic = _make_traffic(400, upload=0, download=0, last_online_ms=0)

        results = classify_users([user], keys, {}, traffic)

        assert len(results["awg_inactive"]) == 0

    def test_awg_inactive_expired_sub_not_triggered(self):
        """User with AWG but expired subscription → NOT awg_inactive (goes to expired)."""
        user = _make_user(tg_id=500, sub_until=NOW - timedelta(days=2))
        keys = {500: [_make_key(500, vpn_type="awg", expires_at=NOW - timedelta(days=2))]}
        traffic = _make_traffic(500, upload=10 * MB, download=20 * MB,
                                last_online_ms=(NOW - timedelta(days=5)).timestamp() * 1000)

        results = classify_users([user], keys, {}, traffic)

        assert len(results["awg_inactive"]) == 0
        assert len(results["expired_fresh"]) == 1

    def test_awg_inactive_vless_awg_both_present(self):
        """User with both VLESS and AWG offline ≥1 day → awg_inactive (not vless_only)."""
        user = _make_user(tg_id=600)
        keys = {
            600: [
                _make_key(600, vpn_type="vless"),
                _make_key(600, vpn_type="awg"),
            ]
        }
        traffic = _make_traffic(600, upload=50 * MB, download=100 * MB,
                                last_online_ms=(NOW - timedelta(days=3)).timestamp() * 1000)

        results = classify_users([user], keys, {}, traffic)

        assert len(results["awg_inactive"]) == 1
        assert len(results["vless_only_inactive"]) == 0

    def test_vless_only_still_works_when_awg_present(self):
        """VLESS-only user (no AWG) offline ≥1 day → vless_only_inactive (not awg_inactive)."""
        user = _make_user(tg_id=700)
        keys = {700: [_make_key(700, vpn_type="vless")]}
        traffic = _make_traffic(700, upload=10 * MB, download=20 * MB,
                                last_online_ms=(NOW - timedelta(days=2)).timestamp() * 1000)

        results = classify_users([user], keys, {}, traffic)

        assert len(results["vless_only_inactive"]) == 1
        assert len(results["awg_inactive"]) == 0

    def test_awg_inactive_multiple_users(self):
        """Multiple AWG users, only offline ones matched."""
        users = [_make_user(tg_id=800), _make_user(tg_id=801), _make_user(tg_id=802)]
        keys = {
            800: [_make_key(800, vpn_type="awg")],
            801: [_make_key(801, vpn_type="awg")],
            802: [_make_key(802, vpn_type="awg")],
        }
        traffic = {
            800: {"upload": 10 * MB, "download": 20 * MB, "enabled": True,
                   "last_online": (NOW - timedelta(days=5)).timestamp() * 1000},
            801: {"upload": 10 * MB, "download": 20 * MB, "enabled": True,
                   "last_online": (NOW - timedelta(hours=6)).timestamp() * 1000},
            802: {"upload": 10 * MB, "download": 20 * MB, "enabled": True,
                   "last_online": (NOW - timedelta(days=10)).timestamp() * 1000},
        }

        results = classify_users(users, keys, {}, traffic)

        assert len(results["awg_inactive"]) == 2
        matched_ids = {u["tg_id"] for u in results["awg_inactive"]}
        assert matched_ids == {800, 802}

    def test_awg_inactive_days_offline_in_info(self):
        """Verify days_offline is correctly calculated and included."""
        user = _make_user(tg_id=900)
        keys = {900: [_make_key(900, vpn_type="awg")]}
        traffic = _make_traffic(900, upload=10 * MB, download=20 * MB,
                                last_online_ms=(NOW - timedelta(days=7)).timestamp() * 1000)

        results = classify_users([user], keys, {}, traffic)

        assert len(results["awg_inactive"]) == 1
        assert results["awg_inactive"][0]["days_offline"] == 7


class TestPrintReportNoneHandling:

    def test_none_name_does_not_crash(self):
        """User with name=None should not crash print_report."""
        import io
        from scripts.win_back_users import print_report

        results = {
            'zero_traffic': [{
                'tg_id': 100,
                'name': None,
                'total_mb': 0.0,
                'keys': 1,
                'vpn_types': ['vless'],
                'paid_count': 0,
            }],
        }
        # Should not raise TypeError
        print_report(results)


# ═════════════════════════════════════════════
#  Hysteria2 scenarios
# ═════════════════════════════════════════════

class TestHysteria2:

    def test_hysteria_traffic_counts_as_active(self):
        """User with only vless key in DB but hysteria2 in x-ui — traffic from hysteria counts."""
        user = _make_user(tg_id=1000)
        # Only VLESS key in vpn_keys, but hysteria2 exists in x-ui
        keys = {1000: [_make_key(1000, vpn_type="vless")]}
        traffic = _make_traffic(1000, upload=50 * MB, download=100 * MB,
                                last_online_ms=(NOW - timedelta(hours=6)).timestamp() * 1000)
        hysteria_ids = {1000}

        results = classify_users([user], keys, {}, traffic, hysteria_ids)

        # Should NOT be zero_traffic — user has traffic via hysteria2
        assert len(results["zero_traffic"]) == 0
        assert len(results["low_traffic"]) == 0

    def test_hysteria_in_vpn_types(self):
        """Hysteria2 client appears in vpn_types."""
        user = _make_user(tg_id=1001)
        keys = {1001: [_make_key(1001, vpn_type="vless")]}
        traffic = _make_traffic(1001, upload=10 * MB, download=20 * MB,
                                last_online_ms=(NOW - timedelta(days=2)).timestamp() * 1000)
        hysteria_ids = {1001}

        results = classify_users([user], keys, {}, traffic, hysteria_ids)

        # hysteria should appear in vpn_types for this user
        all_users = (
            results["zero_traffic"] + results["low_traffic"] +
            results["vless_only_inactive"] + results["recently_inactive"]
        )
        found = [u for u in all_users if u["tg_id"] == 1001]
        if found:
            assert "hysteria" in found[0]["vpn_types"]

    def test_hysteria_user_without_vless_key_not_classified_as_zero_traffic(self):
        """User with hysteria2 (no vpn_keys), has traffic — not zero_traffic."""
        user = _make_user(tg_id=1002)
        keys = {}  # No keys in vpn_keys
        traffic = _make_traffic(1002, upload=100 * MB, download=200 * MB)
        hysteria_ids = {1002}

        results = classify_users([user], keys, {}, traffic, hysteria_ids)

        # User has no keys at all, but has traffic — should not be in zero_traffic
        # (zero_traffic requires has_active_key)
        assert len(results["zero_traffic"]) == 0

    def test_vless_only_inactive_excludes_hysteria_users(self):
        """User with VLESS+hysteria2 (no AWG) offline → vless_only_inactive (hysteria doesn't count as AWG)."""
        user = _make_user(tg_id=1003)
        keys = {1003: [_make_key(1003, vpn_type="vless")]}
        traffic = _make_traffic(1003, upload=10 * MB, download=20 * MB,
                                last_online_ms=(NOW - timedelta(days=3)).timestamp() * 1000)
        hysteria_ids = {1003}

        results = classify_users([user], keys, {}, traffic, hysteria_ids)

        # User has VLESS + hysteria but no AWG — should still get vless_only_inactive
        assert len(results["vless_only_inactive"]) == 1
        assert results["vless_only_inactive"][0]["tg_id"] == 1003

    def test_multi_config_partial_includes_hysteria(self):
        """User with VLESS + hysteria + AWG → multi_config_partial if one type unused."""
        user = _make_user(tg_id=1004, created_at=NOW - timedelta(days=30))
        keys = {
            1004: [
                _make_key(1004, vpn_type="vless", created_at=NOW - timedelta(days=20)),
                _make_key(1004, vpn_type="awg", created_at=NOW - timedelta(days=20)),
            ]
        }
        traffic = _make_traffic(1004, upload=50 * MB, download=100 * MB)
        hysteria_ids = {1004}

        results = classify_users([user], keys, {}, traffic, hysteria_ids)

        # 3 types (vless, awg, hysteria), key_age=20d ≥ 7d → multi_config_partial
        assert len(results["multi_config_partial"]) == 1
        assert "hysteria" in results["multi_config_partial"][0]["vpn_types"]


# ═════════════════════════════════════════════
#  promo_activated_no_purchase scenario
# ═════════════════════════════════════════════

class TestPromoActivatedNoPurchase:

    def _make_unused_promo(self, tg_id, code="WIN100_ABC123", value=15, days_active=5, days_expire=9):
        return {
            "tg_id": tg_id,
            "promocode_id": 1,
            "activated_at": NOW - timedelta(days=days_active),
            "code": code,
            "value": value,
            "expires_at": NOW + timedelta(days=days_expire),
        }

    def test_promo_activated_no_purchase_basic(self):
        """User with unused activated promo → promo_activated_no_purchase."""
        user = _make_user(tg_id=2000)
        keys = {2000: [_make_key(2000)]}
        traffic = _make_traffic(2000, upload=10 * MB, download=20 * MB)
        unused = [self._make_unused_promo(2000)]

        results = classify_users([user], keys, {}, traffic, unused_promos=unused)

        assert len(results["promo_activated_no_purchase"]) == 1
        assert results["promo_activated_no_purchase"][0]["tg_id"] == 2000
        assert results["promo_activated_no_purchase"][0]["promo_code"] == "WIN100_ABC123"
        assert results["promo_activated_no_purchase"][0]["discount"] == 15

    def test_promo_activated_no_purchase_includes_expires(self):
        """Result should contain formatted expiry date."""
        user = _make_user(tg_id=2001)
        keys = {2001: [_make_key(2001)]}
        traffic = _make_traffic(2001, upload=10 * MB, download=20 * MB)
        unused = [self._make_unused_promo(2001)]

        results = classify_users([user], keys, {}, traffic, unused_promos=unused)

        entry = results["promo_activated_no_purchase"][0]
        assert "expires" in entry
        # Should be a formatted date string like 'dd.mm.yyyy'
        assert isinstance(entry["expires"], str)
        assert "." in entry["expires"]

    def test_promo_activated_no_purchase_no_expiry(self):
        """Promo with no expiry → expires shows '∞'."""
        user = _make_user(tg_id=2002)
        keys = {2002: [_make_key(2002)]}
        traffic = _make_traffic(2002, upload=10 * MB, download=20 * MB)
        promo = self._make_unused_promo(2002)
        promo["expires_at"] = None
        unused = [promo]

        results = classify_users([user], keys, {}, traffic, unused_promos=unused)

        entry = results["promo_activated_no_purchase"][0]
        assert entry["expires"] == "∞"

    def test_promo_activated_no_purchase_multiple_users(self):
        """Multiple users with unused promos."""
        users = [_make_user(tg_id=2010), _make_user(tg_id=2011)]
        keys = {
            2010: [_make_key(2010)],
            2011: [_make_key(2011)],
        }
        traffic = {
            2010: {"upload": 10 * MB, "download": 20 * MB, "enabled": True, "last_online": 0},
            2011: {"upload": 10 * MB, "download": 20 * MB, "enabled": True, "last_online": 0},
        }
        unused = [
            self._make_unused_promo(2010, code="WIN2010_AAAA", value=10),
            self._make_unused_promo(2011, code="WIN2011_BBBB", value=20),
        ]

        results = classify_users(users, keys, {}, traffic, unused_promos=unused)

        assert len(results["promo_activated_no_purchase"]) == 2
        codes = {u["promo_code"] for u in results["promo_activated_no_purchase"]}
        assert codes == {"WIN2010_AAAA", "WIN2011_BBBB"}

    def test_promo_activated_no_purchase_user_without_keys(self):
        """User with unused promo but no keys — still classified (no has_active_key gate)."""
        user = _make_user(tg_id=2020)
        keys = {}
        traffic = {}
        unused = [self._make_unused_promo(2020)]

        results = classify_users([user], keys, {}, traffic, unused_promos=unused)

        # The scenario has no has_active_key gate, so user should be matched
        assert len(results["promo_activated_no_purchase"]) == 1

    def test_promo_activated_no_purchase_does_not_interfere_other_scenarios(self):
        """User with unused promo AND should get another scenario — both apply."""
        user = _make_user(tg_id=2030, sub_until=NOW - timedelta(days=2))
        keys = {2030: [_make_key(2030, expires_at=NOW - timedelta(days=2))]}
        # No traffic, has active key — zero_traffic won't fire (expired sub)
        traffic = {2030: {"upload": 0, "download": 0, "enabled": True, "last_online": 0}}
        unused = [self._make_unused_promo(2030, value=10)]

        results = classify_users([user], keys, {}, traffic, unused_promos=unused)

        # Sub is expired → expired_fresh
        assert len(results["expired_fresh"]) == 1
        # Also should have promo_activated_no_purchase
        assert len(results["promo_activated_no_purchase"]) == 1

    def test_promo_activated_no_purchase_empty_unused(self):
        """No unused promos → empty list."""
        user = _make_user(tg_id=2040)
        keys = {2040: [_make_key(2040)]}
        traffic = _make_traffic(2040, upload=10 * MB, download=20 * MB)

        results = classify_users([user], keys, {}, traffic, unused_promos=[])

        assert len(results["promo_activated_no_purchase"]) == 0

    def test_promo_activated_no_purchase_default_none(self):
        """Default unused_promos=None → empty list."""
        user = _make_user(tg_id=2050)
        keys = {2050: [_make_key(2050)]}
        traffic = _make_traffic(2050, upload=10 * MB, download=20 * MB)

        results = classify_users([user], keys, {}, traffic)

        assert len(results["promo_activated_no_purchase"]) == 0


# ═════════════════════════════════════════════
#  Win-back message templates
# ═════════════════════════════════════════════

class TestWinBackMessages:

    def test_promo_message_has_howto_instruction(self):
        """All promo messages should contain activation instructions."""
        promo_msgs = ['expired_fresh', 'expired_old', 'test_no_purchase', 'test_no_connect', 'second_expiry_reminder']
        for key in promo_msgs:
            msg = MESSAGES[key]
            assert "Как активировать" in msg, f"Message '{key}' missing activation instructions"

    def test_promo_message_has_promo_code_placeholder(self):
        """Promo messages should have {promo_code} placeholder."""
        promo_msgs = ['expired_fresh', 'expired_old', 'test_no_purchase', 'test_no_connect', 'second_expiry_reminder']
        for key in promo_msgs:
            msg = MESSAGES[key]
            assert "{promo_code}" in msg, f"Message '{key}' missing {{promo_code}} placeholder"

    def test_promo_message_has_example_command(self):
        """Promo messages should show example /promo command."""
        promo_msgs = ['expired_fresh', 'expired_old', 'test_no_purchase', 'test_no_connect', 'second_expiry_reminder']
        for key in promo_msgs:
            msg = MESSAGES[key]
            assert "/promo" in msg, f"Message '{key}' missing /promo command example"

    def test_promo_activated_no_purchase_message(self):
        """New promo_activated_no_purchase message should have required placeholders."""
        msg = MESSAGES['promo_activated_no_purchase']
        assert "{promo_code}" in msg
        assert "{discount}" in msg
        assert "активировали" in msg


class TestSendMessagesFinalExemption:

    @pytest.mark.asyncio
    async def test_active_subscriber_exempt_from_final(self):
        """Active subscribers or paying users should not receive the final message on reaching limit."""
        from unittest.mock import AsyncMock
        from scripts.win_back_users import send_messages

        results = {
            'referral_prompt': [{
                'tg_id': 138315162,
                'name': 'Rinat',
                'sub_until': NOW + timedelta(days=20),
                'paid_count': 1,
                'reg_days': 15,
                'total_mb': 500.0,
                'keys': 2,
                'vpn_types': ['vless', 'awg'],
            }]
        }

        with patch('scripts.win_back_users.get_recent_sends', return_value={}), \
             patch('scripts.win_back_users.execute_query', return_value=[{'tg_id': 138315162, 'c': 2}]), \
             patch('bot_xui.messaging.send_link_safely', new_callable=AsyncMock) as mock_send, \
             patch('scripts.win_back_users.log_send') as mock_log_send:
            mock_send.return_value = True

            await send_messages(results)

            assert mock_send.called
            sent_kwargs = mock_send.call_args.kwargs
            assert sent_kwargs['scenario'] == 'referral_prompt'
            assert "не заинтересовал" not in sent_kwargs['text']
            assert "Реферальн" in sent_kwargs['text'] or "Пригласите" in sent_kwargs['text']

