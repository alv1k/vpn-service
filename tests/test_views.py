"""Tests for bot_xui/views.py — bot screens: menu, tariffs, configs, instructions."""
import sys
from unittest.mock import patch, MagicMock, AsyncMock
from datetime import datetime, timedelta

import pytest

sys.modules.setdefault("yookassa", MagicMock())


# ═════════════════════════════════════════════
#  show_main_menu
# ═════════════════════════════════════════════

class TestShowMainMenu:

    @pytest.mark.asyncio
    @patch("bot_xui.bot.send_start_screen", new_callable=AsyncMock)
    @patch("bot_xui.views.make_main_keyboard")
    async def test_calls_edit_with_menu(self, mock_keyboard, mock_send):
        """Shows main menu text with keyboard."""
        from bot_xui.views import show_main_menu

        mock_keyboard.return_value = MagicMock()
        query = MagicMock()
        query.from_user.id = 111
        query.message.delete = AsyncMock()

        await show_main_menu(query)

        mock_keyboard.assert_called_once_with(111)
        mock_send.assert_called_once()


# ═════════════════════════════════════════════
#  show_instructions
# ═════════════════════════════════════════════

class TestShowInstructions:

    @pytest.mark.asyncio
    async def test_shows_platform_links(self):
        """Instructions include Android, iOS, desktop, TV app links."""
        from bot_xui.views import show_instructions

        query = MagicMock()
        query.edit_message_text = AsyncMock()

        await show_instructions(query)

        call_kwargs = query.edit_message_text.call_args
        text = call_kwargs[0][0]
        markup = call_kwargs[1]["reply_markup"]

        assert "Как подключиться" in text
        # Check keyboard has multiple rows for different platforms
        rows = markup.inline_keyboard
        assert len(rows) >= 4  # Android, iOS, desktop, TV + back


# ═════════════════════════════════════════════
#  _build_tariff_text_and_keyboard
# ═════════════════════════════════════════════

class TestBuildTariffTextAndKeyboard:

    @patch("bot_xui.views.get_permanent_discount", return_value=0)
    @patch("bot_xui.views.is_vless_test_activated", return_value=False)
    @patch("bot_xui.views.is_awg_test_activated", return_value=False)
    def test_buy_mode_shows_test_tariff(self, mock_awg, mock_vless, mock_disc):
        """Buy mode with no tests used shows free test tariff."""
        from bot_xui.views import _build_tariff_text_and_keyboard

        text, markup = _build_tariff_text_and_keyboard(100, mode="buy")

        assert "Тарифы VPN" in text
        assert "бесплатно" in text.lower()
        # Should have test tariff button
        all_callbacks = [btn.callback_data for row in markup.inline_keyboard for btn in row if btn.callback_data]
        assert any("test_24h" in cb for cb in all_callbacks)

    @patch("bot_xui.views.get_permanent_discount", return_value=0)
    @patch("bot_xui.views.is_vless_test_activated", return_value=True)
    @patch("bot_xui.views.is_awg_test_activated", return_value=False)
    def test_buy_mode_hides_test_if_used(self, mock_awg, mock_vless, mock_disc):
        """If VLESS test already used, no free test button."""
        from bot_xui.views import _build_tariff_text_and_keyboard

        text, markup = _build_tariff_text_and_keyboard(100, mode="buy")

        all_callbacks = [btn.callback_data for row in markup.inline_keyboard for btn in row if btn.callback_data]
        assert not any("test_24h" in cb for cb in all_callbacks)

    @patch("bot_xui.views.get_permanent_discount", return_value=25)
    @patch("bot_xui.views.is_vless_test_activated", return_value=True)
    @patch("bot_xui.views.is_awg_test_activated", return_value=True)
    def test_discount_shown(self, mock_awg, mock_vless, mock_disc):
        """Permanent discount is displayed in text and button prices."""
        from bot_xui.views import _build_tariff_text_and_keyboard

        text, markup = _build_tariff_text_and_keyboard(100, mode="buy")

        assert "25%" in text

    @patch("bot_xui.views.get_permanent_discount", return_value=0)
    @patch("bot_xui.views.is_vless_test_activated", return_value=False)
    @patch("bot_xui.views.is_awg_test_activated", return_value=False)
    def test_renew_mode(self, mock_awg, mock_vless, mock_disc):
        """Renew mode shows 'Продление' header and _renew suffix in callbacks."""
        from bot_xui.views import _build_tariff_text_and_keyboard

        text, markup = _build_tariff_text_and_keyboard(100, mode="renew")

        assert "Продление" in text
        regular_callbacks = [btn.callback_data for row in markup.inline_keyboard for btn in row
                            if btn.callback_data and btn.callback_data.startswith("buy_tariff_")]
        # All regular tariff buttons should have _renew suffix
        for cb in regular_callbacks:
            if "test_24h" not in cb:
                assert cb.endswith("_renew"), f"Expected _renew suffix: {cb}"

    @patch("bot_xui.views.get_permanent_discount", return_value=0)
    @patch("bot_xui.views.is_vless_test_activated", return_value=False)
    @patch("bot_xui.views.is_awg_test_activated", return_value=False)
    def test_back_button_always_present(self, mock_awg, mock_vless, mock_disc):
        """Last row always has a back button."""
        from bot_xui.views import _build_tariff_text_and_keyboard

        _, markup = _build_tariff_text_and_keyboard(100, mode="buy")

        last_row = markup.inline_keyboard[-1]
        assert any(btn.callback_data == "back_to_menu" for btn in last_row)


