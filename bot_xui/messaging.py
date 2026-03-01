"""
Низкоуровневая отправка сообщений: прямые вызовы Bot API / httpx.
"""
import json
import logging
import httpx
from typing import Optional, List, Dict

from telegram import Bot, InlineKeyboardMarkup
from config import TELEGRAM_BOT_TOKEN

logger = logging.getLogger(__name__)


async def send_message_by_tg_id(
    tg_id: int,
    text: str,
    parse_mode: Optional[str] = None,
    reply_markup: Optional[InlineKeyboardMarkup] = None,
) -> bool:
    """Отправка сообщения пользователю по tg_id через python-telegram-bot."""
    bot = Bot(token=TELEGRAM_BOT_TOKEN)
    try:
        await bot.send_message(
            chat_id=tg_id,
            text=text,
            parse_mode=parse_mode,
            reply_markup=reply_markup,
        )
        return True
    except Exception as e:
        logger.error(f"[send_message] Ошибка отправки для {tg_id}: {e}")
        return False


async def send_link_safely(
    tg_id: int,
    text: str,
    buttons: Optional[List[List[Dict[str, str]]]] = None,
    parse_mode: Optional[str] = None,
) -> bool:
    """
    Отправка через сырой HTTP (httpx) — используется из вебхука/воркера,
    где нет экземпляра Application.

    buttons: [[{"text": "Кнопка", "callback_data": "data"}], ...]
    """
    try:
        data: Dict = {"chat_id": tg_id, "text": text}
        if parse_mode:
            data["parse_mode"] = parse_mode
        if buttons:
            data["reply_markup"] = json.dumps({"inline_keyboard": buttons})

        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(
                f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                data=data,
            )

        if response.status_code == 200:
            logger.info(f"✅ Message sent to {tg_id}")
            return True

        logger.warning(f"⚠️ sendMessage failed: {response.text}")
        return False

    except Exception as e:
        logger.error(f"❌ send_link_safely error: {e}")
        return False
