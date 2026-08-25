#!/usr/bin/env python3
"""Напоминание пользователям, активировавшим тест, но не зашедшим в кабинет."""
import asyncio
import logging
import sys
from datetime import datetime, timedelta

sys.path.insert(0, "/home/alvik/vpn-service")

from dotenv import load_dotenv
load_dotenv("/home/alvik/vpn-service/.env")

from api.db import execute_query
from bot_xui.messaging import send_link_safely

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

REMINDER_TEXT = (
    "👋 <b>Привет! Ты активировал тестовый период, но ещё не заглянул в кабинет.</b>\n\n"
    "🌍 В кабинете ты найдёшь все данные для подключения:\n"
    "— ссылки для приложений\n"
    "— QR-код\n"
    "— инструкцию по настройке\n\n"
    "📎 <b>Открой личный кабинет и подключайся:</b>\n"
    "https://344988.snk.wtf/my/{web_token}\n\n"
    "⏱ Тест активен до <b>{until}</b>\n"
    "💎 После окончания можно выбрать тариф и продолжить пользоваться."
)

REMINDER_INTERVAL = timedelta(hours=12)


def get_test_users():
    return execute_query(
        """SELECT id, tg_id, web_token, subscription_until, first_name
           FROM users
           WHERE (test_vless_activated = 1 OR test_awg_activated = 1)
             AND subscription_until > NOW()
             AND bot_blocked = 0
             AND web_token IS NOT NULL
             AND NOT EXISTS (
               SELECT 1 FROM vpn_keys
               WHERE tg_id = users.tg_id AND payment_id IS NOT NULL
               LIMIT 1
             )""",
        fetch="all",
    )


def has_webpage_events(web_token: str) -> bool:
    row = execute_query(
        "SELECT 1 FROM webpage_events WHERE web_token = %s LIMIT 1",
        (web_token,),
        fetch="one",
    )
    return row is not None


def last_reminder_sent(tg_id: int) -> datetime | None:
    row = execute_query(
        "SELECT created_at FROM message_log WHERE tg_id = %s AND scenario = 'test_reminder' ORDER BY created_at DESC LIMIT 1",
        (tg_id,),
        fetch="one",
    )
    return row["created_at"] if row else None


async def main():
    users = get_test_users()
    if not users:
        logger.info("Нет пользователей с активным тестом")
        return

    now = datetime.now()
    sent = 0

    for u in users:
        tg_id = u["tg_id"]
        web_token = u["web_token"]
        until = u["subscription_until"]
        name = u["first_name"] or str(tg_id)

        if has_webpage_events(web_token):
            logger.info(f"{name} ({tg_id}) — уже заходил в кабинет, пропускаем")
            continue

        last = last_reminder_sent(tg_id)
        if last and (now - last) < REMINDER_INTERVAL:
            logger.info(f"{name} ({tg_id}) — напоминание уже отправлялось недавно ({last}), пропускаем")
            continue

        until_str = until.strftime("%d.%m.%Y %H:%M") if until else "—"
        text = REMINDER_TEXT.format(web_token=web_token, until=until_str)

        buttons = [
            [{"text": "📖 Открыть кабинет", "url": f"https://344988.snk.wtf/my/{web_token}"}],
            [{"text": "💎 Тарифы", "callback_data": "tariffs"}],
        ]

        ok = await send_link_safely(
            tg_id=tg_id,
            text=text,
            buttons=buttons,
            parse_mode="HTML",
            source="admin_send",
            scenario="test_reminder",
        )
        if ok:
            sent += 1
            logger.info(f"✅ Напоминание отправлено {name} ({tg_id})")
        else:
            logger.error(f"❌ Ошибка отправки {name} ({tg_id})")

    logger.info(f"Готово. Отправлено напоминаний: {sent}")


if __name__ == "__main__":
    asyncio.run(main())
