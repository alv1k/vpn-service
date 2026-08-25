"""
Вспомогательные функции: форматирование, конвертация времени, общие утилиты.
"""
import io
import logging
import sqlite3
import json
import qrcode
from datetime import datetime, timedelta
from io import BytesIO
from urllib.parse import quote
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from config import MTPROTO_SERVER, MTPROTO_PORT, MTPROTO_SECRET, BOT_USERNAME, REFERRAL_REWARD_DAYS, XUI_SUB_PATH

logger = logging.getLogger(__name__)


async def _log_message(tg_id: int, source: str, scenario: str, text: str, status: str = "sent"):
    """Log a sent message to the database."""
    try:
        from api.db import log_message_sent
        short = (text[:200] + "...") if text and len(text) > 200 else (text or "")
        log_message_sent(tg_id=tg_id, source=source, scenario=scenario,
                         message_text=short, status=status)
    except Exception:
        pass


async def log_and_reply_text(update_or_query, text: str, scenario: str = "bot",
                              reply_markup=None, parse_mode: str = "HTML", **kwargs):
    """Reply with text and log to database."""
    if hasattr(update_or_query, 'message') and update_or_query.message:
        result = await update_or_query.message.reply_text(text, reply_markup=reply_markup,
                                                          parse_mode=parse_mode, **kwargs)
    elif hasattr(update_or_query, 'chat'):
        result = await update_or_query.chat.send_message(text, reply_markup=reply_markup,
                                                         parse_mode=parse_mode, **kwargs)
    else:
        result = await update_or_query.reply_text(text, reply_markup=reply_markup,
                                                  parse_mode=parse_mode, **kwargs)
    tg_id = _get_tg_id(update_or_query)
    if tg_id:
        await _log_message(tg_id, "bot", scenario, text)
    return result


async def log_and_reply_photo(update_or_query, photo, caption: str = None, scenario: str = "bot",
                               reply_markup=None, parse_mode: str = "HTML", **kwargs):
    """Reply with photo and log to database."""
    if hasattr(update_or_query, 'message') and update_or_query.message:
        result = await update_or_query.message.reply_photo(photo, caption=caption,
                                                           reply_markup=reply_markup,
                                                           parse_mode=parse_mode, **kwargs)
    elif hasattr(update_or_query, 'chat'):
        result = await update_or_query.chat.send_photo(photo, caption=caption,
                                                       reply_markup=reply_markup,
                                                       parse_mode=parse_mode, **kwargs)
    else:
        result = await update_or_query.reply_photo(photo, caption=caption,
                                                   reply_markup=reply_markup,
                                                   parse_mode=parse_mode, **kwargs)
    tg_id = _get_tg_id(update_or_query)
    if tg_id:
        await _log_message(tg_id, "bot", scenario, caption or "")
    return result


async def log_and_reply_document(update_or_query, document, caption: str = None, scenario: str = "bot",
                                  reply_markup=None, parse_mode: str = "HTML", **kwargs):
    """Reply with document and log to database."""
    if hasattr(update_or_query, 'message') and update_or_query.message:
        result = await update_or_query.message.reply_document(document, caption=caption,
                                                              reply_markup=reply_markup,
                                                              parse_mode=parse_mode, **kwargs)
    elif hasattr(update_or_query, 'chat'):
        result = await update_or_query.chat.send_document(document, caption=caption,
                                                          reply_markup=reply_markup,
                                                          parse_mode=parse_mode, **kwargs)
    else:
        result = await update_or_query.reply_document(document, caption=caption,
                                                      reply_markup=reply_markup,
                                                      parse_mode=parse_mode, **kwargs)
    tg_id = _get_tg_id(update_or_query)
    if tg_id:
        await _log_message(tg_id, "bot", scenario, caption or "")
    return result


async def log_and_send_message(chat, text: str, scenario: str = "bot",
                                reply_markup=None, parse_mode: str = "HTML", **kwargs):
    """Send message to chat and log to database."""
    result = await chat.send_message(text, reply_markup=reply_markup, parse_mode=parse_mode, **kwargs)
    tg_id = chat.id if hasattr(chat, 'id') else None
    if tg_id:
        await _log_message(tg_id, "bot", scenario, text)
    return result


