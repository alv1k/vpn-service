#!/usr/bin/env python3
"""Отправить уведомление пользователю 6864368530 о восстановлении флоу оплаты."""
import asyncio
import logging
import sys

sys.path.insert(0, "/home/alvik/vpn-service")

from dotenv import load_dotenv
load_dotenv("/home/alvik/vpn-service/.env")

from bot_xui.messaging import send_link_safely
from api.db import get_web_token, log_message_sent

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

TG_ID = 6864368530

MESSAGE = (
    "✅ <b>Платёж прошёл успешно!</b>\n\n"
    "К сожалению, произошёл сбой в автоматической обработке платежа, "
    "но сейчас всё в порядке — мы восстановили весь флоу.\n\n"
    "🔑 Вам выдана новая ссылка подписки, конфиг AWG работает в штатном режиме.\n\n"
    "Если возникнут вопросы — обращайтесь 👇"
)

BUTTONS = [
    [{"text": "📖 Инструкция", "url": None}],  # url будет заменён после получения токена
    [{"text": "💬 Поддержка", "callback_data": "feedback"}],
]


async def main():
    web_token = get_web_token(TG_ID)
    logger.info(f"web_token for {TG_ID}: {web_token}")

    buttons = [
        [{"text": "📖 Инструкция", "url": f"https://344988.snk.wtf/my/{web_token}"}],
        [{"text": "💬 Поддержка", "callback_data": "feedback"}],
    ]

    text = MESSAGE
    ok = await send_link_safely(
        tg_id=TG_ID,
        text=text,
        buttons=buttons,
        parse_mode="HTML",
        source="admin_send",
        scenario="payment_issue_resolved",
    )

    if ok:
        logger.info(f"✅ Сообщение отправлено {TG_ID}")
    else:
        logger.error(f"❌ Не удалось отправить сообщение {TG_ID}")


if __name__ == "__main__":
    asyncio.run(main())
