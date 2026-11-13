#!/usr/bin/env python3
"""Отправляет пользователю предложение о возврате с кнопками Да/Нет."""
import argparse
import asyncio
import logging
import sys

sys.path.insert(0, "/home/alvik/vpn-service")

from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("offer_refund")

from bot_xui.messaging import send_link_safely


async def main():
    parser = argparse.ArgumentParser(description="Отправить предложение о возврате")
    parser.add_argument("--tg_id", type=int, required=True, help="Telegram ID пользователя")
    args = parser.parse_args()

    tg_id = args.tg_id

    text = (
        "Здравствуйте!\n\n"
        "Вы оплатили подписку, но мы не видим активности с вашей стороны.\n"
        "Хотите оформить возврат средств?"
    )

    buttons = [
        [
            {"text": "✅ Да, верните деньги", "callback_data": f"refund_confirm_{tg_id}"},
            {"text": "❌ Нет, всё работает", "callback_data": f"refund_decline_{tg_id}"},
        ],
    ]

    ok = await send_link_safely(
        tg_id=tg_id,
        text=text,
        buttons=buttons,
        parse_mode="HTML",
        source="admin_send",
        scenario="refund_offer",
    )

    if ok:
        logger.info(f"✅ Refund offer sent to {tg_id}")
    else:
        logger.error(f"❌ Failed to send refund offer to {tg_id}")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
