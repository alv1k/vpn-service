#!/usr/bin/env python3
"""
Скрипт отправки уведомления об отмене белых списков.
Целевые получатели (5 аккаунтов, Валентина исключена):
- 667624374
- 88486656 (Yana Seeva)
- 6335998601 (Yakutia)
- 6648354839 (Айсен)
- 5254383247 (Клавдия Алексеева)

Запуск:
    python3 scripts/send_whitelist_cancellation_notice.py --dry-run
    python3 scripts/send_whitelist_cancellation_notice.py --send
"""
import os
import sys
import argparse
import asyncio
import logging

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import TELEGRAM_BOT_TOKEN

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# Список получателей (Валентина 909509933 исключена)
TARGET_TG_IDS = [
    667624374,   # User #11
    88486656,    # Yana Seeva (#12)
    6335998601,  # Yakutia (#17)
    6648354839,  # Айсен (#55)
    5254383247,  # Клавдия Алексеева (#90)
]

MESSAGE_TEXT = (
    "⚠️ <b>Важное обновление по маршрутизации</b>\n\n"
    "Ранее мы тестировали отдельный механизм обхода ограничений (белых списков) для ряда сервисов.\n\n"
    "По результатам тестов мы пришли к выводу, что <b>данная схема небезопасна и несет риски для стабильности и защиты данных</b>. "
    "В связи с этим мы полностью закрыли этот эксперимент и отменили действие специальных списков.\n\n"
    "Весь трафик к российским ресурсам (Госуслуги, банки, маркетплейсы) переведен обратно на штатный защищенный канал прямого доступа. "
    "Приложения работают в обычном режиме, с вашей стороны никаких настроек менять не нужно."
)


async def send_notifications(dry_run: bool = True):
    if not TELEGRAM_BOT_TOKEN:
        logger.error("❌ TELEGRAM_BOT_TOKEN не задан в конфигурации!")
        return

    from telegram import Bot
    from telegram.constants import ParseMode

    bot = Bot(token=TELEGRAM_BOT_TOKEN)

    logger.info(f"🚀 Запуск рассылки уведомлений (dry_run={dry_run}) для {len(TARGET_TG_IDS)} получателей...")
    
    success_count = 0
    fail_count = 0

    for tg_id in TARGET_TG_IDS:
        if dry_run:
            logger.info(f"[DRY-RUN] Сообщение будет отправлено TG ID: {tg_id}")
            success_count += 1
            continue

        try:
            await bot.send_message(
                chat_id=tg_id,
                text=MESSAGE_TEXT,
                parse_mode=ParseMode.HTML
            )
            logger.info(f"✅ Успешно отправлено TG ID: {tg_id}")
            success_count += 1
            await asyncio.sleep(0.5)  # Небольшая пауза между отправками
        except Exception as e:
            logger.error(f"❌ Ошибка отправки TG ID {tg_id}: {e}")
            fail_count += 1

    logger.info(f"🎉 Рассылка завершена. Успешно: {success_count}, Ошибок: {fail_count}")


def main():
    parser = argparse.ArgumentParser(description="Отправка уведомления об отмене белых списков")
    parser.add_argument("--send", action="store_true", help="Реальная отправка сообщений")
    parser.add_argument("--dry-run", action="store_true", help="Тестовый прогон без отправки")
    args = parser.parse_args()

    dry_run = not args.send
    asyncio.run(send_notifications(dry_run=dry_run))


if __name__ == "__main__":
    main()
