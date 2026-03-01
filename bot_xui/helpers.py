"""
Вспомогательные функции: форматирование, конвертация времени, общие утилиты.
"""
from datetime import datetime, timedelta
from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def convert_to_local(dt: datetime, offset_hours: int = 9) -> str:
    """Конвертирует UTC datetime в локальное время."""
    if dt is None:
        return "∞"
    return (dt + timedelta(hours=offset_hours)).strftime("%d.%m.%Y %H:%M")


def make_back_keyboard(label: str = "◀️ В меню", data: str = "back_to_menu") -> InlineKeyboardMarkup:
    """Клавиатура с единственной кнопкой «Назад»."""
    return InlineKeyboardMarkup([[InlineKeyboardButton(label, callback_data=data)]])


def make_main_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура главного меню."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 Мои конфиги", callback_data="my_configs")],
        [InlineKeyboardButton("🏷 Тарифы",       callback_data="tariffs")],
        [InlineKeyboardButton("📑 Инструкция и ссылки", callback_data="instructions")],
    ])


MAIN_MENU_TEXT = "👋 Добро пожаловать в tiin vpn manager!\n\nВыберите действие:"


def tariff_emoji(days: int) -> str:
    """Эмодзи для кнопки тарифа по количеству дней."""
    if days <= 3:
        return "⚡️"
    if days <= 7:
        return "📱"
    if days <= 14:
        return "📊"
    if days <= 30:
        return "📦"
    return "💎"


async def safe_edit_text(query, text: str, reply_markup=None, parse_mode: str = "Markdown") -> bool:
    """
    Пробует edit_message_text; при ошибке (например, сообщение с медиа)
    удаляет старое и отвечает новым. Возвращает True при успехе.
    """
    try:
        await query.edit_message_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
        return True
    except Exception:
        try:
            await query.message.delete()
        except Exception:
            pass
        try:
            await query.message.reply_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
            return True
        except Exception:
            return False