# ═════════════════════════════════════════════
#  show_tariffs / show_renew_tariffs
# ═════════════════════════════════════════════

class TestShowTariffs:

    @pytest.mark.asyncio
    @patch("bot_xui.views._build_tariff_text_and_keyboard")
    async def test_show_tariffs(self, mock_build):
        """show_tariffs delegates to _build with mode=buy."""
        from bot_xui.views import show_tariffs

        mock_build.return_value = ("text", MagicMock())
        query = MagicMock()
        query.from_user.id = 100
        query.edit_message_text = AsyncMock()

        await show_tariffs(query)

        mock_build.assert_called_once_with(100, mode="buy")

    @pytest.mark.asyncio
    @patch("bot_xui.views._build_tariff_text_and_keyboard")
    async def test_show_renew_tariffs(self, mock_build):
        """show_renew_tariffs saves context and uses mode=renew."""
        from bot_xui.views import show_renew_tariffs

        mock_build.return_value = ("text", MagicMock())
        query = MagicMock()
        query.from_user.id = 100
        query.message.reply_text = AsyncMock()

        context = MagicMock()
        context.user_data = {}

        await show_renew_tariffs(query, context, inbound_id=1, client_name="tiin_100")

        mock_build.assert_called_once_with(100, mode="renew")
        assert context.user_data["renew_info"] == {"inbound_id": 1, "client_name": "tiin_100"}


# ═════════════════════════════════════════════
#  _refresh_vless_links
# ═════════════════════════════════════════════

