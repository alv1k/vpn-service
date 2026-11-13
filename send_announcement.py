#!/usr/bin/env python3
import asyncio
from aiogram import Bot

# Токен вашего бота
BOT_TOKEN = "8075947163:AAEQ5A4rmMLRXjynOiNH3lXWQV-EHwCkdn8"  # Замените на реальный токен

# ID пользователей
YOUR_ID = 364224373  # Ваш tg_id (получите через @userinfobot)

# Остальные пользователи
OTHER_USERS = [
    451181644,   # .
    6864368530,  # Александр
    667624374,   # Alamai
    88486656,    # Yana
    909509933,   # Валентина
    6335998601,  # Yakutia
    392639199,   # Angelika
    6648354839,  # Айсен
]

MESSAGE = """
🎉 Туннель снова с вами!

Мы перенастроили VPN-инфраструктуру.

🚀 Что сделано:
• Обновлены серверные протоколы
• Настроены автоматические подписки
• Убраны старые прокси-прослойки (скорость выше)

Как получить конфиг?
👉 Просто напишите /start нашему боту: @tiin_service_bot, подключитесь используя Мастер подключения или самостоятельно скопировав ссылку подписки из Мои Конфиги

Вопросы — в поддержку.
Ваш TIIN 🌲
"""

async def send_to_user(bot: Bot, user_id: int, name: str = ""):
    """Отправляет сообщение пользователю"""
    try:
        await bot.send_message(user_id, MESSAGE)
        print(f"✅ Отправлено {user_id} ({name})")
        try:
            from api.db import execute_query, log_message_sent
            execute_query("UPDATE users SET bot_blocked = 0 WHERE tg_id = %s AND bot_blocked = 1", (user_id,))
            log_message_sent(tg_id=user_id, source="announcement", status='sent',
                             message_text=MESSAGE[:500])
        except Exception:
            pass
        return True
    except Exception as e:
        err = str(e).lower()
        is_block = "blocked" in err or "deactivated" in err
        status = 'blocked' if is_block else 'failed'
        print(f"{'🚫' if is_block else '❌'} {status.upper()} {user_id}: {e}")
        if is_block:
            try:
                from api.db import execute_query
                execute_query("UPDATE users SET bot_blocked = 1 WHERE tg_id = %s", (user_id,))
            except Exception:
                pass
        try:
            from api.db import log_message_sent
            log_message_sent(tg_id=user_id, source="announcement", status=status,
                             error_text=str(e)[:255])
        except Exception:
            pass
        return False

async def main():
    from api.db import execute_query

    bot = Bot(token=BOT_TOKEN)

    print("=" * 50)
    print("📨 НАЧАЛО РАССЫЛКИ")
    print("=" * 50)

    # Filter out users who blocked the bot
    blocked_rows = execute_query(
        "SELECT tg_id FROM users WHERE tg_id IN %s AND bot_blocked = 1",
        (tuple(OTHER_USERS),),
        fetch='all',
    ) if OTHER_USERS else []
    blocked_set = {r['tg_id'] for r in blocked_rows}

    if blocked_set:
        print(f"\n🚫 Skipping {len(blocked_set)} users who blocked the bot: {blocked_set}")

    users_to_send = [uid for uid in OTHER_USERS if uid not in blocked_set]

    # 1. Сначала отправляем себе
    print("\n📤 Отправка себе...")
    await send_to_user(bot, YOUR_ID, "Вы")

    # Ждём подтверждения от вас
    print("\n⏳ Проверьте, пришло ли сообщение. Если всё хорошо, отправляем остальным?")
    response = input(f"Отправить {len(users_to_send)} пользователям? (y/n): ").strip().lower()

    if response != 'y':
        print("❌ Рассылка отменена")
        await bot.session.close()
        return

    # 2. Отправляем остальным
    print("\n📤 Отправка остальным пользователям...")

    success_count = 0
    for i, user_id in enumerate(users_to_send, 1):
        print(f"  [{i}/{len(users_to_send)}] Отправка {user_id}...")
        if await send_to_user(bot, user_id):
            success_count += 1
        await asyncio.sleep(0.5)  # Пауза между сообщениями

    # 3. Итог
    print("\n" + "=" * 50)
    print(f"📊 ИТОГ: {success_count}/{len(users_to_send)} отправлено успешно")
    if blocked_set:
        print(f"🚫 Пропущено (бот заблокирован): {len(blocked_set)}")
    print("=" * 50)

    await bot.session.close()

if __name__ == "__main__":
    asyncio.run(main())