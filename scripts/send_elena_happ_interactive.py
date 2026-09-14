#!/usr/bin/env python3
"""
Скрипт отправки интерактивного сообщения пользователю Елена (281603923)
с кнопками подтверждения работы и запроса помощи.
"""
import sys
import os
import asyncio
import logging

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv("/home/alvik/vpn-service/.env")

from bot_xui.messaging import send_link_safely

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

TG_ID = 281603923
WEB_TOKEN = "B7iBik8zxPEXwyvrLWKJJg"

MESSAGE_TEXT = (
    "Елена, здравствуйте! 🐿\n\n"
    "Мы видим, что у вас оформлена годовая подписка и приложение <b>Happ</b> уже установлено на телефоне, "
    "но само подключение ещё не запущено.\n\n"
    "<b>Чтобы включить VPN:</b>\n"
    "1. Откройте приложение <b>Happ</b> на телефоне.\n"
    "2. Нажмите круглую кнопку подключения в центре экрана.\n"
    "3. Если появится системный запрос Android — нажмите <b>«ОК / Разрешить»</b>.\n\n"
    "Получилось ли запустить интернет через VPN?"
)

BUTTONS = [
    [
        {"text": "🟢 Всё работает", "callback_data": "happ_check_ok"},
        {"text": "🔴 Нужна помощь", "callback_data": "happ_check_help"},
    ],
    [
        {"text": "📖 Открыть инструкцию", "url": f"https://344988.snk.wtf/my/{WEB_TOKEN}"}
    ]
]

async def main():
    logger.info(f"Отправка интерактивного сообщения для TG ID {TG_ID}...")
    success = await send_link_safely(
        tg_id=TG_ID,
        text=MESSAGE_TEXT,
        buttons=BUTTONS,
        parse_mode="HTML",
        source="admin_send",
        scenario="happ_interactive_check"
    )
    if success:
        logger.info(f"✅ Сообщение успешно доставлено пользователю {TG_ID}!")
    else:
        logger.error(f"❌ Не удалось отправить сообщение пользователю {TG_ID}")

if __name__ == "__main__":
    asyncio.run(main())
