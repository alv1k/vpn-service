"""Tests for bot_xui/bot.py — _bot_rate_check and button_handler routing."""
import sys
import time
from unittest.mock import patch, MagicMock, AsyncMock

import pytest

sys.modules.setdefault("yookassa", MagicMock())


# ═════════════════════════════════════════════
#  _bot_rate_check
# ═════════════════════════════════════════════

class TestBotRateCheck:

    def setup_method(self):
        """Clear rate limiter state between tests."""
        from bot_xui import bot
        bot._bot_rate.clear()

    def test_allows_under_limit(self):
        from bot_xui.bot import _bot_rate_check
        for _ in range(10):
            assert _bot_rate_check(100) is True

    def test_blocks_over_limit(self):
        from bot_xui.bot import _bot_rate_check
        for _ in range(10):
            _bot_rate_check(200)
        assert _bot_rate_check(200) is False

    def test_resets_after_window(self):
        from bot_xui.bot import _bot_rate_check, _bot_rate
        # Fill up the bucket with old timestamps
        _bot_rate[300] = [time.time() - 60] * 10  # all expired (>30s)
        assert _bot_rate_check(300) is True

    def test_different_users_independent(self):
        from bot_xui.bot import _bot_rate_check
        for _ in range(10):
            _bot_rate_check(400)
        # User 400 is at limit
        assert _bot_rate_check(400) is False
        # User 401 is fresh
        assert _bot_rate_check(401) is True


# ═════════════════════════════════════════════
#  button_handler routing
# ═════════════════════════════════════════════

def _make_update(callback_data: str, tg_id: int = 100):
    """Create a mock Update with callback_query."""
    query = MagicMock()
    query.from_user.id = tg_id
    query.data = callback_data
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()
    query.message.reply_text = AsyncMock()
    query.message.delete = AsyncMock()

    update = MagicMock()
    update.callback_query = query
    return update, query


class TestButtonHandler:

    def setup_method(self):
        from bot_xui import bot
        bot._bot_rate.clear()

    @pytest.mark.asyncio
    @patch("bot_xui.bot.show_configs", new_callable=AsyncMock)
    async def test_my_configs(self, mock_show):
        from bot_xui.bot import button_handler
        update, query = _make_update("my_configs")
        context = MagicMock()

        await button_handler(update, context)

        query.answer.assert_called_once()
        mock_show.assert_called_once()

    @pytest.mark.asyncio
    @patch("bot_xui.bot.show_tariffs", new_callable=AsyncMock)
    async def test_tariffs(self, mock_show):
        from bot_xui.bot import button_handler
        update, query = _make_update("tariffs")
        context = MagicMock()

        await button_handler(update, context)

        mock_show.assert_called_once_with(query)

    @pytest.mark.asyncio
    @patch("bot_xui.bot.show_main_menu", new_callable=AsyncMock)
    async def test_back_to_menu(self, mock_show):
        from bot_xui.bot import button_handler
        update, query = _make_update("back_to_menu")
        context = MagicMock()

        await button_handler(update, context)

        mock_show.assert_called_once_with(query)

    @pytest.mark.asyncio
    @patch("bot_xui.bot.show_instructions", new_callable=AsyncMock)
    async def test_instructions(self, mock_show):
        from bot_xui.bot import button_handler
        update, query = _make_update("instructions")
        context = MagicMock()

        await button_handler(update, context)

        mock_show.assert_called_once_with(query)

    @pytest.mark.asyncio
    @patch("bot_xui.bot.handle_test_vless", new_callable=AsyncMock)
    async def test_test_protocol_goes_to_vless(self, mock_handler):
        """test_protocol shortcut goes straight to VLESS."""
        from bot_xui.bot import button_handler
        update, query = _make_update("test_protocol")
        context = MagicMock()

        await button_handler(update, context)

        mock_handler.assert_called_once()

    @pytest.mark.asyncio
    async def test_test_protocol_choose_shows_options(self):
        """test_protocol_choose shows VLESS + SoftEther buttons."""
        from bot_xui.bot import button_handler
        update, query = _make_update("test_protocol_choose")
        context = MagicMock()

        await button_handler(update, context)

        call_kwargs = query.edit_message_text.call_args
        text = call_kwargs[0][0]
        assert "VLESS" in text
        assert "SoftEther" in text

    @pytest.mark.asyncio
    @patch("bot_xui.bot.handle_test_vless", new_callable=AsyncMock)
    async def test_test_vless(self, mock_handler):
        from bot_xui.bot import button_handler
        update, query = _make_update("test_vless")
        context = MagicMock()

        await button_handler(update, context)

        mock_handler.assert_called_once()

    @pytest.mark.asyncio
    @patch("bot_xui.bot.handle_test_awg", new_callable=AsyncMock)
    async def test_test_awg(self, mock_handler):
        from bot_xui.bot import button_handler
        update, query = _make_update("test_awg")
        context = MagicMock()

        await button_handler(update, context)

        mock_handler.assert_called_once()

    @pytest.mark.asyncio
    @patch("bot_xui.bot.handle_test_softether", new_callable=AsyncMock)
    async def test_test_softether(self, mock_handler):
        from bot_xui.bot import button_handler
        update, query = _make_update("test_softether")
        context = MagicMock()

        await button_handler(update, context)

        mock_handler.assert_called_once()

    @pytest.mark.asyncio
    @patch("bot_xui.bot.show_single_config", new_callable=AsyncMock)
    async def test_show_key_routing(self, mock_show):
        """show_key_<name> routes to show_single_config with correct name."""
        from bot_xui.bot import button_handler
        update, query = _make_update("show_key_tiin_100")
        context = MagicMock()

        await button_handler(update, context)

        mock_show.assert_called_once()
        assert mock_show.call_args[0][1] == "tiin_100"

    @pytest.mark.asyncio
    @patch("bot_xui.bot.handle_get_awg_config", new_callable=AsyncMock)
    async def test_get_awg_config(self, mock_handler):
        from bot_xui.bot import button_handler
        update, query = _make_update("get_awg_config")
        context = MagicMock()

        await button_handler(update, context)

        mock_handler.assert_called_once()

    @pytest.mark.asyncio
    @patch("bot_xui.bot.handle_get_softether_config", new_callable=AsyncMock)
    async def test_get_softether_config(self, mock_handler):
        from bot_xui.bot import button_handler
        update, query = _make_update("get_softether_config")
        context = MagicMock()

        await button_handler(update, context)

        mock_handler.assert_called_once()

    @pytest.mark.asyncio
    async def test_rate_limited(self):
        """Rate-limited user gets throttle message."""
        from bot_xui.bot import button_handler, _bot_rate
        _bot_rate[999] = [time.time()] * 15  # over limit

        update, query = _make_update("tariffs", tg_id=999)
        context = MagicMock()

        await button_handler(update, context)

        query.edit_message_text.assert_called_once()
        assert "Слишком много" in query.edit_message_text.call_args[0][0]

    @pytest.mark.asyncio
    async def test_split_tunneling(self):
        """split_tunneling shows Happ routing info."""
        from bot_xui.bot import button_handler
        update, query = _make_update("split_tunneling")
        context = MagicMock()

        with patch("bot_xui.bot.safe_edit_text", new_callable=AsyncMock) as mock_edit:
            await button_handler(update, context)
            text = mock_edit.call_args[0][1]
            assert "Split tunneling" in text or "Happ" in text
