#!/usr/bin/env python3
import os
import json
import logging
import qrcode
import uuid
import sys
import httpx
import uuid
sys.path.insert(0, '/home/alvik/vpn-service')
from datetime import datetime, timedelta
from config import XUI_HOST, XUI_USERNAME, XUI_PASSWORD, VLESS_DOMAIN, VLESS_PORT, VLESS_PATH, TELEGRAM_BOT_TOKEN, YOO_KASSA_SECRET_KEY, YOO_KASSA_SHOP_ID, AMNEZIA_WG_API_URL, AMNEZIA_WG_API_PASSWORD
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from bot_xui.utils import XUIClient, generate_vless_link, format_bytes
from io import BytesIO
from typing import Optional, List, Dict
from dotenv import load_dotenv
from bot.tariffs import TARIFFS
from api.db import (
    get_or_create_user,
    create_payment,
    update_payment_status,
    get_payment_by_id,
    upsert_user_subscription,
    get_subscription_until,
    get_keys_by_tg_id,
    set_awg_test_activated,
    set_vless_test_activated,
    is_awg_test_activated,
    is_vless_test_activated,
)

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
    XUI_HOST,
    XUI_USERNAME,
    XUI_PASSWORD
)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg_id = update.effective_user.id
    get_or_create_user(tg_id)  # ← связь с БД
    """Обработчик команды /start"""
    keyboard = [
        [InlineKeyboardButton("📊 Мои конфиги", callback_data='my_configs')],
        [InlineKeyboardButton("📈 Статистика", callback_data='stats')],
        [InlineKeyboardButton("🏷 Тарифы", callback_data='tariffs')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        '👋 Добро пожаловать в tiin vpn manager!\n\n'
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
    elif query.data == 'test_awg':
        await create_test_awg_config(query)
    elif query.data == 'test_vless':
        await create_test_vless_config(query)
    elif query.data.startswith('select_awg_'):
        tariff_id = query.data.replace('select_awg_', '')
        context.user_data['vpn_type'] = 'awg'
        await process_payment(query, tariff_id, 'awg')
    elif query.data.startswith('select_vless_'):
        tariff_id = query.data.replace('select_vless_', '')
        context.user_data['vpn_type'] = 'vless'
        await process_payment(query, tariff_id, 'vless')
    elif query.data.startswith('check_btn'):
        await check_handler(query)

        
async def check_handler(query):
    print('🕯 !checking config output! 🕯')
    
    await query.message.reply_text(
        text=(
            f"🔑 Конфиг:\n\n"
            f"```\nvless://e5959df1-9bf2-4fbb-8aca-19c17b5766d5@344988.snk.wtf:443?type=ws&security=tls&path=/vless&encryption=none#tg_364224373_311f9ce7\n```"
            f"Скопируйте эту ссылку и вставьте в ваше приложение\n\n"
        ),
        parse_mode="Markdown"
    )


async def back_to_menu(query):
    """Вернуться в главное меню"""
    keyboard = [
        [InlineKeyboardButton("📊 Мои конфиги", callback_data='my_configs')],
        [InlineKeyboardButton("📈 Статистика", callback_data='stats')],
        [InlineKeyboardButton("🏷 Тарифы", callback_data='tariffs')],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        '👋 Добро пожаловать в tiin vpn manager!\n\n'
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
        # Тестовый - выбор протокола
        await create_test_config(query, tariff_id)
    else:
        # Платные - выбор протокола перед оплатой
        keyboard = [
            [InlineKeyboardButton("🔵 AmneziaWG", callback_data=f'select_awg_{tariff_id}')],
            [InlineKeyboardButton("🟢 VLESS (recommended)", callback_data=f'select_vless_{tariff_id}')],
            [InlineKeyboardButton("◀️ Назад к тарифам", callback_data='tariffs')],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        text = f"🛒 **Покупка тарифа {tariff['name']}**\n\n"
        text += f"💰 Стоимость: {tariff['price']} ₽\n"
        text += f"⏱ Период: {tariff['period']}\n\n"
        text += f"Выберите протокол VPN:"
        
        await query.edit_message_text(text, reply_markup=reply_markup, parse_mode='Markdown')

async def process_payment(query, tariff_id, vpn_type):
    """Создание платежа в YooKassa"""
    import uuid
    from yookassa import Configuration, Payment
    
    user_id = query.from_user.id
    tariff = TARIFFS.get(tariff_id)
    
    if not tariff:
        await query.edit_message_text("❌ Тариф не найден")
        return
    
    try:
        # Настройка YooKassa
        Configuration.account_id = YOO_KASSA_SHOP_ID
        Configuration.secret_key = YOO_KASSA_SECRET_KEY
        
        # Генерируем idempotency key (для повторных запросов)
        idempotency_key = str(uuid.uuid4())
        
        # Создаем платеж в YooKassa
        payment = Payment.create({
            "amount": {
                "value": str(tariff['price']),
                "currency": "RUB"
            },
            "confirmation": {
                "type": "redirect",
                "return_url": "https://t.me/tiin_service_bot"
            },
            "capture": True,
            "description": f"Оплата тарифа {tariff['name']}",
            "metadata": {
                "tg_id": str(user_id),
                "tariff": tariff_id,
                "vpn_type": vpn_type,  # ← Добавляем тип VPN
                "username": query.from_user.username or ""
            }
        }, idempotency_key)
        
        # ✅ Используем ID от YooKassa для сохранения в БД
        yookassa_payment_id = payment.id
        
        # Для детальной отладки
        if logger.level == logging.DEBUG:
            logger.debug(f"Full payment data: {payment.__dict__}")

        # Сохраняем платеж в БД
        create_payment(
            payment_id=yookassa_payment_id,  # ← ID от YooKassa
            tg_id=user_id,
            tariff=tariff_id,
            amount=tariff["price"],
            status="pending"
        )
        
        logger.info(f"Payment created: {yookassa_payment_id} for user {user_id}")
        
        # Отправляем ссылку на оплату
        keyboard = [
            [InlineKeyboardButton("💳 Оплатить", url=payment.confirmation.confirmation_url)],
            [InlineKeyboardButton("◀️ Назад к тарифам", callback_data='tariffs')],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        text = f"💳 **Оплата тарифа {tariff['name']}**\n\n"
        text += f"💰 Сумма: {tariff['price']} ₽\n"
        text += f"⏱ Период: {tariff['period']}\n\n"
        text += f"Нажмите кнопку для перехода к оплате.\n"
        text += f"После оплаты конфиг придет автоматически."
        
        await query.edit_message_text(
            text,
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
        
    except Exception as e:
        logger.error(f"Payment creation error: {e}")        
        # Кнопка назад
        await query.message.reply_text(
            "❌ Ошибка создания платежа. Попробуйте позже.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("◀️ В меню", callback_data="back_to_menu")]
            ])
        )

async def show_tariffs(query):
    """Показать доступные тарифы"""

    tg_id = query.from_user.id
    
    awg_test_already_activated = is_awg_test_activated(tg_id)
    vless_test_already_activated = is_vless_test_activated(tg_id)

    print('🚑 Test status:', awg_test_already_activated, vless_test_already_activated)
    
    text = "💳 **Доступные тарифы VPN**\n\n"
    
    for tariff_id, tariff in TARIFFS.items():
        print(f"\n--- Processing tariff: {tariff_id} ---")
        print(f"is_test: {tariff.get('is_test')}")
        print(f"awg_test: {awg_test_already_activated}")
        print(f"vless_test: {vless_test_already_activated}")
        print(f"OR result: {awg_test_already_activated or vless_test_already_activated}")
        print(f"Full condition: {tariff.get('is_test') and (awg_test_already_activated or vless_test_already_activated)}")
        
        # Пропускаем тестовый тариф, если он уже активирован
        if tariff.get('is_test') and (awg_test_already_activated or vless_test_already_activated):
            print(f"✅ SKIPPING {tariff_id}")
            continue
        
        print(f"➕ ADDING {tariff_id}")
        text += f"**{tariff['name']}**\n"
        text += f"💰 Цена: {tariff['price']} ₽\n"
        text += f"⏱ Период: {tariff['period']}\n"
        text += f"👥 Устройств: {tariff['device_limit']}\n"
        
        if tariff.get('features'):
            text += f"✨ Особенности: {', '.join(tariff['features'])}\n"
        
        text += "\n"
    
    # Кнопки для покупки каждого тарифа
    keyboard = []
    for tariff_id, tariff in TARIFFS.items():
        # Пропускаем тестовый тариф
        if tariff.get('is_test') and (awg_test_already_activated or vless_test_already_activated):
            continue
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
    """Показать конфиги пользователя (ТОЛЬКО через БД)"""

    tg_id = query.from_user.id
    keys = get_keys_by_tg_id(tg_id)

    if not keys:
        await query.edit_message_text(
            "❌ У вас нет активных VPN-конфигов",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🏷 Тарифы", callback_data="tariffs")],
                [InlineKeyboardButton("◀️ Назад", callback_data="back_to_menu")]
            ])
        )
        return

    now = datetime.now()

    for key in keys:
        expires_at = key["expires_at"]

        # Пропускаем истёкшие
        if expires_at and expires_at < now:
            continue

        vless_link = key["config"]

        # QR код
        qr = qrcode.QRCode(version=1, box_size=8, border=4)
        qr.add_data(vless_link)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")

        bio = BytesIO()
        bio.name = "qr.png"
        img.save(bio, "PNG")
        bio.seek(0)

        expires_text = (
            expires_at.strftime("%d.%m.%Y %H:%M")
            if expires_at else "∞"
        )

        await query.message.reply_photo(
            photo=bio,
            caption=
                "🔐 **Ваш VPN конфиг**\n\n"
                f"👤 Имя: `{key['client_name']}`\n"
                f"⏱ Действителен до: `{expires_text}`\n\n"
                f"📱 **Ссылка для подключения:**\n"
                f"`{vless_link}`\n\n"
                "Поддержка: v2rayNG / Nekoray",
            parse_mode="Markdown"
        )

    # Кнопка назад после вывода всех конфигов
    await query.message.reply_text(
        "⬆️ Ваши активные конфиги выше",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("◀️ В меню", callback_data="back_to_menu")]
        ])
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
    
    # Кнопка назад после вывода всех конфигов
    await query.message.reply_text(
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("◀️ В меню", callback_data="back_to_menu")]
        ])
    )



