#!/usr/bin/env python3
import asyncio
import logging
import sys

sys.path.insert(0, "/home/alvik/vpn-service")

from dotenv import load_dotenv
load_dotenv("/home/alvik/vpn-service/.env")

from bot_xui.messaging import send_link_safely
from api.db import get_web_token

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

TG_ID = 115744584

MESSAGE = (
    "Здравствуйте, Дмитрий!\n\n"
    "Заметили, что на Windows при попытке подключиться через кнопку в Happ подписка могла не добавиться автоматически (в клиенте Happ для Windows иногда сбоит обработка браузерных ссылок).\n\n"
    "Чтобы всё подключить вручную за пару кликов:\n\n"
    "1. Скопируйте ссылку подписки:\n"
    "<code>https://344988.snk.wtf/sub/FoFoL9y-o_QvbwH8LI13bg</code>\n\n"
    "2. Откройте <b>Happ</b> на компьютере.\n"
    "3. Вверху нажмите на значок <b>« + »</b> ➔ <b>«Добавить из буфера»</b> (или вставьте скопированную ссылку).\n"
    "4. Нажмите большую кнопку подключения по центру.\n\n"
    "Либо откройте <a href=\"https://344988.snk.wtf/go-connect/FoFoL9y-o_QvbwH8LI13bg\">страницу быстрого подключения</a> — там есть все варианты импорта и копирования в один клик."
)

BUTTONS = [
    [
        {"text": "🟢 Получилось", "callback_data": "setup_win_ok"},
        {"text": "🔴 Не получилось", "callback_data": "setup_win_fail"},
    ]
]

async def main():
    web_token = get_web_token(TG_ID)
    logger.info(f"web_token for {TG_ID}: {web_token}")

    ok = await send_link_safely(
        tg_id=TG_ID,
        text=MESSAGE,
        buttons=BUTTONS,
        parse_mode="HTML",
        source="admin_send",
        scenario="happ_win_setup_assist",
    )

    if ok:
        logger.info(f"✅ Сообщение успешно отправлено пользователю {TG_ID}")
    else:
        logger.error(f"❌ Не удалось отправить сообщение {TG_ID}")

if __name__ == "__main__":
    asyncio.run(main())
