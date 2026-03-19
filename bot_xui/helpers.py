"""
Вспомогательные функции: форматирование, конвертация времени, общие утилиты.
"""
import logging
from datetime import datetime, timedelta
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

logger = logging.getLogger(__name__)


def convert_to_local(dt: datetime, offset_hours: int = 9) -> str:
    """Конвертирует UTC datetime в локальное время."""
    if dt is None:
        return "∞"
    return (dt + timedelta(hours=offset_hours)).strftime("%d.%m.%Y")


def make_back_keyboard(label: str = "◀️ В меню", data: str = "back_to_menu") -> InlineKeyboardMarkup:
    """Клавиатура с единственной кнопкой «Назад»."""
    return InlineKeyboardMarkup([[InlineKeyboardButton(label, callback_data=data)]])


def make_main_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура главного меню."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🎁 Попробовать бесплатно", callback_data="test_protocol")],
        [InlineKeyboardButton("📊 Мои конфиги", callback_data="my_configs")],
        [InlineKeyboardButton("🏷 Тарифы",       callback_data="tariffs")],
        [InlineKeyboardButton("👥 Реферальная программа", callback_data="referral")],
        [InlineKeyboardButton("📑 Инструкция и ссылки", callback_data="instructions")],
        [InlineKeyboardButton("🌐 Личный кабинет (веб)", callback_data="web_portal")],
        [InlineKeyboardButton("📢 Наш канал", url="https://t.me/tiin_service")],
        [InlineKeyboardButton("✉️ Написать нам", callback_data="feedback")],
    ])


MAIN_MENU_TEXT = (
    "⚡️ тииҥ VPN\n\n"
    "🔒 Безопасный и быстрый\n"
    "🌍 Доступ к любым сайтам\n"
    "Выберите действие:"
)


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


async def safe_edit_text(query, text: str, reply_markup=None, parse_mode: str = "HTML") -> bool:
    """
    Для обычных сообщений — edit_message_text.
    Для сообщений с медиа — заменяем медиа на текстовое сообщение через edit_message_media.
    """
    from telegram import InputMediaDocument

    if query.message.photo or query.message.video or query.message.document:
        try:
            # Удаляем сообщение с медиа и отправляем чистый текст
            await query.message.delete()
            await query.message.chat.send_message(text, reply_markup=reply_markup, parse_mode=parse_mode)
            return True
        except Exception as e:
            logger.warning(f"safe_edit_text media fallback failed: {e}")
            return False

    try:
        await query.edit_message_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
        return True
    except Exception as e:
        logger.warning(f"safe_edit_text edit failed: {e}")
        return False