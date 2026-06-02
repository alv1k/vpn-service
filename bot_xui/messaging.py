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

_bot = Bot(token=TELEGRAM_BOT_TOKEN)


async def send_message_by_tg_id(
    tg_id: int,
    text: str,
    parse_mode: Optional[str] = None,
    reply_markup: Optional[InlineKeyboardMarkup] = None,
    bot: Optional[Bot] = None,
    source: str = "bot_command",
    scenario: str = None,
) -> bool:
    """Отправка сообщения пользователю по tg_id через python-telegram-bot."""
    try:
        await (bot or _bot).send_message(
            chat_id=tg_id,
            text=text,
            parse_mode=parse_mode,
            reply_markup=reply_markup,
        )
        try:
            from api.db import execute_query, log_message_sent
            execute_query("UPDATE users SET bot_blocked = 0 WHERE tg_id = %s AND bot_blocked = 1", (tg_id,))
            log_message_sent(tg_id=tg_id, source=source, scenario=scenario, message_text=text, status='sent')
        except Exception:
            pass
        return True
    except Exception as e:
        err = str(e).lower()
        status = 'blocked' if ("blocked" in err or "deactivated" in err) else 'failed'
        if status == 'blocked':
            try:
                from api.db import execute_query, log_message_sent
                execute_query("UPDATE users SET bot_blocked = 1 WHERE tg_id = %s", (tg_id,))
                log_message_sent(tg_id=tg_id, source=source, scenario=scenario, message_text=text, status='blocked', error_text=str(e)[:255])
                logger.info(f"[send_message] Пользователь {tg_id} заблокировал бота — помечен в БД")
            except Exception as db_err:
                logger.error(f"[send_message] Не удалось пометить {tg_id}: {db_err}")
        else:
            try:
                from api.db import log_message_sent
                log_message_sent(tg_id=tg_id, source=source, scenario=scenario, message_text=text, status='failed', error_text=str(e)[:255])
            except Exception:
                pass
        logger.error(f"[send_message] Ошибка отправки для {tg_id}: {e}")
        return False


async def send_link_safely(
    tg_id: int,
    text: str,
    buttons: Optional[List[List[Dict[str, str]]]] = None,
    parse_mode: Optional[str] = None,
    source: str = "webhook",
    scenario: str = None,
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
            try:
                from api.db import execute_query, log_message_sent
                execute_query("UPDATE users SET bot_blocked = 0 WHERE tg_id = %s AND bot_blocked = 1", (tg_id,))
                log_message_sent(tg_id=tg_id, source=source, scenario=scenario, message_text=text, status='sent')
            except Exception:
                pass
            return True

        error_text = response.text[:255]
        is_block = False
        try:
            resp_json = response.json()
            desc = resp_json.get("description", "").lower()
            if "blocked" in desc or "deactivated" in desc:
                is_block = True
        except Exception:
            pass

        if is_block:
            logger.info(f"🚫 User {tg_id} blocked bot — marking in DB")
            try:
                from api.db import execute_query, log_message_sent
                execute_query("UPDATE users SET bot_blocked = 1 WHERE tg_id = %s", (tg_id,))
                log_message_sent(tg_id=tg_id, source=source, scenario=scenario, message_text=text, status='blocked', error_text=error_text)
            except Exception:
                pass
            return False

        logger.warning(f"⚠️ sendMessage failed: {response.text}")
        try:
            from api.db import log_message_sent
            log_message_sent(tg_id=tg_id, source=source, scenario=scenario, message_text=text, status='failed', error_text=error_text)
        except Exception:
            pass
        return False

    except Exception as e:
        logger.error(f"❌ send_link_safely error: {e}")
        try:
            from api.db import log_message_sent
            log_message_sent(tg_id=tg_id, source=source, scenario=scenario, message_text=text, status='failed', error_text=str(e)[:255])
        except Exception:
            pass
        return False
