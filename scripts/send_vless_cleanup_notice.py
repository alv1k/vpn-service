#!/usr/bin/env python3
"""Отправить уведомление пользователю 6864368530 о чистке старого конфига."""
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

TG_ID = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 6864368530
WEB_TOKEN = sys.argv[2] if len(sys.argv) > 2 else os.getenv("TARGET_WEB_TOKEN", "REDACTED_WEB_TOKEN")

MESSAGE = (
    "Добрый день, Александр! Мы продолжили изучать ситуацию по вашей заявке, "
    "провели глубокий анализ и готовы предложить подключиться через Личный кабинет:\n\n"
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
    text = MESSAGE
    ok = await send_link_safely(
        tg_id=TG_ID,
        text=text,
        buttons=BUTTONS,
        parse_mode="HTML",
        source="admin_send",
        scenario="vless_cleanup_notice",
    )

    if ok:
        logger.info(f"✅ Сообщение отправлено {TG_ID}")
    else:
        logger.error(f"❌ Не удалось отправить сообщение {TG_ID}")


if __name__ == "__main__":
    asyncio.run(main())