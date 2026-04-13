"""Тесты для bot_xui/helpers.py — утилиты бота."""
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch
import pytest


# ─────────────────────────────────────────────
#  convert_to_local
# ─────────────────────────────────────────────

def test_convert_to_local_default_offset():
    from bot_xui.helpers import convert_to_local
    dt = datetime(2026, 3, 28, 12, 0, 0)  # UTC
    result = convert_to_local(dt)
    assert result == "28.03.2026"  # +9h = 21:00, same day


def test_convert_to_local_next_day():
    from bot_xui.helpers import convert_to_local
    dt = datetime(2026, 3, 28, 16, 0, 0)  # 16 UTC + 9 = 01:00 next day
    result = convert_to_local(dt)
    assert result == "29.03.2026"


def test_convert_to_local_none():
    from bot_xui.helpers import convert_to_local
    assert convert_to_local(None) == "∞"


def test_convert_to_local_custom_offset():
    from bot_xui.helpers import convert_to_local
    dt = datetime(2026, 1, 1, 0, 0, 0)
    result = convert_to_local(dt, offset_hours=3)
    assert result == "01.01.2026"


# ─────────────────────────────────────────────
#  tariff_emoji
# ─────────────────────────────────────────────

def test_tariff_emoji_short():
    from bot_xui.helpers import tariff_emoji
    assert tariff_emoji(1) == "⚡️"
    assert tariff_emoji(3) == "⚡️"


def test_tariff_emoji_week():
    from bot_xui.helpers import tariff_emoji
    assert tariff_emoji(7) == "📱"


def test_tariff_emoji_month():
    from bot_xui.helpers import tariff_emoji
    assert tariff_emoji(30) == "📦"


def test_tariff_emoji_long():
    from bot_xui.helpers import tariff_emoji
    assert tariff_emoji(90) == "💎"
    assert tariff_emoji(365) == "💎"


# ─────────────────────────────────────────────
#  make_back_keyboard
# ─────────────────────────────────────────────

def test_make_back_keyboard_default():
    from bot_xui.helpers import make_back_keyboard
    kb = make_back_keyboard()
    assert len(kb.inline_keyboard) == 1
    assert len(kb.inline_keyboard[0]) == 1
    btn = kb.inline_keyboard[0][0]
    assert btn.text == "◀️ В меню"
    assert btn.callback_data == "back_to_menu"


def test_make_back_keyboard_custom():
    from bot_xui.helpers import make_back_keyboard
    kb = make_back_keyboard(label="Back", data="go_back")
    btn = kb.inline_keyboard[0][0]
    assert btn.text == "Back"
    assert btn.callback_data == "go_back"


# ─────────────────────────────────────────────
#  make_main_keyboard
# ─────────────────────────────────────────────

def test_make_main_keyboard_has_buttons():
    from bot_xui.helpers import make_main_keyboard
    kb = make_main_keyboard()
    all_btns = [btn for row in kb.inline_keyboard for btn in row]
    texts = [btn.text for btn in all_btns]
    assert any("конфиг" in t.lower() for t in texts)
    assert any("тариф" in t.lower() for t in texts)


def test_make_main_keyboard_has_proxy():
    from bot_xui.helpers import make_main_keyboard
    kb = make_main_keyboard()
    callbacks = [btn.callback_data for row in kb.inline_keyboard for btn in row if btn.callback_data]
    assert "proxy_file" in callbacks


# ─────────────────────────────────────────────
#  make_proxy_file
# ─────────────────────────────────────────────

def test_make_proxy_file():
    from bot_xui.helpers import make_proxy_file
    buf = make_proxy_file()
    assert buf.name == "tiinservice_telegram_proxy.html"
    content = buf.read().decode()
    assert "<!DOCTYPE html>" in content
    assert "tg://proxy" in content
    assert "tiinservice.ru" in content


def test_make_proxy_file_has_both_links():
    from bot_xui.helpers import make_proxy_file, MTPROTO_PROXY_LINK, MTPROTO_HTTPS_LINK
    content = make_proxy_file().read().decode()
    assert MTPROTO_PROXY_LINK in content
    assert MTPROTO_HTTPS_LINK in content


# ─────────────────────────────────────────────
#  MAIN_MENU_TEXT
# ─────────────────────────────────────────────

def test_main_menu_text():
    from bot_xui.helpers import MAIN_MENU_TEXT
    assert "тииҥ VPN" in MAIN_MENU_TEXT
    assert "тариф" in MAIN_MENU_TEXT


# ─────────────────────────────────────────────
#  safe_edit_text
# ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_safe_edit_text_normal_message():
    from bot_xui.helpers import safe_edit_text
    query = MagicMock()
    query.message.photo = None
    query.message.video = None
    query.message.document = None
    query.edit_message_text = AsyncMock()
    result = await safe_edit_text(query, "Hello")
    assert result is True
    query.edit_message_text.assert_called_once()


@pytest.mark.asyncio
async def test_safe_edit_text_media_message():
    from bot_xui.helpers import safe_edit_text
    query = MagicMock()
    query.message.photo = [MagicMock()]  # has photo
    query.message.video = None
    query.message.document = None
    query.message.delete = AsyncMock()
    query.message.chat.send_message = AsyncMock()
    result = await safe_edit_text(query, "Hello")
    assert result is True
    query.message.delete.assert_called_once()
    query.message.chat.send_message.assert_called_once()
