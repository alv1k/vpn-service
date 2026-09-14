#!/usr/bin/env python3
"""Отправить уведомление пользователю 1002481019 по поводу диспута/отзыва платежа."""
import asyncio
import logging
import sys

sys.path.insert(0, "/home/alvik/vpn-service")

from dotenv import load_dotenv
load_dotenv("/home/alvik/vpn-service/.env")

from bot_xui.messaging import send_link_safely

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

TG_ID = 1002481019

MESSAGE = (
    "Здравствуйте!\n\n"
    "Мы получили уведомление от платёжной системы об отзыве платежа (диспут через банк) "
    "за автопродление подписки от 21.08.2026 на сумму 499 ₽ (платёж № <code>321a7bb0-000f-5001-9000-167a74602c5b</code>).\n\n"
    "При этом наша система зафиксировала регулярное использование VPN-сервиса после списания.\n\n"
    "Мы создали функцию автопродления исключительно для удобства — чтобы вы не остались без связи "
    "в самый неподходящий момент, когда заканчивается срок подписки. Если у вас есть предложения, "
    "как сделать этот процесс более удобным и прозрачным (например, добавить больше способов или напоминаний "
    "для управления и остановки автоплатежа), мы будем очень благодарны за вашу обратную связь!\n\n"
    "Если произошла ошибка со стороны банка или вы хотите сохранить доступ к VPN, пожалуйста, "
    "выберите удобный тариф ниже и оплатите подписку повторно. В противном случае через 5 часов "
    "мы будем вынуждены приостановить работу ключей и отключить доступ к сервису.\n\n"
    "Если у вас возникли вопросы или вы хотите поделиться мнением, просто напишите нам в ответ или в поддержку.\n\n"
    "С наилучшими пожеланиями,\n"
    "команда <b>TIIN service</b> 🐿"
)

BUTTONS = [
    [{"text": "💳 Месяц — 199 ₽", "callback_data": "buy_tariff_monthly_30d"}],
    [{"text": "📦 Стандарт (3 мес) — 499 ₽", "callback_data": "buy_tariff_standard_3m"}],
    [{"text": "💬 Поддержка", "url": "https://t.me/tiin_support"}],
]


async def main():
    logger.info(f"Отправка сообщения для TG_ID: {TG_ID}")
    ok = await send_link_safely(
        tg_id=TG_ID,
        text=MESSAGE,
        buttons=BUTTONS,
        parse_mode="HTML",
        source="admin_send",
        scenario="dispute_notice",
    )
    if ok:
        logger.info(f"✅ Сообщение успешно отправлено пользователю {TG_ID}")
    else:
        logger.error(f"❌ Ошибка отправки пользователю {TG_ID}")


if __name__ == "__main__":
    asyncio.run(main())
