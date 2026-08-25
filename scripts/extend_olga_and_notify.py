#!/usr/bin/env python3
"""Ольга: +3 дня к подписке + уведомление с Happ deep link."""
import asyncio
import logging
import sys
from datetime import datetime, timezone

sys.path.insert(0, "/home/alvik/vpn-service")

from dotenv import load_dotenv
load_dotenv("/home/alvik/vpn-service/.env")

from bot_xui.utils import XUIClient
from bot_xui.messaging import send_link_safely
from api.db import get_web_token, log_message_sent
from config import XUI_HOST, XUI_USERNAME, XUI_PASSWORD, ADMIN_TG_ID

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

TG_ID = 2047816123

MESSAGE = (
    "🔍 <b>Мы изучили ваш случай</b>\n\n"
    "Вы не могли подключиться к TIIN через приложение Happ. "
    "Мы нашли причину — конфликт параметров шифрования — и устранили её.\n\n"
    "✅ Вследствие данного недоразумения дарим вам продление подписки на <b>3 дня</b>.\n\n"
    "Теперь откройте Happ и <b>обновите подписку</b>:\n"
    "• Нажмите на свою подписку\n"
    "• Выберите «Обновить» / «Refresh»\n"
    "• После обновления — подключитесь\n\n"
    "Если вдруг снова не заработает — попробуйте другие приложения:\n"
    "• <b>Shadowrocket</b>\n"
    "• <b>Karing</b>\n"
    "(доступны в App Store)"
)


async def main():
    xui = XUIClient(XUI_HOST, XUI_USERNAME, XUI_PASSWORD)

    existing = xui.get_client_by_tg_id(TG_ID)
    if not existing:
        logger.error(f"Client with tg_id={TG_ID} not found in x-ui")
        return

    expiry_ms = existing['client'].get('expiryTime', 0)
    expiry_dt = datetime.fromtimestamp(expiry_ms / 1000, tz=timezone.utc)
    logger.info(f"Current x-ui expiry for tg_id={TG_ID}: {expiry_dt}")

    sub_url = xui.get_client_subscription_url(TG_ID)
    if not sub_url:
        logger.error(f"Could not get sub URL for tg_id={TG_ID}")
        return

    web_token = get_web_token(TG_ID)
    instructions_url = f"https://344988.snk.wtf/my/{web_token}"

    text_with_link = MESSAGE

    buttons = [
        [{"text": "📖 Инструкция", "url": instructions_url}],
        [
            {"text": "🚀 Shadowrocket", "url": "https://apps.apple.com/app/shadowrocket/id932747118"},
            {"text": "🔵 Karing", "url": "https://apps.apple.com/app/karing/id6472431552"},
        ],
    ]

    admin_ok = await send_link_safely(
        tg_id=ADMIN_TG_ID,
        text="📋 <b>Предпросмотр сообщения для Ольги:</b>\n\n" + text_with_link,
        buttons=buttons,
        parse_mode="HTML",
        source="admin_send",
        scenario="olga_3day_extend_preview",
    )

    if not admin_ok:
        logger.error("❌ Не удалось отправить предпросмотр админу")
        return

    logger.info("✅ Предпросмотр отправлен админу. Жду подтверждения...")

    print("\n" + "=" * 50)
    print("ПРЕДПРОСМОТР ОТПРАВЛЕН АДМИНУ В TELEGRAM")
    print("=" * 50)
    print("Введи 'yes' чтобы отправить Ольге, или 'no' чтобы отменить:")

    confirm = input("> ").strip().lower()
    if confirm != "yes":
        logger.info("Отправка отменена")
        return

    ok = await send_link_safely(
        tg_id=TG_ID,
        text=text_with_link,
        buttons=buttons,
        parse_mode="HTML",
        source="admin_send",
        scenario="olga_3day_extend",
    )

    if ok:
        logger.info(f"✅ Message sent to {TG_ID}")
    else:
        logger.error(f"❌ Failed to send message to {TG_ID}")


if __name__ == "__main__":
    asyncio.run(main())
