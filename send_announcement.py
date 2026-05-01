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
        return True
    except Exception as e:
        print(f"❌ Ошибка {user_id}: {e}")
        return False

async def main():
    bot = Bot(token=BOT_TOKEN)
    
    print("=" * 50)
    print("📨 НАЧАЛО РАССЫЛКИ")
    print("=" * 50)
    
    # 1. Сначала отправляем себе
    print("\n📤 Отправка себе...")
    await send_to_user(bot, YOUR_ID, "Вы")
    
    # Ждём подтверждения от вас
    print("\n⏳ Проверьте, пришло ли сообщение. Если всё хорошо, отправляем остальным?")
    response = input("Отправить остальным? (y/n): ").strip().lower()
    
    if response != 'y':
        print("❌ Рассылка отменена")
        await bot.session.close()
        return
    
    # 2. Отправляем остальным
    print("\n📤 Отправка остальным пользователям...")
    
    success_count = 0
    for i, user_id in enumerate(OTHER_USERS, 1):
        print(f"  [{i}/{len(OTHER_USERS)}] Отправка {user_id}...")
        if await send_to_user(bot, user_id):
            success_count += 1
        await asyncio.sleep(0.5)  # Пауза между сообщениями
    
    # 3. Итог
    print("\n" + "=" * 50)
    print(f"📊 ИТОГ: {success_count}/{len(OTHER_USERS)} отправлено успешно")
    print("=" * 50)
    
    await bot.session.close()

if __name__ == "__main__":
    asyncio.run(main())