def _get_tg_id(obj):
    """Extract tg_id from update, query, or chat object."""
    try:
        if hasattr(obj, 'effective_user') and obj.effective_user:
            return obj.effective_user.id
        if hasattr(obj, 'from_user') and obj.from_user:
            return obj.from_user.id
        if hasattr(obj, 'message') and obj.message and obj.message.from_user:
            return obj.message.from_user.id
        if hasattr(obj, 'chat') and obj.chat:
            return obj.chat.id
    except Exception:
        pass
    return None


def convert_to_local(dt: datetime, offset_hours: int = 9) -> str:
    """Конвертирует UTC datetime в локальное время."""
    if dt is None:
        return "∞"
    return (dt + timedelta(hours=offset_hours)).strftime("%d.%m.%Y")


PUBLIC_BASE_URL = "https://344988.snk.wtf"
WEB_BASE_URL = "https://344988.snk.wtf"


def make_qr_bytes(data: str, box_size: int = 10, border: int = 5) -> BytesIO:
    """Генерирует PNG QR-код и возвращает BytesIO. Единая функция для всего проекта."""
    qr = qrcode.QRCode(version=1, box_size=box_size, border=border)
    qr.add_data(data)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    bio = BytesIO()
    bio.name = "qr.png"
    img.save(bio, "PNG")
    bio.seek(0)
    return bio


def get_user_sub_url(tg_id: int, users_id: int) -> str:
    """Получает subId пользователя из 3x-ui"""

    db_path = "/etc/x-ui/x-ui.db"
    tg_id_str = str(tg_id)
    
    # Массив портов для проверки
    ports_to_check = [7443]  # Добавьте нужные порты
    
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        # Безопасная проверка tg_id
        if tg_id and tg_id != 0:  # Проверяем что tg_id не None и не 0
            search_pattern = str(tg_id)
        else:
            search_pattern = "web_" + str(users_id)  # Преобразуем users_id в строку
        
        for port in ports_to_check:
            cursor.execute("""
                SELECT settings 
                FROM inbounds 
                WHERE port = ?
            """, (port,))
            
            result = cursor.fetchone()
            
            if result:
                settings = json.loads(result[0])
                clients = settings.get('clients', [])
                
                for client in clients:
                    client_email = client.get('email', '')
                    if search_pattern in client_email:
                        sub_id = client.get('subId')
                        if sub_id:
                            conn.close()
                            return f"{XUI_SUB_PATH}/sub/{sub_id}"
                                    
        conn.close()
        return ""
        
    except Exception as e:
        print(f"Error getting sub_url for tg_id {tg_id}: {e}")
        return ""


def make_back_keyboard(label: str = "◀️ В меню", data: str = "back_to_menu") -> InlineKeyboardMarkup:
    """Клавиатура с единственной кнопкой «Назад»."""
    return InlineKeyboardMarkup([[InlineKeyboardButton(label, callback_data=data)]])


MTPROTO_PROXY_LINK = (
    f"tg://proxy?server={MTPROTO_SERVER}&port={MTPROTO_PORT}"
    f"&secret={MTPROTO_SECRET}"
)

