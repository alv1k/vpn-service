#!/usr/bin/env python3
"""Отправить ответ поддержки пользователю Colin (TG: 401499373) лично в Telegram."""
import asyncio
import logging
import sys

sys.path.insert(0, "/home/alvik/vpn-service")

from dotenv import load_dotenv
load_dotenv("/home/alvik/vpn-service/.env")

from bot_xui.messaging import send_link_safely

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

TG_ID = 401499373

MESSAGE = (
    "Здравствуйте! На каком устройстве перестал работать VPN? "
    "На данный момент видим одно активное устройство.\n\n"
    "Попробуйте импортировать ссылку снова:\n"
    "https://ru-server.tiinservice.online/sub/b7DpKyr9JKV_O2xZwGtUkA\n\n"
    "Рекомендуемое приложение Happ — зеркала для скачивания на свои устройства "
    "есть по этой ссылке, так же там есть резервный канал связи под Telegram 🙌🏼"
)


async def main():
    ok = await send_link_safely(
        tg_id=TG_ID,
        text=MESSAGE,
        parse_mode=None,
        source="admin_send",
        scenario="support_reply_colin",
    )
    if ok:
        logger.info(f"✅ Сообщение отправлено {TG_ID}")
    else:
        logger.error(f"❌ Не удалось отправить сообщение {TG_ID}")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
