#!/usr/bin/env python3
import os
import json
import logging
from config import XUI_HOST, XUI_USERNAME, XUI_PASSWORD, VLESS_DOMAIN, VLESS_PORT, VLESS_PATH, TELEGRAM_BOT_TOKEN
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from utils import XUIClient, generate_vless_link, format_bytes
import qrcode
from io import BytesIO

# Загрузка переменных окружения
load_dotenv()

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Инициализация XUI клиента
xui = XUIClient(
    os.getenv('XUI_HOST'),
    os.getenv('XUI_USERNAME'),
    os.getenv('XUI_PASSWORD')
)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /start"""
    keyboard = [
        [InlineKeyboardButton("📊 Мои конфиги", callback_data='my_configs')],
        [InlineKeyboardButton("📈 Статистика", callback_data='stats')],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        '👋 Добро пожаловать в VPN Manager!\n\n'
        'Выберите действие:',
        reply_markup=reply_markup
    )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик нажатий кнопок"""
    query = update.callback_query
    await query.answer()
    
    if query.data == 'my_configs':
        await show_configs(query)
    elif query.data == 'stats':
        await show_stats(query)

async def show_configs(query):
    """Показать конфиги пользователя"""
    # Для демо - показываем первого клиента из inbound
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
    email = client['email']
    
    # Генерируем VLESS ссылку
    vless_link = generate_vless_link(
        uuid,
        os.getenv('VLESS_DOMAIN'),
        os.getenv('VLESS_PORT'),
        os.getenv('VLESS_PATH'),
        email
    )
    
    # Создаем QR код
    qr = qrcode.QRCode(version=1, box_size=10, border=5)
    qr.add_data(vless_link)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    
    bio = BytesIO()
    bio.name = 'qr.png'
    img.save(bio, 'PNG')
    bio.seek(0)
    
    # Отправляем QR код и ссылку
    await query.message.reply_photo(
        photo=bio,
        caption=f"🔐 **VLESS конфиг**\n\n"
                f"👤 Email: `{email}`\n"
                f"🌐 Домен: `{os.getenv('VLESS_DOMAIN')}`\n"
                f"🔌 Порт: `{os.getenv('VLESS_PORT')}`\n\n"
                f"📱 Ссылка для подключения:\n`{vless_link}`\n\n"
                f"Отсканируйте QR код или скопируйте ссылку в приложение v2rayNG/Nekoray",
        parse_mode='Markdown'
    )

async def show_stats(query):
    """Показать статистику"""
    inbounds = xui.get_inbounds()
    
    if not inbounds:
        await query.edit_message_text("❌ Данные не найдены")
        return
    
    total_up = sum(ib.get('up', 0) for ib in inbounds)
    total_down = sum(ib.get('down', 0) for ib in inbounds)
    
    text = f"📊 **Статистика VPN**\n\n"
    text += f"⬆️ Отправлено: {format_bytes(total_up)}\n"
    text += f"⬇️ Получено: {format_bytes(total_down)}\n"
    text += f"📦 Всего: {format_bytes(total_up + total_down)}\n"
    
    await query.edit_message_text(text, parse_mode='Markdown')

def main():
    """Запуск бота"""
    token = os.getenv('TELEGRAM_BOT_TOKEN')
    
    if not token:
        logger.error("TELEGRAM_BOT_TOKEN not found in .env")
        return
    
    # Создаем приложение
    application = Application.builder().token(token).build()
    
    # Регистрируем обработчики
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(button_handler))
    
    # Запускаем бота
    logger.info("Bot started!")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()