class TestRefreshVlessLinks:

    @patch("bot_xui.views.update_vless_link")
    @patch("requests.get")
    def test_updates_link_on_success(self, mock_get, mock_update):
        """Fetches sub URL, decodes base64, updates DB."""
        from bot_xui.views import _refresh_vless_links
        import base64

        xui = MagicMock()
        xui.get_client_subscription_url.return_value = "https://sub/100"

        vless = "vless://uuid@host:443?type=tcp#remark"
        mock_get.return_value = MagicMock(
            status_code=200,
            text=base64.b64encode(vless.encode()).decode(),
        )

        _refresh_vless_links(100, xui)

        mock_update.assert_called_once_with(100, vless)

    @patch("bot_xui.views.update_vless_link")
    def test_no_sub_url_skips(self, mock_update):
        """If XUI returns no sub URL, does nothing."""
        from bot_xui.views import _refresh_vless_links

        xui = MagicMock()
        xui.get_client_subscription_url.return_value = None

        _refresh_vless_links(100, xui)

        mock_update.assert_not_called()

    @patch("bot_xui.views.update_vless_link")
    @patch("requests.get")
    def test_non_vless_link_ignored(self, mock_get, mock_update):
        """If decoded content doesn't start with vless://, skip."""
        from bot_xui.views import _refresh_vless_links
        import base64

        xui = MagicMock()
        xui.get_client_subscription_url.return_value = "https://sub/100"

        mock_get.return_value = MagicMock(
            status_code=200,
            text=base64.b64encode(b"trojan://something").decode(),
        )

        _refresh_vless_links(100, xui)

        mock_update.assert_not_called()

    @patch("bot_xui.views.update_vless_link")
    @patch("requests.get", side_effect=Exception("timeout"))
    def test_error_does_not_crash(self, mock_get, mock_update):
        """Network error is caught silently."""
        from bot_xui.views import _refresh_vless_links

        xui = MagicMock()
        xui.get_client_subscription_url.return_value = "https://sub/100"

        _refresh_vless_links(100, xui)  # should not raise

        mock_update.assert_not_called()


# ═════════════════════════════════════════════
#  show_configs
# ═════════════════════════════════════════════

class TestShowConfigs:

    @pytest.mark.asyncio
    @patch("bot_xui.views.safe_edit_text", new_callable=AsyncMock)
    @patch("bot_xui.views.get_keys_by_tg_id")
    @patch("bot_xui.views._refresh_vless_links")
    async def test_with_active_keys(self, mock_refresh, mock_keys, mock_edit):
        """Shows config list when user has active keys."""
        from bot_xui.views import show_configs

        mock_keys.return_value = [
            {"client_name": "tiin_100", "vpn_type": "vless",
             "expires_at": datetime.utcnow() + timedelta(days=10),
             "vless_link": "vless://uuid@host", "subscription_link": "https://sub/100"},
        ]

        query = MagicMock()
        query.from_user.id = 100

        xui = MagicMock()
        await show_configs(query, xui)

        mock_edit.assert_called_once()
        text = mock_edit.call_args[0][1]
        assert "VLESS" in text

    @pytest.mark.asyncio
    @patch("bot_xui.views._show_no_configs", new_callable=AsyncMock)
    @patch("bot_xui.views.get_keys_by_tg_id", return_value=[])
    @patch("bot_xui.views._refresh_vless_links")
    async def test_no_keys_shows_empty(self, mock_refresh, mock_keys, mock_no_configs):
        """Empty key list shows 'no configs' screen."""
        from bot_xui.views import show_configs

        query = MagicMock()
        query.from_user.id = 100

        await show_configs(query, MagicMock())

        mock_no_configs.assert_called_once()

    @pytest.mark.asyncio
    @patch("bot_xui.views.safe_edit_text", new_callable=AsyncMock)
    @patch("bot_xui.views.get_keys_by_tg_id")
    @patch("bot_xui.views._refresh_vless_links")
    async def test_extra_protocol_buttons(self, mock_refresh, mock_keys, mock_edit):
        """Active VLESS-only user gets +AWG and +SoftEther buttons."""
        from bot_xui.views import show_configs

        mock_keys.return_value = [
            {"client_name": "tiin_100", "vpn_type": "vless",
             "expires_at": datetime.utcnow() + timedelta(days=10),
             "vless_link": "vless://uuid@host", "subscription_link": ""},
        ]

        query = MagicMock()
        query.from_user.id = 100

        await show_configs(query, MagicMock())

        markup = mock_edit.call_args[1]["reply_markup"]
        all_callbacks = [btn.callback_data for row in markup.inline_keyboard for btn in row if btn.callback_data]
        assert "get_awg_config" in all_callbacks
        assert "get_softether_config" in all_callbacks


