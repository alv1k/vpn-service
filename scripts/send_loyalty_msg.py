#!/usr/bin/env python3
"""
Отправляет loyalty-сообщение админу для теста.
"""
import sys
import os
import asyncio

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from telegram import Bot, InlineKeyboardMarkup, InlineKeyboardButton
from config import TELEGRAM_BOT_TOKEN, ADMIN_TG_ID

TEXT = (
    "Привет! 👋\n\n"
    "Мы обновили конфигурацию инбаундов — теперь используем <b>ML-DSA-65 (Dilithium3)</b> 🔐 "
    "постквантовую криптографию, стандартизированную NIST 🛡️⚡\n\n"
    "Раньше бывали подтормаживания и сбои — теперь всё стабильно и летает 🚀💨\n\n"
    "И приятный бонус — <b>+7 дней к твоей подписке</b> 🎁🥳 "
    "Просто так, за то что ты с нами! 💛\n\n"
    "Если есть вопросы — пиши, всегда на связи 😊"
)

async def main():
    bot = Bot(token=TELEGRAM_BOT_TOKEN)
    await bot.send_message(
        chat_id=ADMIN_TG_ID,
        text=TEXT,
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("✉️ Поддержка", callback_data="feedback")],
        ]),
    )
    print(f"✅ Sent to admin {ADMIN_TG_ID}")

if __name__ == "__main__":
    asyncio.run(main())
