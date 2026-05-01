"""
Вспомогательные функции: форматирование, конвертация времени, общие утилиты.
"""
import io
import logging
import sqlite3 
import json
from datetime import datetime, timedelta
from urllib.parse import quote
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from config import MTPROTO_SERVER, MTPROTO_PORT, MTPROTO_SECRET, BOT_USERNAME, REFERRAL_REWARD_DAYS

logger = logging.getLogger(__name__)


def convert_to_local(dt: datetime, offset_hours: int = 9) -> str:
    """Конвертирует UTC datetime в локальное время."""
    if dt is None:
        return "∞"
    return (dt + timedelta(hours=offset_hours)).strftime("%d.%m.%Y")


PUBLIC_BASE_URL = "https://344988.snk.wtf"


def get_user_sub_url(tg_id: int) -> str:
    # """Прокси-URL подписки для пользователя: /sub/{web_token}.
    # Пустая строка, если web_token не выдан."""
    # from api.db import get_web_token
    # token = get_web_token(tg_id)
    # if not token:
    #     return ""
    # return f"{PUBLIC_BASE_URL}/sub/{token}"
    
    """Получает subId пользователя из 3x-ui"""
    
    db_path = "/etc/x-ui/x-ui.db"
    email = f"vless_{tg_id}"  # Формат email в вашей БД
    
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # Получаем settings для inbound с портом 7443
        cursor.execute("""
            SELECT settings 
            FROM inbounds 
            WHERE port = 7443
        """)
        
        result = cursor.fetchone()
        conn.close()
        
        if result:
            settings = json.loads(result[0])
            clients = settings.get('clients', [])
            
            # Ищем клиента с нужным email
            for client in clients:
                if client.get('email') == email:
                    sub_id = client.get('subId')
                    if sub_id:
                        return f"http://344988.snk.wtf:2096/sub/{sub_id}"
        
        return ""
        
    except Exception as e:
        print(f"Error: {e}")
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