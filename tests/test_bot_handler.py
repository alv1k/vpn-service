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

        mock_show.assert_called_once()
        assert mock_show.call_args[0][0] == query

    @pytest.mark.asyncio
    @patch("bot_xui.bot.show_instructions", new_callable=AsyncMock)
    async def test_instructions(self, mock_show):
        from bot_xui.bot import button_handler
        update, query = _make_update("instructions")
        context = MagicMock()

        await button_handler(update, context)

        mock_show.assert_called_once_with(query)

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


# ═════════════════════════════════════════════
#  send_start_screen
# ═════════════════════════════════════════════

class TestSendStartScreen:

    def setup_method(self):
        """Reset cached file_id between tests."""
        from bot_xui import bot
        bot._START_IMAGE_FILE_ID = None

    @pytest.mark.asyncio
    async def test_falls_back_to_text_when_image_missing(self):
        """If image file does not exist, falls back to send_message with text."""
        from bot_xui import bot
        from pathlib import Path

        chat = MagicMock()
        chat.send_message = AsyncMock()
        chat.send_photo = AsyncMock()

        with patch.object(bot, "START_IMAGE_PATH", Path("/nonexistent/missing.png")):
            await bot.send_start_screen(chat, "menu text", reply_markup=None)

        chat.send_message.assert_called_once()
        chat.send_photo.assert_not_called()

    @pytest.mark.asyncio
    async def test_falls_back_to_text_when_caption_too_long(self):
        """Caption > 1024 chars → falls back to text-only message."""
        from bot_xui import bot

        chat = MagicMock()
        chat.send_message = AsyncMock()
        chat.send_photo = AsyncMock()

        long_text = "x" * 1100  # > 1024 limit
        # Real path exists; failure is caption length, not file
        await bot.send_start_screen(chat, long_text, reply_markup=None)

        chat.send_message.assert_called_once()
        chat.send_photo.assert_not_called()

    @pytest.mark.asyncio
    async def test_first_send_reads_disk_and_caches_file_id(self):
        """First send reads PNG from disk and caches the returned file_id."""
        from bot_xui import bot

        # Mock the returned message with a photo file_id
        photo_obj = MagicMock()
        photo_obj.file_id = "cached-file-id-1"
        sent_msg = MagicMock()
        sent_msg.photo = [photo_obj]

        chat = MagicMock()
        chat.send_photo = AsyncMock(return_value=sent_msg)
        chat.send_message = AsyncMock()

        await bot.send_start_screen(chat, "short menu", reply_markup=None)

        chat.send_photo.assert_called_once()
        # Cache populated
        assert bot._START_IMAGE_FILE_ID == "cached-file-id-1"

    @pytest.mark.asyncio
    async def test_second_send_uses_cached_file_id(self):
        """When _START_IMAGE_FILE_ID is set, subsequent sends pass it instead of bytes."""
        from bot_xui import bot
        bot._START_IMAGE_FILE_ID = "preset-id-99"

        photo_obj = MagicMock()
        photo_obj.file_id = "preset-id-99"
        sent_msg = MagicMock()
        sent_msg.photo = [photo_obj]

        chat = MagicMock()
        chat.send_photo = AsyncMock(return_value=sent_msg)
        chat.send_message = AsyncMock()

        await bot.send_start_screen(chat, "short menu", reply_markup=None)

        # Photo arg should be the cached id string, not a file handle
        chat.send_photo.assert_called_once()
        assert chat.send_photo.call_args[1]["photo"] == "preset-id-99"

    @pytest.mark.asyncio
    async def test_telegram_error_falls_back_to_text(self):
        """If Telegram raises during send_photo, falls back to send_message."""
        from bot_xui import bot
        bot._START_IMAGE_FILE_ID = "cached-id"

        chat = MagicMock()
        chat.send_photo = AsyncMock(side_effect=RuntimeError("Telegram timeout"))
        chat.send_message = AsyncMock()

        await bot.send_start_screen(chat, "short menu", reply_markup=None)

        chat.send_photo.assert_called_once()
        chat.send_message.assert_called_once()