# ═════════════════════════════════════════════
#  _show_no_configs
# ═════════════════════════════════════════════

class TestShowNoConfigs:

    @pytest.mark.asyncio
    async def test_shows_tariffs_only(self):
        """No-configs screen shows 'choose tariff' and no free-trial button (tests are auto-issued)."""
        from bot_xui.views import _show_no_configs

        query = MagicMock()
        query.from_user.id = 100
        query.edit_message_text = AsyncMock()

        await _show_no_configs(query)

        markup = query.edit_message_text.call_args[1]["reply_markup"]
        all_callbacks = [btn.callback_data for row in markup.inline_keyboard for btn in row]
        assert "test_protocol" not in all_callbacks
        assert "tariffs" in all_callbacks


# ═════════════════════════════════════════════
#  _pretty_config_label
# ═════════════════════════════════════════════

class TestPrettyConfigLabel:
    """Pure-function tests for _pretty_config_label."""

    def test_test_vless_no_payment(self):
        """Test VLESS (tiin_<id>, no payment_id) labeled as 'VLESS — Тестовый'."""
        from bot_xui.views import _pretty_config_label
        key = {"vpn_type": "vless", "client_name": "tiin_42", "payment_id": None}
        emoji, label = _pretty_config_label(key)
        assert emoji == "🟢"
        assert label == "VLESS — Тестовый"

    @patch("bot_xui.views.get_payment_by_id")
    def test_paid_vless_uses_tariff_name(self, mock_payment):
        """Paid VLESS shows tariff name from payment lookup."""
        from bot_xui.views import _pretty_config_label
        mock_payment.return_value = {"tariff": "monthly_30d"}
        key = {"vpn_type": "vless", "client_name": "vl_42_abc", "payment_id": "pay-1"}
        emoji, label = _pretty_config_label(key)
        assert emoji == "🟢"
        assert "VLESS" in label
        assert "Месяц" in label  # name of monthly_30d tariff

    @patch("bot_xui.views.get_payment_by_id")
    def test_short_uses_period(self, mock_payment):
        """short=True prefers period over name (compact for buttons)."""
        from bot_xui.views import _pretty_config_label
        mock_payment.return_value = {"tariff": "weekly_7d"}
        key = {"vpn_type": "vless", "client_name": "x", "payment_id": "p"}
        _, label_long = _pretty_config_label(key, short=False)
        _, label_short = _pretty_config_label(key, short=True)
        # short uses period "7 дней", long uses full name "Неделя — 7 дней"
        assert "7" in label_short

    def test_softether_protocol(self):
        """SoftEther vpn_type returns hardware emoji."""
        from bot_xui.views import _pretty_config_label
        key = {"vpn_type": "softether", "client_name": "se_1", "payment_id": None}
        emoji, label = _pretty_config_label(key)
        assert emoji == "🖥"
        assert "SoftEther" in label

    def test_awg_protocol(self):
        """AWG vpn_type returns mobile emoji."""
        from bot_xui.views import _pretty_config_label
        key = {"vpn_type": "awg", "client_name": "awg_1", "payment_id": None}
        emoji, label = _pretty_config_label(key)
        assert emoji == "📱"
        assert "AmneziaWG" in label

    def test_unknown_protocol_fallback(self):
        """Unknown vpn_type falls back to upper-cased name with key emoji."""
        from bot_xui.views import _pretty_config_label
        key = {"vpn_type": "wireguard", "client_name": "x", "payment_id": None}
        emoji, label = _pretty_config_label(key)
        assert emoji == "🔑"
        assert "WIREGUARD" in label


# ═════════════════════════════════════════════
#  build_main_menu_text
# ═════════════════════════════════════════════