async def create_test_config(query, tariff_id):
    """Выбор типа VPN для тестового периода"""
    keyboard = [
        [InlineKeyboardButton("🔵 AmneziaWG", callback_data='test_awg')],
        [InlineKeyboardButton("🟢 VLESS (recommended)", callback_data='test_vless')],
        [InlineKeyboardButton("◀️ Назад к тарифам", callback_data='tariffs')],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    text = "🎁 **Тестовый период - Выберите протокол**\n\n"
    text += "🔵 **AmneziaWG**\n"
    text += "   • Высокая скорость\n"
    text += "   • Стабильное соединение\n"
    text += "   • Низкий пинг\n\n"
    text += "🟢 **VLESS**\n"
    text += "   • Обходит блокировки DPI\n"
    text += "   • Маскируется под HTTPS\n"
    text += "   • Работает в сложных сетях"
    
    await query.edit_message_text(text, reply_markup=reply_markup, parse_mode='Markdown')


async def create_test_awg_config(query):
    """Создание тестового AmneziaWG конфига"""
    user_id = query.from_user.id
    
    await query.edit_message_text("⏳ Создаю тестовый AmneziaWG конфиг...")
    
    try:
        
        client_name = f"user-{user_id}-{uuid.uuid4().hex[:8]}"
        
        async with httpx.AsyncClient(timeout=15) as client:
            # Login
            r = await client.post(
                f"{AMNEZIA_WG_API_URL}/api/session",
                json={"password": AMNEZIA_WG_API_PASSWORD}
            )
            r.raise_for_status()
            logger.info("✅ Logged in to AmneziaWG")
            
            # Create client
            r = await client.post(
                f"{AMNEZIA_WG_API_URL}/api/wireguard/client",
                json={"name": client_name}
            )
            r.raise_for_status()
            logger.info(f"✅ Client created: {client_name}")
            
            # Get client_id
            r = await client.get(f"{AMNEZIA_WG_API_URL}/api/wireguard/client")
            r.raise_for_status()
            
            client_id = None
            client_ip = None
            for c in r.json():
                if c.get("name") == client_name:
                    client_id = c.get("id")
                    client_ip = c.get("address")
                    break
            
            if not client_id:
                raise RuntimeError("Client not found after creation")
            
            logger.info(f"✅ Client ID: {client_id}, IP: {client_ip}")
            
            # Get config
            r = await client.get(
                f"{AMNEZIA_WG_API_URL}/api/wireguard/client/{client_id}/configuration"
            )
            r.raise_for_status()
            
            client_config = r.text
            if not client_config:
                raise RuntimeError("Empty configuration")
        
        # Отправляем конфиг файлом
        from io import BytesIO
        
        config_file = BytesIO(client_config.encode('utf-8'))
        config_file.name = f'amneziawg_test_{user_id}.conf'
        
        await query.message.reply_document(
            document=config_file,
            caption=f"🔵 **Тестовый AmneziaWG конфиг**\n\n"
                    f"👤 Клиент: `{client_name}`\n"
                    f"🌐 IP: `{client_ip}`\n"
                    f"⏱ Действителен: 1 час\n"
                    f"📱 **Инструкция:**\n"
                    f"1. Установите [AmneziaVPN](https://amnezia.org)\n"
                    f"2. Импортируйте файл конфигурации\n"
                    f"3. Подключитесь\n\n"
                    f"💬 Поддержка: @al_v1k",
            parse_mode ='HTML'
        )

        set_awg_test_activated(user_id)
        
        # Возврат в меню
        keyboard = [[InlineKeyboardButton("◀️ В главное меню", callback_data='back_to_menu')]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(
            "✅ Конфиг создан!\n\nПроверьте сообщение выше ☝️",
            reply_markup=reply_markup
        )
        
    except Exception as e:
        logger.error(f"Error creating AWG config: {e}")
        # Кнопка назад
        await query.message.reply_text(
            f"❌ Ошибка создания конфига\n\n{str(e)}\n\nПопробуйте позже или выберите VLESS.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("◀️ В меню", callback_data="back_to_menu")]
            ])
        )


