#!/usr/bin/env python3
import asyncio
import logging
import sys

sys.path.insert(0, "/home/alvik/vpn-service")

from dotenv import load_dotenv
load_dotenv("/home/alvik/vpn-service/.env")

from bot_xui.messaging import send_link_safely

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

TG_ID = 823162260
WEB_TOKEN = "b_1ALbkM-ptxAXPWpnreKA"

MESSAGE = (
    "Добрый день, Светлана! Мы провели анализ и готовы предложить "
    "подключиться через Личный кабинет:\n\n"
    f"https://344988.snk.wtf/my/{WEB_TOKEN}\n\n"
    "Портал поддерживает iOS устройства — просто откройте ссылку в приложении "
    "(Shadowrocket, hiddify, karing и др.) и подписка загрузится автоматически.\n\n"
    "Пожалуйста, попробуйте подключиться и дайте знать, всё ли работает корректно."
)

BUTTONS = [
    [{"text": "🪄 Личный кабинет", "url": f"https://344988.snk.wtf/my/{WEB_TOKEN}"}],
    [{"text": "💬 Поддержка", "callback_data": "feedback"}],
]


async def main():
    ok = await send_link_safely(
        tg_id=TG_ID,
        text=MESSAGE,
        buttons=BUTTONS,
        parse_mode="HTML",
        source="admin_send",
        scenario="portal_ios_notice",
    )

    if ok:
        logger.info(f"✅ Сообщение отправлено {TG_ID}")
    else:
        logger.error(f"❌ Не удалось отправить сообщение {TG_ID}")


if __name__ == "__main__":
    asyncio.run(main())