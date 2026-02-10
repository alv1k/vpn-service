#!/usr/bin/env python3
import os
import json
import logging
import qrcode
import sys
sys.path.insert(0, '/home/alvik/vpn-service')
from config import XUI_HOST, XUI_USERNAME, XUI_PASSWORD, VLESS_DOMAIN, VLESS_PORT, VLESS_PATH, TELEGRAM_BOT_TOKEN
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from utils import XUIClient, generate_vless_link, format_bytes
from io import BytesIO
from dotenv import load_dotenv
from bot.tariffs import TARIFFS


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
        [InlineKeyboardButton("📈 Тарифы", callback_data='tariffs')],
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
    elif query.data == 'tariffs':
        await show_tariffs(query)
    elif query.data == 'back_to_menu':
        await back_to_menu(query)
    elif query.data.startswith('buy_tariff_'):
        tariff_id = query.data.replace('buy_tariff_', '')
        await buy_tariff(query, tariff_id)
    elif query.data.startswith('create_test_config_'):  # Новый обработчик
        tariff_id = query.data.replace('create_test_config_', '')
        await create_test_config(query, tariff_id)
    elif query.data.startswith('pay_card_'):
        tariff_id = query.data.replace('pay_card_', '')
        await process_payment(query, tariff_id, 'card')

async def back_to_menu(query):
    """Вернуться в главное меню"""
    keyboard = [
        [InlineKeyboardButton("📊 Мои конфиги", callback_data='my_configs')],
        [InlineKeyboardButton("📈 Статистика", callback_data='stats')],
        [InlineKeyboardButton("📊 Тарифы", callback_data='tariffs')],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        '👋 Добро пожаловать в VPN Manager!\n\n'
        'Выберите действие:',
        reply_markup=reply_markup
    )

async def buy_tariff(query, tariff_id):
    """Обработка покупки тарифа"""
    if tariff_id not in TARIFFS:
        await query.edit_message_text("❌ Тариф не найден")
        return
    
    tariff = TARIFFS[tariff_id]
    
    if tariff_id == "test_1h":
        # Тестовый тариф - сразу создаем конфиг
        text = f"🎁 **Тестовый период {tariff['name']}**\n\n"
        text += f"💰 Стоимость: {tariff['price']} ₽\n"
        text += f"⏱ Период: {tariff['period']}\n\n"
        text += f"Нажмите кнопку ниже для получения конфига:"
        
        keyboard = [
            [InlineKeyboardButton("🎁 Получить тестовый конфиг", callback_data=f'create_test_config_{tariff_id}')],
            [InlineKeyboardButton("◀️ Назад к тарифам", callback_data='tariffs')],
        ]
    else:
        # Платные тарифы - переход к оплате
        text = f"🛒 **Покупка тарифа {tariff['name']}**\n\n"
        text += f"💰 Стоимость: {tariff['price']} ₽\n"
        text += f"⏱ Период: {tariff['period']}\n\n"
        text += f"Для оплаты выберите способ:"
        
        keyboard = [
            [InlineKeyboardButton("💳 Банковская карта", callback_data=f'pay_card_{tariff_id}')],
            # [InlineKeyboardButton("🪙 Криптовалюта", callback_data=f'pay_crypto_{tariff_id}')],
            [InlineKeyboardButton("◀️ Назад к тарифам", callback_data='tariffs')],
        ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(text, reply_markup=reply_markup, parse_mode='Markdown')

async def show_tariffs(query):
    """Показать доступные тарифы"""
    
    # Формируем текст с тарифами
    text = "💳 **Доступные тарифы VPN**\n\n"
    
    for tariff_id, tariff in TARIFFS.items():
        text += f"**{tariff['name']}**\n"
        text += f"💰 Цена: {tariff['price']} ₽\n"
        text += f"⏱ Период: {tariff['period']} дней\n"
        text += f"👥 Устройств: {tariff['device_limit']}\n"
        
        if tariff.get('features'):
            text += f"✨ Особенности: {', '.join(tariff['features'])}\n"
        
        text += "\n"
    
    # Кнопки для покупки каждого тарифа
    keyboard = []
    for tariff_id, tariff in TARIFFS.items():
        keyboard.append([
            InlineKeyboardButton(
                f"💳 Купить {tariff['name']} - {tariff['price']} ₽",
                callback_data=f'buy_tariff_{tariff_id}'
            )
        ])
    
    # Кнопка "Назад"
    keyboard.append([InlineKeyboardButton("◀️ Назад", callback_data='back_to_menu')])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        text,
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

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

async def create_test_config(query, tariff_id):
    """Создание тестового конфига"""
    user_id = query.from_user.id
    username = query.from_user.username or str(user_id)
    
    await query.edit_message_text("⏳ Создаю тестовый конфиг...")
    
    try:
        # Генерируем уникальный email для клиента
        import uuid
        import time
        
        # Генерируем уникальные данные для клиента
        client_email = f"test_{user_id}_{uuid.uuid4().hex[:8]}"
        client_uuid = str(uuid.uuid4())
        
        # Время истечения: текущее время + 1 час (в миллисекундах)
        expiry_time = int((time.time() + 3600) * 1000)
        
        # Получаем первый inbound
        inbounds = xui.get_inbounds()
        if not inbounds:
            await query.edit_message_text("❌ Ошибка: Inbound не найден")
            return
        
        inbound_id = inbounds[0]['id']
        
        # Добавляем клиента
        success = xui.add_client(
            inbound_id=inbound_id,
            email=client_email,
            uuid=client_uuid,
            expiry_time=expiry_time,
            total_gb=10,  # 10 ГБ лимит
            limit_ip=1    # 1 устройство
        )
        
        if not success:
            await query.edit_message_text("❌ Ошибка создания конфига. Попробуйте позже.")
            return
        
        # Генерируем VLESS ссылку
        vless_link = generate_vless_link(
            client_uuid,
            os.getenv('VLESS_DOMAIN'),
            os.getenv('VLESS_PORT'),
            os.getenv('VLESS_PATH'),
            client_email
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
        
        # Отправляем конфиг
        await query.message.reply_photo(
            photo=bio,
            caption=f"🎁 **Тестовый VLESS конфиг**\n\n"
                    f"👤 ID: `{client_email}`\n"
                    f"⏱ Действителен: 1 час\n"
                    f"📊 Лимит трафика: 10 ГБ\n"
                    f"🌐 Домен: `{os.getenv('VLESS_DOMAIN')}`\n\n"
                    f"📱 Ссылка для подключения:\n`{vless_link}`\n\n"
                    f"Отсканируйте QR код или скопируйте ссылку в v2rayNG/Nekoray",
            parse_mode='Markdown'
        )
        
        # Возвращаем в меню
        keyboard = [
            [InlineKeyboardButton("◀️ В главное меню", callback_data='back_to_menu')],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            "✅ Тестовый конфиг успешно создан!\n\nПроверьте сообщение выше.",
            reply_markup=reply_markup
        )
        
    except Exception as e:
        logger.error(f"Error creating test config: {e}")
        await query.edit_message_text(f"❌ Ошибка: {str(e)}\n\nПопробуйте позже.")

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