async def create_test_vless_config(query):
    """Создание тестового VLESS конфига"""
    user_id = query.from_user.id
    
    await query.edit_message_text("⏳ Создаю тестовый VLESS конфиг...")
    
    try:
        import uuid
        import time
        
        # Генерируем данные клиента
        client_email = f"user-{user_id}-{uuid.uuid4().hex[:8]}"
        client_uuid = str(uuid.uuid4())
        
        # Время истечения: 1 час
        expiry_time = int((time.time() + 3600) * 1000)
        
        # Получаем inbound
        inbounds = xui.get_inbounds()
        if not inbounds:
            raise RuntimeError("Inbound не найден")
        
        inbound_id = inbounds[0]['id']
        
        # Создаем клиента
        success = xui.add_client(
            inbound_id=inbound_id,
            email=client_email,
            tg_id=user_id,
            uuid=client_uuid,
            expiry_time=expiry_time,
            total_gb=0,   # no limit
            limit_ip=1,   # 1 устройство
        )
        
        if not success:
            raise RuntimeError("Не удалось создать клиента")
        
        logger.info(f"✅ VLESS client created: {client_email}")
        
        # Генерируем ссылку
        vless_link = generate_vless_link(
            client_uuid,
            VLESS_DOMAIN,
            VLESS_PORT,
            VLESS_PATH,
            client_email
        )
        
        # Создаем QR код
        import qrcode
        from io import BytesIO
        
        qr = qrcode.QRCode(version=1, box_size=10, border=5)
        qr.add_data(vless_link)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        
        bio = BytesIO()
        bio.name = 'qr.png'
        img.save(bio, 'PNG')
        bio.seek(0)
        
        # Отправляем QR и конфиг
        await query.message.reply_photo(
            photo=bio,
            caption=f"🟢 **Тестовый VLESS конфиг**\n\n"
                    f"👤 ID: {client_email}\n"
                    f"⏱ Действителен: 1 час\n"
                    f"**Инструкция:**\n"
                    f"1. Установите v2rayNG (Android) или Nekoray (Windows/Linux)\n"
                    f"2. Отсканируйте QR или скопируйте ссылку\n"
                    f"3. Подключитесь\n\n"
                    f"💬 Поддержка: @al_v1k",
            parse_mode=None
        )
        
        # Отправка текста в безопасном code-блоке
        await query.message.reply_text(
            text=(
                f"🔑 Конфиг:\n\n"
                f"```\n{vless_link}\n```"
                f"Скопируйте эту ссылку и вставьте в ваше приложение\n\n"
            ),
            parse_mode="Markdown"
        )
        
        set_vless_test_activated(user_id)
    
        
    except Exception as e:
        logger.error(f"Error creating VLESS config: {e}")
        # Кнопка назад
        await query.message.reply_text(
            f"❌ Ошибка создания конфига\n\n{str(e)}\n\nПопробуйте позже или выберите AmneziaWG.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("◀️ В меню", callback_data="back_to_menu")]
            ])
        )

async def send_link_safely(
    tg_id: int,
    text: str,
    buttons: Optional[List[List[Dict[str, str]]]] = None,
    parse_mode: Optional[str] = None
):
    """
    Универсальная функция отправки сообщения пользователю
    
    Args:
        tg_id: Telegram ID пользователя
        text: Текст сообщения
        buttons: Список кнопок [[{"text": "Текст", "callback_data": "data"}]]
        parse_mode: "Markdown" или "HTML"
    """
    try:
        telegram_api = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        
        data = {
            'chat_id': tg_id,
            'text': text
        }
        
        if parse_mode:
            data['parse_mode'] = parse_mode
        
        if buttons:
            keyboard = {"inline_keyboard": buttons}
            data['reply_markup'] = json.dumps(keyboard)
        
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(telegram_api, data=data)
            
            if response.status_code == 200:
                logger.info(f"✅ Message sent to user: {tg_id}")
                return True
            else:
                logger.warning(f"⚠️ Failed: {response.text}")
                return False
                
    except Exception as e:
        logger.error(f"❌ Error: {e}")
        return False

def main():
    """Запуск бота"""
    token = TELEGRAM_BOT_TOKEN
    
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