#!/usr/bin/env python3
"""
Проверка статуса оплаты пользователем 1002481019 через 5 часов после предупреждения о диспуте.
Если оплатил -> отправляет сообщение с благодарностью.
Если не оплатил -> отзывает ключ в 3x-ui, сбрасывает подписку в БД и отправляет уведомление об отключении.
"""
import asyncio
import logging
import sys
from datetime import datetime, timezone

sys.path.insert(0, "/home/alvik/vpn-service")

from dotenv import load_dotenv
load_dotenv("/home/alvik/vpn-service/.env")

from bot_xui.messaging import send_message_by_tg_id
from api.db import execute_query, get_user_by_tg_id, log_message_sent
from api.webhook import deactivate_xui_client

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

TG_ID = 1002481019
NOTICE_TIMESTAMP = "2026-09-01 02:48:00"  # Время отправки первого уведомления

SUCCESS_MESSAGE = (
    "✅ <b>Спасибо! Оплата успешно получена.</b>\n\n"
    "Рады, что мы поняли друг друга! Мы очень ценим ваше доверие и обратную связь. "
    "Наш сервис постоянно развивается и меняется к лучшему, чтобы быть максимально удобным и прозрачным для вас.\n\n"
    "Ваша подписка активна, приятного пользования! 🐿"
)

DEACTIVATE_MESSAGE = (
    "Здравствуйте.\n\n"
    "Время ожидания истекло, повторная оплата подписки не поступила. "
    "В связи с этим мы приостановили работу вашего VPN-ключа и отключили доступ к сервису.\n\n"
    "Если вы захотите вернуться и возобновить пользование, вы всегда можете оформить новую подписку в меню бота: /start\n\n"
    "Всего доброго!\n"
    "команда <b>TIIN service</b> 🐿"
)


def has_paid_since_notice(tg_id: int) -> bool:
    """Проверяет, был ли успешный платёж после времени отправки предупреждения."""
    query = """
        SELECT id, payment_id, amount, tariff, created_at 
        FROM payments 
        WHERE tg_id = %s AND status = 'paid' AND created_at >= %s
    """
    rows = execute_query(query, (tg_id, NOTICE_TIMESTAMP), fetch='all')
    return bool(rows)


async def main():
    logger.info(f"Запуск итоговой проверки для TG_ID: {TG_ID}")
    
    paid = has_paid_since_notice(TG_ID)
    if paid:
        logger.info(f"Пользователь {TG_ID} успешно оплатил подписку.")
        await send_message_by_tg_id(
            tg_id=TG_ID,
            text=SUCCESS_MESSAGE,
            parse_mode="HTML",
            source="admin_send",
            scenario="dispute_resolved_paid",
        )
        logger.info(f"✅ Отправлено благодарственное сообщение пользователю {TG_ID}")
        return

    logger.info(f"Пользователь {TG_ID} НЕ оплатил подписку. Начинаем деактивацию...")
    
    # 1. Деактивируем ключ в 3x-ui
    client_name = f"tiin_{TG_ID}"
    deact_res = deactivate_xui_client(client_name)
    logger.info(f"Результат деактивации XUI ({client_name}): {deact_res}")
    
    # 2. Сбрасываем срок подписки в БД
    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    execute_query("UPDATE users SET subscription_until = %s, autopay_enabled = 0 WHERE tg_id = %s", (now_utc, TG_ID))
    logger.info(f"Подписка пользователя {TG_ID} в БД установлена на {now_utc}")
    
    # 3. Отправляем уведомление об отключении
    await send_message_by_tg_id(
        tg_id=TG_ID,
        text=DEACTIVATE_MESSAGE,
        parse_mode="HTML",
        source="admin_send",
        scenario="dispute_service_disabled",
    )
    logger.info(f"🛑 Отправлено уведомление об отключении пользователю {TG_ID}")


if __name__ == "__main__":
    asyncio.run(main())
