import os
import sys
import mysql.connector
import asyncio
import signal
import json
from aiogram import Bot, Dispatcher, Router, F
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from aiogram.filters import CommandStart
from io import BytesIO
from config import TELEGRAM_BOT_TOKEN, XUI_HOST, XUI_USERNAME, XUI_PASSWORD, VLESS_DOMAIN, VLESS_PORT, VLESS_PATH
from api.db import get_db
from .phrases import START_TEXT, TARIFF_INFO
from .tariffs import TARIFFS
from .handlers.buy import router as buy_router
from .handlers.get_vpn import router as get_vpn_router
from .utils_xui import XUIClient, generate_vless_link, format_bytes


LOCK_FILE = "/tmp/vpn_bot.lock"

# Проверка на уже запущенный экземпляр
if os.path.exists(LOCK_FILE):
    print("Bot already running! Exiting...")
    sys.exit(1)

# Создаём lock file
with open(LOCK_FILE, "w") as f:
    f.write(str(os.getpid()))

import atexit
atexit.register(lambda: os.remove(LOCK_FILE) if os.path.exists(LOCK_FILE) else None)

# Настройка логирования

bot = Bot(token=TELEGRAM_BOT_TOKEN)
dp = Dispatcher()
router = Router()
dp.include_router(router)
dp.include_router(buy_router)
dp.include_router(get_vpn_router)


# Инициализация XUI клиента
xui = XUIClient(
    os.getenv('XUI_HOST'),
    os.getenv('XUI_USERNAME'),
    os.getenv('XUI_PASSWORD')
)

@dp.message(CommandStart())
async def start_handler(message: Message):
    print("🔥 Start command detected!")
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"Купить {t['name']} — {t['price']} ₽",
                    callback_data=f"buy_{tid}"
                )
            ]
            for tid, t in TARIFFS.items()
        ]
    )

    await message.answer(
        START_TEXT,
        reply_markup=kb,
        parse_mode="Markdown"
    )

    inbounds = xui.get_inbounds()
    
    if not inbounds:
        await query.edit_message_text("❌ Конфиги не найдены")
        return
    
    inbound = inbounds[0]  # Берем первый inbound
    settings = json.loads(inbound.get('settings', '{}'))
    clients = settings.get('clients', [])
    
    if not clients:
        await query.edit_message_text("❌ Клиенты не найдены")
        return
    
    client = clients[0]  # Первый клиент
    uuid = client['id']
    print('!!!uuid!!!', uuid)


@router.message(F.text == "/myvpn")
async def myvpn(message: Message):
    print("🔥 Myvpn list!")
    db = get_db()
    cur = db.cursor(dictionary=True)
    cur.execute("SELECT user_id FROM vpn_keys WHERE user_id=%s ORDER BY id DESC LIMIT 1",
                (message.from_user.id,))
    row = cur.fetchone()
    if not row:
        await message.answer("❌ У вас нет активных ключей")
        return

    await message.answer_document(
        BytesIO(row["config"].encode()),
        filename="vpn.conf"
    )


async def main():
    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()

async def shutdown(signal_name):
    print(f"🛑 Received {signal_name}, shutting down...", file=sys.stderr)
    await bot.session.close()

if __name__ == "__main__":
    asyncio.run(main())
    
    for s in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(
            s, lambda s=s: asyncio.create_task(shutdown(s.name))
        )

    try:
        loop.run_until_complete(main())
    finally:
        loop.close()
