"""Tests for bot_xui/messaging.py — send_message_by_tg_id and send_link_safely."""
import sys
from unittest.mock import patch, MagicMock, AsyncMock

import pytest

sys.modules.setdefault("yookassa", MagicMock())


# ═════════════════════════════════════════════
#  send_message_by_tg_id
# ═════════════════════════════════════════════

class TestSendMessageByTgId:

    @pytest.mark.asyncio
    @patch("api.db.execute_query")
    async def test_success(self, mock_exec):
        """Sends message and clears bot_blocked flag."""
        from bot_xui.messaging import send_message_by_tg_id

        bot = MagicMock()
        bot.send_message = AsyncMock()

        result = await send_message_by_tg_id(100, "hello", bot=bot)

        assert result is True
        bot.send_message.assert_called_once_with(
            chat_id=100, text="hello", parse_mode=None, reply_markup=None,
        )
        # Should clear bot_blocked
        mock_exec.assert_called_once()
        assert "bot_blocked = 0" in mock_exec.call_args[0][0]

    @pytest.mark.asyncio
    @patch("api.db.execute_query")
    async def test_blocked_user_flagged(self, mock_exec):
        """If user blocked the bot, sets bot_blocked=1 in DB."""
        from bot_xui.messaging import send_message_by_tg_id

        bot = MagicMock()
        bot.send_message = AsyncMock(side_effect=Exception("Forbidden: bot was blocked by the user"))

        result = await send_message_by_tg_id(200, "hi", bot=bot)

        assert result is False
        mock_exec.assert_called_once()
        assert "bot_blocked = 1" in mock_exec.call_args[0][0]

    @pytest.mark.asyncio
    @patch("api.db.execute_query")
    async def test_deactivated_user_flagged(self, mock_exec):
        """Deactivated account also sets bot_blocked."""
        from bot_xui.messaging import send_message_by_tg_id

        bot = MagicMock()
        bot.send_message = AsyncMock(side_effect=Exception("Forbidden: user is deactivated"))

        result = await send_message_by_tg_id(300, "hi", bot=bot)

        assert result is False
        assert "bot_blocked = 1" in mock_exec.call_args[0][0]

    @pytest.mark.asyncio
    async def test_other_error_not_flagged(self):
        """Non-blocked errors return False but don't set bot_blocked."""
        from bot_xui.messaging import send_message_by_tg_id

        bot = MagicMock()
        bot.send_message = AsyncMock(side_effect=Exception("network timeout"))

        with patch("api.db.execute_query") as mock_exec:
            result = await send_message_by_tg_id(400, "hi", bot=bot)

        assert result is False
        mock_exec.assert_not_called()

    @pytest.mark.asyncio
    @patch("api.db.execute_query")
    async def test_with_markup(self, mock_exec):
        """Passes parse_mode and reply_markup through."""
        from bot_xui.messaging import send_message_by_tg_id

        bot = MagicMock()
        bot.send_message = AsyncMock()
        markup = MagicMock()

        await send_message_by_tg_id(500, "<b>bold</b>", parse_mode="HTML", reply_markup=markup, bot=bot)

        bot.send_message.assert_called_once_with(
            chat_id=500, text="<b>bold</b>", parse_mode="HTML", reply_markup=markup,
        )


# ═════════════════════════════════════════════
#  send_link_safely
# ═════════════════════════════════════════════

class TestSendLinkSafely:

    @pytest.mark.asyncio
    async def test_success(self):
        """Sends via raw HTTP API and returns True on 200."""
        from bot_xui.messaging import send_link_safely
        import httpx

        async def mock_handler(request: httpx.Request):
            return httpx.Response(200, json={"ok": True})

        transport = httpx.MockTransport(mock_handler)
        mock_client = httpx.AsyncClient(transport=transport)

        with patch("bot_xui.messaging.httpx.AsyncClient", return_value=mock_client):
            result = await send_link_safely(100, "config link here")

        assert result is True

    @pytest.mark.asyncio
    async def test_api_error_returns_false(self):
        """Non-200 from Telegram API returns False."""
        from bot_xui.messaging import send_link_safely
        import httpx

        async def mock_handler(request: httpx.Request):
            return httpx.Response(403, json={"ok": False, "description": "blocked"})

        transport = httpx.MockTransport(mock_handler)
        mock_client = httpx.AsyncClient(transport=transport)

        with patch("bot_xui.messaging.httpx.AsyncClient", return_value=mock_client):
            result = await send_link_safely(200, "text")

        assert result is False

    @pytest.mark.asyncio
    async def test_with_buttons(self):
        """Buttons are serialized as reply_markup JSON."""
        from bot_xui.messaging import send_link_safely
        import httpx
        import json

        sent_data = {}

        async def mock_handler(request: httpx.Request):
            # httpx sends form data
            content = request.content.decode()
            sent_data["body"] = content
            return httpx.Response(200, json={"ok": True})

        transport = httpx.MockTransport(mock_handler)
        mock_client = httpx.AsyncClient(transport=transport)

        buttons = [[{"text": "Click", "callback_data": "test"}]]

        with patch("bot_xui.messaging.httpx.AsyncClient", return_value=mock_client):
            result = await send_link_safely(100, "text", buttons=buttons)

        assert result is True
        assert "reply_markup" in sent_data["body"]

    @pytest.mark.asyncio
    async def test_exception_returns_false(self):
        """Network error returns False, doesn't crash."""
        from bot_xui.messaging import send_link_safely

        with patch("bot_xui.messaging.httpx.AsyncClient", side_effect=Exception("conn refused")):
            result = await send_link_safely(100, "text")

        assert result is False
