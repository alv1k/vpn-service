#!/usr/bin/env python3
"""
Скрипт отправки уведомления о включении доступа к «белым спискам» (Госуслуги, банки)
для списка родственников и доверенных пользователей (>5 месяцев).
Исключена: Валентина (909509933).
"""
import os
import sys
import html
import asyncio
import logging

sys.path.insert(0, "/home/alvik/vpn-service")

from dotenv import load_dotenv
load_dotenv("/home/alvik/vpn-service/.env")

from bot_xui.messaging import send_message_by_tg_id
from api.db import get_db

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("send_whitelist_notice")

RECIPIENT_IDS = [
    88486656,     # Yana
    6335998601,   # Yakutia
    5254383247,   # Клавдия
    161526954,    # Melamoory
    667624374,     # Mikhail
    6648354839    # Айсен
]


def build_message(name: str | None) -> str:
    safe_name = html.escape(name.strip()) if name and name.strip() and name.strip() != "Пользователь" else None
    greeting = f"{safe_name}, привет! 🐿" if safe_name else "Привет! 🐿"
    
    return (
        f"{greeting}\n\n"
        f"Мы обновили настройки вашего подключения и включили для вас **бесшовный доступ к «белым спискам» и государственным сервисам РФ**.\n\n"
        f"**Что это значит:**\n"
        f"Теперь при включенном VPN у вас стабильно и без отключений открываются:\n"
        f"🏛 **Госуслуги** (gosuslugi.ru)\n"
        f"💳 **Онлайн-банки** (Сбербанк, ВТБ, Т-Банк)\n"
        f"📄 **Налоговая** (nalog.gov.ru) и другие гос. порталы.\n\n"
        f"💡 *Совет:* если мобильное приложение Госуслуг или банка просит отключить VPN из-за внутренней защиты приложения — просто откройте сайт через браузер (Safari / Chrome), через браузер всё работает без ограничений.\n\n"
        f"Спасибо, что вы с нами! Если возникнут вопросы — пишите в поддержку ❤️\n\n"
        f"— *Команда Tiin VPN*"
    )


async def send_notices():
    logger.info(f"Начинаем отправку для {len(RECIPIENT_IDS)} получателей...")
    
    with get_db() as db:
        c = db.cursor(dictionary=True)
        format_strings = ','.join(['%s'] * len(RECIPIENT_IDS))
        c.execute(f"SELECT tg_id, first_name FROM users WHERE tg_id IN ({format_strings})", tuple(RECIPIENT_IDS))
        users = {u["tg_id"]: u.get("first_name") for u in c.fetchall()}

    success = 0
    failed = 0

    for tg_id in RECIPIENT_IDS:
        name = users.get(tg_id)
        msg_text = build_message(name)
        
        logger.info(f"Отправка для {tg_id} ({name})...")
        sent = await send_message_by_tg_id(
            tg_id=tg_id,
            text=msg_text,
            parse_mode="Markdown",
            source="whitelist_notice",
            scenario="whitelist_announcement"
        )
        if sent:
            success += 1
            logger.info(f"✅ Успешно доставлено {tg_id} ({name})")
        else:
            failed += 1
            logger.warning(f"❌ Не удалось доставить {tg_id} ({name})")
        
        await asyncio.sleep(1.0)

    logger.info(f"🎉 Рассылка завершена: успешно {success}, ошибок {failed}")


if __name__ == "__main__":
    asyncio.run(send_notices())
