"""
Вспомогательные функции: форматирование, конвертация времени, общие утилиты.
"""
import io
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


MTPROTO_PROXY_LINK = (
    "tg://proxy?server=91.132.161.112&port=8443"
    "&secret=7tZhp-UUviXSuUagLCZgx8UzNDQ5ODguc25rLnd0Zg"
)

MTPROTO_HTTPS_LINK = (
    "https://t.me/proxy?server=91.132.161.112&port=8443"
    "&secret=7tZhp-UUviXSuUagLCZgx8UzNDQ5ODguc25rLnd0Zg"
)


def make_proxy_file() -> io.BytesIO:
    """HTML-файл для настройки прокси Telegram в один клик."""
    html = f"""\
<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>тииҥ VPN — Прокси Telegram</title>
<style>
  body {{ font-family: -apple-system, system-ui, sans-serif; display: flex;
         justify-content: center; align-items: center; min-height: 100vh;
         margin: 0; background: #1a1a2e; color: #eee; }}
  .card {{ text-align: center; padding: 2rem; max-width: 400px; }}
  h1 {{ font-size: 1.4rem; margin-bottom: .5rem; }}
  p {{ color: #aaa; font-size: .95rem; line-height: 1.5; }}
  .btn {{ display: inline-block; margin-top: 1.2rem; padding: .9rem 2rem;
          background: #0088cc; color: #fff; text-decoration: none;
          border-radius: 8px; font-size: 1.1rem; font-weight: 600; }}
  .btn:active {{ background: #006fa3; }}
  .alt {{ margin-top: 1rem; font-size: .85rem; color: #888; }}
  .alt a {{ color: #0088cc; }}
</style>
</head>
<body>
<div class="card">
  <h1>⚡ тииҥ VPN — Прокси</h1>
  <p>Нажмите кнопку, чтобы подключить<br>бесплатный прокси для Telegram</p>
  <a class="btn" href="{MTPROTO_PROXY_LINK}">Подключить прокси</a>
  <p class="alt">Не открывается? <a href="{MTPROTO_HTTPS_LINK}">Попробуйте эту ссылку</a></p>
</div>
</body>
</html>"""
    buf = io.BytesIO(html.encode())
    buf.name = "tiin_proxy.html"
    return buf


def make_main_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура главного меню."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🎁 Попробовать бесплатно", callback_data="test_protocol")],
        [
            InlineKeyboardButton("🔑 Мои конфиги", callback_data="my_configs"),
            InlineKeyboardButton("💎 Тарифы", callback_data="tariffs"),
        ],
        [
            InlineKeyboardButton("📖 Инструкция", callback_data="instructions"),
            InlineKeyboardButton("🌐 Кабинет", callback_data="web_portal"),
        ],
        [
            InlineKeyboardButton("👥 Пригласить друга", callback_data="referral"),
            InlineKeyboardButton("✉️ Поддержка", callback_data="feedback"),
        ],
        [
            InlineKeyboardButton("📢 Наш канал", url="https://t.me/tiin_service"),
            InlineKeyboardButton("🔗 Прокси TG", callback_data="proxy_file"),
        ],
    ])

MAIN_MENU_TEXT = (
    "⚡️ <b>тииҥ VPN</b>\n\n"
    "🔒 Безопасный  ·  🚀 Быстрый  ·  🌍 Без ограничений\n\n"
    f'🔗 <a href="{MTPROTO_PROXY_LINK}">Подключить прокси для Telegram</a>'
    " — доступ к боту без VPN\n\n"
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