class TestBuildMainMenuText:

    @patch("bot_xui.views.get_keys_by_tg_id", return_value=[])
    def test_no_keys_returns_main_menu_text(self, mock_keys):
        """Empty keys list falls back to MAIN_MENU_TEXT."""
        from bot_xui.views import build_main_menu_text
        from bot_xui.helpers import MAIN_MENU_TEXT
        text = build_main_menu_text(tg_id=100)
        assert text == MAIN_MENU_TEXT

    @patch("bot_xui.views.get_referral_count", return_value=0)
    @patch("bot_xui.views.get_web_token", return_value=None)
    @patch("bot_xui.views._build_subscription_info", return_value=("sub-info-block", False))
    @patch("bot_xui.views.get_keys_by_tg_id")
    def test_active_key_shows_expires(self, mock_keys, mock_sub, mock_token, mock_ref):
        """Active key shows '⏱ Истекает' label."""
        from bot_xui.views import build_main_menu_text
        future = datetime.utcnow() + timedelta(days=30)
        mock_keys.return_value = [
            {"client_name": "x", "vpn_type": "vless", "expires_at": future, "payment_id": "p1"},
        ]
        text = build_main_menu_text(tg_id=100)
        assert "Истекает" in text
        assert "Истекла" not in text
        assert "sub-info-block" in text

    @patch("bot_xui.views.get_referral_count", return_value=0)
    @patch("bot_xui.views.get_web_token", return_value=None)
    @patch("bot_xui.views._build_subscription_info", return_value=("sub-info-block", False))
    @patch("bot_xui.views.get_keys_by_tg_id")
    def test_expired_key_shows_expired_label(self, mock_keys, mock_sub, mock_token, mock_ref):
        """Expired key shows '⏱ Истекла' + 'Подписка истекла' hint."""
        from bot_xui.views import build_main_menu_text
        past = datetime.utcnow() - timedelta(days=1)
        mock_keys.return_value = [
            {"client_name": "x", "vpn_type": "vless", "expires_at": past, "payment_id": "p1"},
        ]
        text = build_main_menu_text(tg_id=100)
        assert "Истекла" in text
        assert "Подписка истекла" in text

    @patch("bot_xui.views.get_referral_count", return_value=0)
    @patch("bot_xui.views.get_web_token", return_value="tok123")
    @patch("bot_xui.views._build_subscription_info", return_value=("sub-info", False))
    @patch("bot_xui.views.get_keys_by_tg_id")
    def test_web_token_renders_guide_link(self, mock_keys, mock_sub, mock_token, mock_ref):
        """If web_token exists, '🪄 Гид по подключению' link is shown."""
        from bot_xui.views import build_main_menu_text
        mock_keys.return_value = [
            {"client_name": "x", "vpn_type": "vless",
             "expires_at": datetime.utcnow() + timedelta(days=10), "payment_id": "p1"},
        ]
        text = build_main_menu_text(tg_id=100)
        assert "Гид по подключению" in text
        assert "tok123" in text

    @patch("bot_xui.views.get_referral_count", return_value=0)
    @patch("bot_xui.views.get_web_token", return_value=None)
    @patch("bot_xui.views._build_subscription_info", return_value=("sub-info", False))
    @patch("bot_xui.views.get_keys_by_tg_id")
    def test_block_order_sub_then_guide_then_referral(self, mock_keys, mock_sub, mock_token, mock_ref):
        """sub-info appears before referral block (and guide if present)."""
        from bot_xui.views import build_main_menu_text
        mock_keys.return_value = [
            {"client_name": "x", "vpn_type": "vless",
             "expires_at": datetime.utcnow() + timedelta(days=10), "payment_id": "p1"},
        ]
        text = build_main_menu_text(tg_id=100)
        sub_pos = text.find("sub-info")
        ref_pos = text.find("Бонус за друзей")
        assert sub_pos < ref_pos