MTPROTO_HTTPS_LINK = (
    f"https://t.me/proxy?server={MTPROTO_SERVER}&port={MTPROTO_PORT}"
    f"&secret={MTPROTO_SECRET}"
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
    buf.name = "tiinservice_telegram_proxy.html"
    return buf


def make_main_keyboard(tg_id: int | None = None) -> InlineKeyboardMarkup:
    """Клавиатура главного меню."""
    rows = [
        [
            InlineKeyboardButton("🔑 Мои конфиги", callback_data="my_configs"),
            InlineKeyboardButton("🎁 Активировать тест", callback_data="activate_test"),  # новая кнопка
        ],
        [
            InlineKeyboardButton("💎 Тарифы", callback_data="tariffs"),
            InlineKeyboardButton("✉️ Поддержка", callback_data="feedback"),
        ],
        [
            InlineKeyboardButton("📢 Наш канал", url="https://t.me/tiin_service"),
            InlineKeyboardButton("🔗 Прокси TG", callback_data="proxy_file"),
        ],
    ]
    if tg_id is not None:
        ref_url = f"https://t.me/{BOT_USERNAME}?start={tg_id}"
        share_text = (
            f"⚡️ тииҥ VPN — быстрый и стабильный VPN.\n"
            f"Переходи по ссылке и получи +{REFERRAL_REWARD_DAYS} дня подписки в подарок 🎁\n"
            f"{ref_url}"
        )
    return InlineKeyboardMarkup(rows)

# MAIN_MENU_TEXT = (
#     "⚡️ <b> тииҥ VPN 🐿</b>\n\n"
#     "Твой тестовый период закончился 🙂\n"
#     "Чтобы продолжить пользоваться VPN — выбери подходящий тариф 👇\n"
# )

# MAIN_MENU_TEXT = (
#     "⚡️ <b> тииҥ VPN 🐿</b>\n\n"
#     "🚀 <b>Добро пожаловать!</b>\n\n"
#     "Тестовый период на 3 дня уже активирован и готов к использованию!\n\n"
#     "📱 <b>Быстрый старт:</b>\n"
#     "1. Нажми «🔑 Мои конфиги»\n"
#     "2. Скопируй VLESS-ссылку\n"
#     "3. Вставь в любое VPN-приложение\n\n"
#     "✨ <b>Совет:</b> Добавь ключ в закладки — он вернется, если купишь тариф после теста.\n\n"
#     "💎 Выбирай тариф, чтобы оставаться на связи:"
# )

MAIN_MENU_TEXT = (
    "⚡️ <b> тииҥ VPN 🐿</b>\n\n"
    "Добро пожаловать!\n\n"
    "🎁 <b>Попробуй VPN бесплатно</b>\n"
    "У тебя есть возможность активировать тестовый период.\n\n"
    "👇 Нажми на кнопку ниже, чтобы начать"
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
    if not text or not text.strip():
        logger.warning("safe_edit_text: empty text provided, skipping.")
        return False

    message = query.message
    if message is None:
        try:
            await query.callback_query.message.reply_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
            return True
        except Exception as e:
            logger.warning(f"safe_edit_text no message fallback failed: {e}")
            return False

    if message.photo or message.video or message.document or message.sticker or message.animation or message.poll or message.voice or message.video_note or message.audio or message.location or message.venue or message.contact or message.dice or message.game:
        try:
            await message.delete()
            await message.chat.send_message(text, reply_markup=reply_markup, parse_mode=parse_mode)
            return True
        except Exception as e:
            logger.warning(f"safe_edit_text media fallback failed: {e}")
            return False

    try:
        await query.edit_message_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
        return True
    except Exception as e:
        err_str = str(e).lower()
        if "there is no text in the message to edit" in err_str:
            try:
                await message.delete()
                await message.chat.send_message(text, reply_markup=reply_markup, parse_mode=parse_mode)
                return True
            except Exception as e2:
                logger.warning(f"safe_edit_text no-text fallback failed: {e2}")
                return False
        logger.warning(f"safe_edit_text edit failed: {e}")
        return False


async def safe_edit_text_logged(query, text: str, scenario: str, reply_markup=None,
                                 parse_mode: str = "HTML") -> bool:
    """safe_edit_text + log to message_log."""
    result = await safe_edit_text(query, text, reply_markup=reply_markup, parse_mode=parse_mode)
    if result:
        tg_id = _get_tg_id(query)
        if tg_id:
            await _log_message(tg_id, "bot_menu", scenario, text)
    return result


async def reply_text_logged(chat, text: str, scenario: str, reply_markup=None,
                             parse_mode: str = "HTML", **kwargs):
    """chat.send_message + log to message_log."""
    result = await chat.send_message(text, reply_markup=reply_markup, parse_mode=parse_mode, **kwargs)
    tg_id = chat.id if hasattr(chat, 'id') else None
    if tg_id:
        await _log_message(tg_id, "bot_menu", scenario, text)
    return result


async def reply_photo_logged(chat, photo, scenario: str, caption: str = None,
                              reply_markup=None, parse_mode: str = "HTML", **kwargs):
    """chat.send_photo + log to message_log."""
    result = await chat.send_photo(photo, caption=caption, reply_markup=reply_markup,
                                   parse_mode=parse_mode, **kwargs)
    tg_id = chat.id if hasattr(chat, 'id') else None
    if tg_id:
        log_text = caption or "sent photo"
        await _log_message(tg_id, "bot_menu", scenario, log_text)
    return result


async def reply_document_logged(chat, document, scenario: str, caption: str = None,
                                 reply_markup=None, parse_mode: str = "HTML", **kwargs):
    """chat.send_document + log to message_log."""
    result = await chat.send_document(document, caption=caption, reply_markup=reply_markup,
                                      parse_mode=parse_mode, **kwargs)
    tg_id = chat.id if hasattr(chat, 'id') else None
    if tg_id:
        log_text = f"sent document: {caption[:100]}" if caption else "sent document"
        await _log_message(tg_id, "bot_menu", scenario, log_text)
    return result