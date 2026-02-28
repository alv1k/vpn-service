#!/usr/bin/env python3
import os
import json
import logging
import qrcode
import uuid
import sys
import httpx
import time
sys.path.insert(0, '/home/alvik/vpn-service')
from datetime import datetime, timedelta, timezone
from yookassa import Configuration, Payment
from config import XUI_HOST, XUI_USERNAME, XUI_PASSWORD, VLESS_DOMAIN, VLESS_PORT, VLESS_PATH, TELEGRAM_BOT_TOKEN, YOO_KASSA_SECRET_KEY, YOO_KASSA_SHOP_ID, AMNEZIA_WG_API_URL, AMNEZIA_WG_API_PASSWORD, VLESS_PBK, VLESS_SID, VLESS_SNI 
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand, Bot
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from bot_xui.utils import XUIClient, generate_vless_link, format_bytes, send_telegram_notification
from bot_xui.tariffs import TARIFFS
from io import BytesIO
from typing import Optional, List, Dict
from dotenv import load_dotenv
from api.db import (
    get_or_create_user,
    create_payment,
    get_keys_by_tg_id,
    set_awg_test_activated,
    set_vless_test_activated,
    is_awg_test_activated,
    is_vless_test_activated,
    get_user_email,
    create_vpn_key,
    get_all_users_tg_ids
)

ADMIN_TG_ID = 364224373  # твой tg_id
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
        # [InlineKeyboardButton("📈 Статистика", callback_data='stats')],
        [InlineKeyboardButton("🏷 Тарифы", callback_data='tariffs')],
        [InlineKeyboardButton("📑 Инструкция и ссылки", callback_data='instructions')],
        # [InlineKeyboardButton("test", callback_data='test')],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        '👋 Добро пожаловать в tiin vpn manager!\n\n'
        'Выберите действие:',
        reply_markup=reply_markup
    )

async def post_init(application):
    """Установка команд меню после инициализации бота."""
    commands = [
        BotCommand(command="start", description="Начать взаимодействие с ботом"),
        # Можно добавить другие команды, например:
        # BotCommand(command="help", description="Помощь"),
    ]
    await application.bot.set_my_commands(commands)


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
        parts = query.data.replace('buy_tariff_', '')
        is_renew = parts.endswith('_renew')
        tariff_id = parts.replace('_renew', '')    
        renew_info = context.user_data.get('renew_info', {})    
        await buy_tariff(query, tariff_id, is_renew=is_renew, **renew_info)
    elif query.data.startswith('create_test_config_'):
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
    elif query.data.startswith('instructions'):
        await show_instructions(query)
    elif query.data.startswith('show_key_'):
        await handle_show_key(query)
    elif query.data.startswith('renew_'):
        parts = query.data.replace('renew_', '', 1)
        client_name, inbound_id = parts.split('_', 1)
        await renew_client(query, context, inbound_id, client_name)
    elif query.data.startswith('test'):
        # xui.add_or_extend_client(5, 'tg_364224373_312f2bfb',  364224373, 'e5c376a6-29d1-4e04-af7b-8fe9680b1503' )
        await send_telegram_notification(364224373, 'test here<pre>https://example.com/some/long/link</pre>test here')

        
async def back_to_menu(query):
    """Вернуться в главное меню"""
    keyboard = [
        [InlineKeyboardButton("📊 Мои конфиги", callback_data='my_configs')],
        # [InlineKeyboardButton("📈 Статистика", callback_data='stats')],
        [InlineKeyboardButton("🏷 Тарифы", callback_data='tariffs')],
        [InlineKeyboardButton("📑 Инструкция и ссылки", callback_data='instructions')],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    text = (
        '👋 Добро пожаловать в tiin vpn manager!\n\n'
        'Выберите действие:'
    )

    try:
        # Если сообщение текстовое
        await query.edit_message_text(text, reply_markup=reply_markup)
    except Exception:
        try:
            # Если сообщение с фото/медиа — редактируем caption
            # await query.edit_message_caption(caption=text, reply_markup=reply_markup)
            # Если совсем не получается редактировать — удаляем и отправляем новое
            await query.message.delete()
            await query.message.answer(text, reply_markup=reply_markup)
        except Exception:
            logger.error(f"Welcome message error")        
            # Кнопка назад
            await query.message.reply_text(
                "❌ Ошибка возвращения в стартовое меню. Попробуйте написать команду '/start' или нажать кнопку ниже.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("◀️ В меню", callback_data="back_to_menu")]
                ])
            )


async def show_instructions(query):

    caption = (
        "📱 *Инструкция по подключению:*\n\n"
        "*1️⃣* Выберите приложение для вашей ОС (кнопки ниже)\n"
        "*2️⃣* Отсканируйте QR-код или скопируйте ссылку\n"
        "*3️⃣* Подключитесь к VPN\n\n"
        "💬 *Поддержка:* @al_v1k"
    )

    keyboard = [        
        # [InlineKeyboardButton("🍎 AmneziaVPN (iOS) - AWG", url="https://apps.apple.com/app/amneziavpn/id1600529900")],       
        # macOS - AWG
        # [InlineKeyboardButton("💻 AmneziaVPN (macOS) - AWG", url="https://github.com/amnezia-vpn/amnezia-client/releases")],        
        # Windows - AWG
        # [InlineKeyboardButton("🖥 AmneziaVPN (Windows) - AWG", url="https://github.com/amnezia-vpn/amnezia-client/releases")],
        # Linux - AWG
        # [InlineKeyboardButton("🐧 AmneziaVPN (Linux) - AWG", url="https://github.com/amnezia-vpn/amnezia-client/releases")],


        # Android - VLESS
        [InlineKeyboardButton("🤖 Amnezia VPN - Android", url="https://play.google.com/store/apps/details?id=org.amnezia.vpn&hl=ru")],
        [InlineKeyboardButton("🤖 v2rayTun - Android", url="https://play.google.com/store/apps/details?id=com.v2raytun.android")],
                
        # iOS
        [InlineKeyboardButton("🍎 v2RayTun app - iOS", url="https://apps.apple.com/ru/app/v2raytun/id6476628951")], 
        [InlineKeyboardButton("🍎 V2Box app - iOS", url="https://apps.apple.com/us/app/v2box-v2ray-client/id6446814690")], 

        # macOS - VLESS
        [InlineKeyboardButton("💻 NekoRay - macOS", url="https://en.nekoray.org/")],
        [InlineKeyboardButton("💻 Fox VPN - macOS", url="https://bestfoxapp.com/en/products/mac")],
        
        # Windows - VLESS
        [InlineKeyboardButton("🖥 Hiddify - Windows", url="https://hiddify.com/")],
        [InlineKeyboardButton("💻 NekoRay - Windows", url="https://en.nekoray.org/")],
                
        # TV
        [InlineKeyboardButton("📺 VPN4TV: VPN для ТВ - TV", url="https://play.google.com/store/apps/details?id=com.vpn4tv.hiddify")],
        
        [InlineKeyboardButton("◀️ В меню", callback_data="back_to_menu")]
    ]

    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.edit_message_text(
        text=caption,
        reply_markup=reply_markup,
        parse_mode='HTML'
    )
    
async def send_message_by_tg_id(tg_id: int, text: str, parse_mode: str = None, reply_markup=None):
    """Отправка сообщения пользователю по tg_id"""    
    bot = Bot(token=TELEGRAM_BOT_TOKEN)
    try:
        await bot.send_message(
            chat_id=tg_id,
            text=text,
            parse_mode=parse_mode,
            reply_markup=reply_markup
        )
        return True
    except Exception as e:
        print(f"[send_message] Ошибка отправки для {tg_id}: {e}")
        return False

async def send_to_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда: /send <tg_id> <сообщение>"""
    if update.effective_user.id != ADMIN_TG_ID:
        await update.message.reply_text("❌ Нет доступа")
        return

    
    # парсим вручную из текста сообщения
    raw = update.message.text.split(maxsplit=2)  # ['/send', 'tg_id', 'text']
    
    if len(context.args) < 2:
        await update.message.reply_text("Использование: /send <tg_id> <сообщение>")
        return
    
    try:
        tg_id = int(raw[1])
    except ValueError:
        await update.message.reply_text("❌ tg_id должен быть числом")
        return
        
    text = raw[2] 
    
    success = await send_message_by_tg_id(tg_id, text)
    
    if success:
        await update.message.reply_text(f"✅ Сообщение отправлено пользователю {tg_id}")
    else:
        await update.message.reply_text(f"❌ Не удалось отправить. Пользователь мог заблокировать бота.")

async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/broadcast <сообщение> — рассылка всем пользователям"""
    if update.effective_user.id != ADMIN_TG_ID:
        await update.message.reply_text("❌ Нет доступа")
        return

    raw = update.message.text.split(maxsplit=1)  # ['/broadcast', 'текст сообщения']
    
    if len(raw) < 2:
        await update.message.reply_text("Использование: /broadcast <сообщение>")
        return

    text = raw[1]
    users = get_all_users_tg_ids()

    ok, fail = 0, 0
    for tg_id in users:
        success = await send_message_by_tg_id(tg_id, text)
        if success:
            ok += 1
        else:
            fail += 1

    await update.message.reply_text(f"📬 Рассылка завершена\n✅ Успешно: {ok}\n❌ Ошибок: {fail}")


async def buy_tariff(query, tariff_id, is_renew = False, inbound_id=None, client_name=None): 
    """Обработка покупки тарифа"""
    if tariff_id not in TARIFFS:
        await query.edit_message_text("❌ Тариф не найден buy tariff")
        return
    
    tariff = TARIFFS[tariff_id]
    
    if tariff_id == "test_24h":
        # Тестовый - выбор протокола
        await create_test_config(query, tariff_id)
    else:
        # Платные - выбор протокола
        keyboard = [
            [InlineKeyboardButton("🔵 AmneziaWG", callback_data=f'select_awg_{tariff_id}')],
            [InlineKeyboardButton("🟢 VLESS (recommended)", callback_data=f'select_vless_{tariff_id}')],
            [InlineKeyboardButton("◀️ Назад к тарифам", callback_data='tariffs')],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        text = f"🛒 **Покупка тарифа {tariff['name']}**\n\n"
        text += f"💰 Стоимость: {tariff['price']} ₽\n"
        text += f"⏱ Период: {tariff['period']}\n\n"
        # text += f"Выберите протокол VPN:"
        
        # await query.edit_message_text(text, parse_mode='Markdown')
        await process_payment(query, tariff_id, 'vless', is_renew, client_name, inbound_id)

async def process_payment(query, tariff_id, vpn_type, is_renew = False, client_name=None, inbound_id=None):
    """Создание платежа в YooKassa"""
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
            "description": f"{'Оплата' if is_renew else 'Продление'} тарифа {tariff['name']}",
            "metadata": {
                "tg_id": str(user_id),
                "tariff": tariff_id,
                "vpn_type": vpn_type,  # ← Добавляем тип VPN
                "username": query.from_user.username or "",
                "is_renew": "true" if is_renew else "false",
                "client_name": client_name if is_renew else "",  # передай client_name в функцию
                "inbound_id": str(inbound_id) if is_renew else "",  # и inbound_id
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
            [InlineKeyboardButton("◀️ Назад к тарифам", callback_data='tariffs')] if not is_renew else [InlineKeyboardButton("◀️ Назад в меню", callback_data='back_to_menu')],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        text = f"💳 **Оплата тарифа {tariff['name']}**\n\n"
        text += f"💰 Сумма: {tariff['price']} ₽\n"
        text += f"⏱ Период: {tariff['period']}\n\n"
        text += f"👥 Устройств: {tariff['device_limit']}\n\n"
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

async def renew_client(query, context, inbound_id: int, client_email: str):
    
    # Сохраняем до цикла
    context.user_data['renew_info'] = {
        'inbound_id': inbound_id,
        'client_email': client_email
    }

    regular_tariffs = []

    for tariff_id, tariff in TARIFFS.items():
        tariff_info = {**tariff, 'id': tariff_id}
        if tariff.get('is_test'):
            continue
        elif tariff_id == "admin_test":
            continue
        else:
            regular_tariffs.append(tariff_info)

    regular_tariffs.sort(key=lambda x: x.get('days', 0))

    text = "💎 **Выберите длительность продления подписки VPN**\n\n"
    text += "📦 **Основные тарифы**\n"

    for i, tariff in enumerate(regular_tariffs):
        bullet = "├" if i < len(regular_tariffs) - 1 else "└"
        price_per_day = tariff['price'] / tariff['days'] if tariff.get('days') else 0
        
        text += f"{bullet}─ **{tariff['name']}**\n"
        text += f"{bullet}   💰 {tariff['price']} ₽  ·  ⏱ {tariff['period']}  ·  👥 {tariff['device_limit']} устройств\n"
        
        if tariff.get('days', 0) > 3:
            text += f"{bullet}   💫 всего {price_per_day:.1f} ₽/день\n"
        
        if tariff.get('features'):
            text += f"{bullet}   ✨ {', '.join(tariff['features'])}\n"
        
        if tariff.get('days', 0) >= 90:
            text += f"{bullet}   🌟 **Самый выгодный!**\n"
        
        if i < len(regular_tariffs) - 1:
            text += f"{bullet}  \n"

    text += "_Выберите подходящий тариф ниже:_ ⬇️"

    keyboard = []
    regular_row = []

    for i, tariff in enumerate(regular_tariffs):
        if tariff.get('days', 0) <= 3:
            emoji = "⚡️"
        elif tariff.get('days', 0) <= 7:
            emoji = "📱"
        elif tariff.get('days', 0) <= 14:
            emoji = "📊"
        elif tariff.get('days', 0) <= 30:
            emoji = "📦"
        else:
            emoji = "💎"
        
        button_text = f"{emoji} {tariff['days']}дн | {tariff['price']}₽"

        regular_row.append(
            InlineKeyboardButton(
                button_text,
                callback_data=f'buy_tariff_{tariff["id"]}_renew'
            )
        )
        
        if len(regular_row) == 2 or i == len(regular_tariffs) - 1:
            keyboard.append(regular_row)
            regular_row = []

    keyboard.append([InlineKeyboardButton("◀️ Вернуться в меню", callback_data='back_to_menu')])

    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.message.reply_text(
        text,
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )


async def show_tariffs(query):
    """Показать доступные тарифы"""

    tg_id = query.from_user.id
    
    awg_test_already_activated = is_awg_test_activated(tg_id)
    vless_test_already_activated = is_vless_test_activated(tg_id)
    
    # Группируем тарифы по типам для красивого отображения
    test_tariffs = []
    regular_tariffs = []
    special_tariffs = []  # для админа

    for tariff_id, tariff in TARIFFS.items():
        tariff_info = {**tariff, 'id': tariff_id}
        if tariff.get('is_test'):
            test_tariffs.append(tariff_info)
        elif tariff_id == "admin_test":
            special_tariffs.append(tariff_info)
        else:
            regular_tariffs.append(tariff_info)

    # Сортируем обычные тарифы по количеству дней
    regular_tariffs.sort(key=lambda x: x.get('days', 0))

    text = "💎 **Доступные тарифы VPN**\n\n"

    # Показываем тестовый тариф, если он еще не активирован
    if test_tariffs and not (awg_test_already_activated or vless_test_already_activated):
        text += "🎁 **Попробуйте бесплатно**\n"
        text += "┌─────────────────────\n"
        for tariff in test_tariffs:
            text += f"│ ✨ **{tariff['name']}**\n"
            text += f"│    ▸ Цена: **{tariff['price']} ₽**\n"
            text += f"│    ▸ Период: {tariff['period']}\n"
            text += f"│    ▸ Устройств: {tariff['device_limit']}\n"
            if tariff.get('features'):
                text += f"│    ▸ {', '.join(tariff['features'])}\n"
        text += "└─────────────────────\n\n"

    # Основные тарифы
    text += "📦 **Основные тарифы**\n"

    # Создаем красивое отображение для основных тарифов
    for i, tariff in enumerate(regular_tariffs):
        # Используем разные символы для разнообразия
        bullet = "├" if i < len(regular_tariffs) - 1 else "└"
        
        # Рассчитываем цену за день для информативности
        price_per_day = tariff['price'] / tariff['days'] if tariff.get('days') else 0
        
        # Основная строка тарифа
        text += f"{bullet}─ **{tariff['name']}**\n"
        text += f"{bullet}   💰 {tariff['price']} ₽  ·  ⏱ {tariff['period']}  ·  👥 {tariff['device_limit']} устройств\n"
        
        # Показываем цену за день для длинных тарифов
        if tariff.get('days', 0) > 3:
            text += f"{bullet}   💫 всего {price_per_day:.1f} ₽/день\n"
        
        # Особенности если есть
        if tariff.get('features'):
            text += f"{bullet}   ✨ {', '.join(tariff['features'])}\n"
        
        # Добавляем подсказку о выгоде для длинных тарифов
        if tariff.get('days', 0) >= 90:
            text += f"{bullet}   🌟 **Самый выгодный!**\n"
        
        if i < len(regular_tariffs) - 1:
            text += f"{bullet}  \n"  # Отступ между тарифами

    text += "\n"

    # Специальные тарифы (только для админа)
    if special_tariffs and tg_id == 364224373:
        text += "⚙️ **Служебные тарифы**\n"
        for tariff in special_tariffs:
            text += f"└─ 🔧 {tariff['name']}\n"
            text += f"   💰 {tariff['price']} ₽ · {tariff['period']}\n"
        text += "\n"

    # Подсказка внизу
    text += "_Выберите подходящий тариф ниже:_ ⬇️"

    # Создаем красивые кнопки
    keyboard = []

    # Кнопки для тестового тарифа (если доступен)
    if test_tariffs and not (awg_test_already_activated or vless_test_already_activated):
        test_row = []
        for tariff in test_tariffs:
            test_row.append(
                InlineKeyboardButton(
                    f"🎁 {tariff['name']} (0 ₽)",
                    callback_data=f'buy_tariff_{tariff["id"]}'
                )
            )
        keyboard.append(test_row)

    # Группируем основные тарифы по 2 в ряд для компактности
    regular_row = []
    for i, tariff in enumerate(regular_tariffs):
        # Эмодзи в зависимости от длительности
        if tariff.get('days', 0) <= 3:
            emoji = "⚡️"
        elif tariff.get('days', 0) <= 7:
            emoji = "📱"
        elif tariff.get('days', 0) <= 14:
            emoji = "📊"
        elif tariff.get('days', 0) <= 30:
            emoji = "📦"
        else:
            emoji = "💎"
        
        button_text = f"{emoji} {tariff['days']}дн | {tariff['price']}₽"
        
        regular_row.append(
            InlineKeyboardButton(
                button_text,
                callback_data=f'buy_tariff_{tariff["id"]}'
            )
        )
        
        # Если набрали 2 кнопки или это последний тариф
        if len(regular_row) == 2 or i == len(regular_tariffs) - 1:
            keyboard.append(regular_row)
            regular_row = []

    # Кнопки для специальных тарифов (только для админа)
    if special_tariffs and tg_id == 364224373:
        admin_row = []
        for tariff in special_tariffs:
            admin_row.append(
                InlineKeyboardButton(
                    f"🔧 {tariff['price']}₽",
                    callback_data=f'buy_tariff_{tariff["id"]}'
                )
            )
        keyboard.append(admin_row)

    # Кнопка "Назад" во всю ширину
    keyboard.append([InlineKeyboardButton("◀️ Вернуться в меню", callback_data='back_to_menu')])

    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.edit_message_text(
        text,
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )


def convert_to_local(dt: datetime, offset_hours: int = 9) -> str:
    """
    Конвертирует UTC datetime в локальное время и возвращает строку.
    
    :param dt: datetime в UTC
    :param offset_hours: смещение часового пояса (по умолчанию +9)
    :return: строка в формате "дд.мм.гггг чч:мм"
    """
    if dt is None:
        return "∞"
    return (dt + timedelta(hours=offset_hours)).strftime("%d.%m.%Y %H:%M")

async def show_configs(query):
    """Показать список конфигов пользователя"""
    tg_id = query.from_user.id
    keys = get_keys_by_tg_id(tg_id)

    if not keys:
        await show_no_configs_message(query)
        return

    # Фильтруем активные и истекшие
    active_keys = []
    expired_keys = []
    
    for key in keys:
        if not key["expires_at"] or key["expires_at"] > datetime.utcnow():
            active_keys.append(key)
        else:
            expired_keys.append(key)

    if not active_keys and not expired_keys:
        await show_no_configs_message(query)
        return

    # Формируем красивое сообщение со списком конфигов
    text = "🔐 **Ваши VPN конфиги**\n\n"

    print(active_keys)
    
    if active_keys:
        text += "✅ **Активные:**\n"
        for i, key in enumerate(active_keys, 1):
            expires_at = key["expires_at"]
            expires_text = convert_to_local(expires_at)
            
            # Определяем эмодзи для типа конфига
            config_emoji = "📱" if "vless" in key["vpn_type"] else "🖥"
            
            # Красивое отображение с псевдографикой
            prefix = "├─" if i < len(active_keys) else "└─"
            text += f"{prefix} {config_emoji} **{key['client_name']}**\n"
            text += f"{prefix}    ⏱ до: `{expires_text}`\n"
            
            config = key["config"] or ""
            if "vless" in config:
                text += f"{prefix}    🔗 VLESS\n"
            elif "trojan" in config:
                text += f"{prefix}    🛡 Trojan\n"
            elif "shadowsocks" in config:
                text += f"{prefix}    🌐 Shadowsocks\n"

            if i < len(active_keys):
                text += f"{prefix}  \n"  # Отступ между конфигами
    
    # if expired_keys:
    #     if active_keys:
    #         text += "\n"
    #     text += "❌ **Истекшие:**\n"
    #     for i, key in enumerate(expired_keys, 1):
    #         expires_at = key["expires_at"]
    #         expires_text = convert_to_local(expires_at)
            
    #         prefix = "├─" if i < len(expired_keys) else "└─"
    #         text += f"{prefix} 📱 {key['client_name']}\n"
    #         text += f"{prefix}    ⏱ истек: `{expires_text}`\n"
    
    text += "\n_Нажмите на конфиг ниже, чтобы показать QR-код и ссылку_ ⬇️"

    # Создаем кнопки для каждого активного конфига
    keyboard = []
    
    # Группируем активные конфиги по 2 в ряд
    active_row = []
    for i, key in enumerate(active_keys):
        # Короткое имя для кнопки (макс 15 символов)
        short_name = key['client_name'][:15] + "..." if len(key['client_name']) > 15 else key['client_name']
        
        

        # Эмодзи в зависимости от протокола
        config = key["config"] or ""
        if "vless" in config:
            emoji = "🔗"
        elif "trojan" in config:
            emoji = "🛡"
        else:
            emoji = "📱"
        
        button_text = f"{emoji} {short_name}"
        
        active_row.append(
            InlineKeyboardButton(
                button_text,
                callback_data=f'show_key_{key["client_name"]}'
            )
        )
        
        # Если набрали 2 кнопки или это последний конфиг
        if len(active_row) == 2 or i == len(active_keys) - 1:
            keyboard.append(active_row)
            active_row = []
    
    # Добавляем кнопки для истекших конфигов (если есть)
    # if expired_keys:
    #     expired_row = []
    #     for key in expired_keys[:2]:  # Максимум 2 истекших в ряд
    #         short_name = key['client_name'][:10] + "..." if len(key['client_name']) > 10 else key['client_name']
    #         expired_row.append(
    #             InlineKeyboardButton(
    #                 f"❌ {short_name}",
    #                 callback_data=f'renew_key_{key["client_name"]}'
    #             )
    #         )
    #     if expired_row:
    #         keyboard.append(expired_row)
        
        # Если больше 2 истекших, добавляем кнопку "Все истекшие"
        # if len(expired_keys) > 2:
        #     keyboard.append([
        #         InlineKeyboardButton(
        #             "🔄 Продлить все истекшие",
        #             callback_data="renew_all_expired"
        #         )
        #     ])
    
    # Кнопки управления
    control_row = []
    control_row.append(InlineKeyboardButton("🆕 Новый конфиг", callback_data="tariffs"))
    # control_row.append(InlineKeyboardButton("🔄 Обновить", callback_data="refresh_configs"))
    keyboard.append(control_row)
    
    # Кнопка "Назад"
    keyboard.append([InlineKeyboardButton("◀️ В главное меню", callback_data="back_to_menu")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)

    try:
        # Если сообщение текстовое
        await query.edit_message_text(text, reply_markup=reply_markup, parse_mode="Markdown")
    except Exception:
        try:
            # Если сообщение с фото/медиа — редактируем caption
            # await query.edit_message_caption(caption=text, reply_markup=reply_markup)
            # Если совсем не получается редактировать — удаляем и отправляем новое
            await query.message.delete()
            # await query.message.answer(text, reply_markup=reply_markup)
        except Exception:
            logger.error(f"Welcome message error")        
            # Кнопка назад
            await query.message.reply_text(
                "❌ Ошибка возвращения в стартовое меню. Попробуйте написать команду '/start' или нажать кнопку ниже.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("◀️ В меню", callback_data="back_to_menu")]
                ])
            )


async def show_single_config(query, client_name):
    """Показать конкретный конфиг с QR-кодом"""
    tg_id = query.from_user.id
    keys = get_keys_by_tg_id(tg_id)
    
    # Ищем нужный ключ
    key = next((k for k in keys if k["client_name"] == client_name), None)
    
    if not key:
        await query.answer("❌ Конфиг не найден", show_alert=True)
        return
    
    expires_at = key["expires_at"]
    expires_text = convert_to_local(expires_at)
    vless_link = key["config"]
    
    # Проверяем активность
    is_active = not expires_at or expires_at > datetime.utcnow()
    status_emoji = "✅" if is_active else "❌"
    status_text = "Активен" if is_active else "Истек"
    
    # Генерируем QR-код
    qr = qrcode.QRCode(version=1, box_size=8, border=4)
    qr.add_data(vless_link)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    
    bio = BytesIO()
    bio.name = "qr.png"
    img.save(bio, "PNG")
    bio.seek(0)
    
    # Определяем протокол
    protocol = "VLESS"
    if "trojan" in vless_link:
        protocol = "Trojan"
    elif "shadowsocks" in vless_link:
        protocol = "Shadowsocks"
    
    # Красивое сообщение с информацией
    caption = (
        f"🔐 **{status_emoji} Конфиг {key['client_name']}**\n\n"
        f"┌─ 📋 **Информация**\n"
        f"│  ▸ Протокол: **{protocol}**\n"
        f"│  ▸ Статус: **{status_text}**\n"
        f"│  ▸ Действует до: `{expires_text}`\n"
        f"└─ 🔧 **Ссылка для подключения:**\n"
        f"`{vless_link}`\n\n"
        "💡 _Скопируйте ссылку или сохраните QR-код_"
    )

    # Получаем inbound
    inbounds = xui.get_inbounds()
    if not inbounds:
        raise RuntimeError("Inbound не найден")
    
    inbound_id = inbounds[2]['id']

    # Кнопки под фото
    keyboard = [
        [
            # InlineKeyboardButton("🔄 Продлить", callback_data=f"renew_{client_name}_{inbound_id}")
        ],
        [
            InlineKeyboardButton("🔙 К списку", callback_data="my_configs")
        ]
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.message.reply_photo(
        photo=bio,
        caption=caption,
        reply_markup=reply_markup,
        parse_mode="Markdown"
    )

async def show_no_configs_message(query):
    """Показать сообщение об отсутствии конфигов"""
    text = (
        "❄️ **У вас пока нет активных конфигов**\n\n"
        "┌─────────────────────\n"
        "│ Чтобы получить доступ к VPN:\n"
        "│ 1️⃣ Выберите подходящий тариф\n"
        "│ 2️⃣ Оплатите удобным способом\n"
        "│ 3️⃣ Получите готовый конфиг\n"
        "└─────────────────────\n\n"
        "✨ **Преимущества:**\n"
        "• ⚡️ Высокая скорость\n"
        "• 🔒 Безопасное шифрование\n"
        "• 📱 До 10 устройств\n"
        "• 🌐 Доступ к любым сайтам\n\n"
        "👇 **Нажмите на кнопку ниже, чтобы выбрать тариф**"
    )
    
    keyboard = [
        [InlineKeyboardButton("🔥 Выбрать тариф", callback_data="tariffs")],
        [InlineKeyboardButton("◀️ В меню", callback_data="back_to_menu")]
    ]
    
    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )

# Добавьте обработчики callback_data
# @dp.callback_query(lambda c: c.data.startswith('show_key_'))
async def handle_show_key(callback_query):
    client_name = callback_query.data.replace('show_key_', '')
    await show_single_config(callback_query, client_name)

# @dp.callback_query(lambda c: c.data == 'refresh_configs')
async def handle_refresh_configs(callback_query):
    await callback_query.answer("🔄 Обновляю список...")
    await show_configs(callback_query)

# @dp.callback_query(lambda c: c.data.startswith('copy_'))
async def handle_copy_key(callback_query):
    # Здесь можно добавить логику копирования или просто показать ссылку
    await callback_query.answer("📋 Ссылка скопирована!", show_alert=False)
async def show_stats(query):
    tg_id = query.from_user.id

    client_email = get_user_email(tg_id)

    # Получаем статистику этого клиента
    stats = get_client_stats_by_email_api(client_email)

    """Показать статистику"""
    if not stats:
        await query.message.reply_text("❌ Статистика не найдена")
        return
    
    up = stats.get('up', 0)
    down = stats.get('down', 0)
    total = up + down
    enable = stats.get('enable', True)
    
    text = f"📊 **Ваша статистика**\n\n"
    text += f"👤 Клиент: `{client_email}`\n"
    text += f"Статус: {'✅ Активен' if enable else '❌ Отключен'}\n\n"
    text += f"⬆️ Отправлено: **{format_bytes(up)}**\n"
    text += f"⬇️ Получено: **{format_bytes(down)}**\n"
    text += f"📦 Всего: **{format_bytes(total)}**\n\n"
    
    # Если есть лимит трафика
    if 'total' in stats and stats['total'] > 0:
        limit = stats['total']
        used_percent = (total / limit) * 100 if limit > 0 else 0
        text += f"📊 Лимит: {format_bytes(limit)}\n"
        text += f"📈 Использовано: {used_percent:.1f}%\n\n"
        
        # Прогресс-бар
        progress = int(used_percent / 10)
        bar = "█" * progress + "░" * (10 - progress)
        text += f"[{bar}]\n\n"
    
    # Если есть срок действия
    if 'expiryTime' in stats and stats['expiryTime'] > 0:
        expiry = datetime.utcfromtimestamp(stats['expiryTime'] / 1000)
        days_left = (expiry - datetime.utcnow()).days
        
        text += f"⏰ Действует до: {convert_to_local(expiry)}\n"
        text += f"📅 Осталось дней: {days_left}\n"
    
    reply_markup=InlineKeyboardMarkup([
        [InlineKeyboardButton("◀️ В меню", callback_data="back_to_menu")]
    ])
    
    await query.edit_message_text(text, reply_markup=reply_markup, parse_mode='Markdown')

# Альтернативный метод через API 3X-UI
def get_client_stats_by_email_api(client_email):
    """Получить через прямой API запрос"""
    try:
        # Метод зависит от версии 3X-UI
        response = xui.session.post(
            f"http://{VLESS_DOMAIN}:51999/panel-3x-ui/panel/api/inbounds/clientStats",
            json={"email": client_email}
        )
        
        # response = xui.session.get(
        #     f"http://{VLESS_DOMAIN}:51999/panel-3x-ui/panel/api/inbounds/getClientTraffics/{client_email}",
        #     json={"email": client_email}
        # )

        
        print('yyyd', response.status_code, client_email)
        if response.status_code == 200:
            data = response.json()
            if data.get('success'):
                stats_list = data.get('obj', [])
                return next((s for s in stats_list if s.get('email') == client_email), None)
        
        return None
        
    except Exception as e:
        print(f"Ошибка API: {e}")
        return None


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
    
    # await query.edit_message_text(text, reply_markup=reply_markup, parse_mode='Markdown')
    await create_test_vless_config(query)
    

async def create_test_awg_config(query):
    """Создание тестового AmneziaWG конфига"""
    tg_id = query.from_user.id
    
    await query.edit_message_text("⏳ Создаю тестовый AmneziaWG конфиг...")
    print('🤜🏻 🤜🏻 🤜🏻 query', query)
    
    try:
        
        client_name = f"test-{tg_id}-{uuid.uuid4().hex[:8]}"
        
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

        
        payment_id = None
        client_public_key = None
        expiry_time = None
        
        # ===== 6. Сохранение в БД =====
        create_vpn_key(
            tg_id=tg_id,
            payment_id=payment_id,
            client_id=client_id,
            client_name=client_name,
            client_ip=client_ip,
            client_public_key=client_public_key,
            config=client_config,
            expires_at=expiry_time,
            vpn_type='awg'
        )

        logger.info("💾 VPN config saved to DB")
        
        # Отправляем конфиг файлом        
        config_file = BytesIO(client_config.encode('utf-8'))
        config_file.name = f'amneziawg_test_{tg_id}.conf'
        
        await query.message.reply_document(
            document=config_file,
            caption=f"🔵 **Тестовый AmneziaWG конфиг**\n\n"
                    f"👤 Клиент: `{client_name}`\n"
                    f"🌐 IP: `{client_ip}`\n"
                    f"⏱ Действителен: {TARIFFS['test_24h']['period']}\n"
                    f"📱 **Инструкция:**\n"
                    f"1. Установите [AmneziaVPN](https://amnezia.org)\n"
                    f"2. Импортируйте файл конфигурации\n"
                    f"3. Подключитесь\n\n"
                    f"💬 Поддержка: @al_v1k",
            parse_mode ='HTML'
        )

        set_awg_test_activated(tg_id)
        
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
    tg_id = query.from_user.id
    
    await query.edit_message_text("⏳ Создаю тестовый VLESS конфиг...")
    
    try:
        
        # Генерируем данные клиента
        client_email = f"test-{tg_id}-{uuid.uuid4().hex[:8]}"
        client_uuid = str(uuid.uuid4())
        
        # Время истечения: 24 часа
        expiry_time = int((time.time() + 86400) * 1000)
        
        # Получаем inbound
        inbounds = xui.get_inbounds()
        if not inbounds:
            raise RuntimeError("Inbound не найден")
        
        inbound_id = inbounds[2]['id']
        
        # Создаем клиента
        success = xui.add_client(
            inbound_id=inbound_id,
            email=client_email,
            tg_id=tg_id,
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
            client_id=client_uuid,
            domain=VLESS_DOMAIN,
            port=VLESS_PORT,
            path=VLESS_PATH,
            client_name=client_email,
            pbk=VLESS_PBK,
            sid=VLESS_SID,
            sni=VLESS_SNI,
            fp="chrome",
            spx="/"
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
        
        payment_id = None
        client_ip = None
        client_public_key = None
        expiry_time = datetime.now(timezone.utc) + timedelta(hours=TARIFFS['test_24h']['hours'])

        # ===== 6. Сохранение в БД =====
        create_vpn_key(
            tg_id=tg_id,
            payment_id=payment_id,
            client_id=client_uuid,
            client_name=client_email,
            client_ip=client_ip,
            client_public_key=client_public_key,
            config=vless_link,
            expires_at=expiry_time,
            vpn_type='vless'
        )

        logger.info("💾 VPN config saved to DB")
        
        # Отправляем QR и конфиг
        await query.message.reply_photo(
            photo=bio,
            caption=f"🟢 **Тестовый VLESS конфиг**\n\n"
                    f"👤 ID: {client_email}\n"
                    f"⏱ Действителен: {TARIFFS['test_24h']['period']}\n"
                    f"**Инструкция:**\n"
                    f"1. Установите приложение из раздела 'Инструкция' \n"
                    f"2. Отсканируйте QR или скопируйте ссылку\n"
                    f"3. Подключитесь\n\n"
                    f"💬 Поддержка: @al_v1k",
            parse_mode=None
        )
        
        # Отправка текста в безопасном code-блоке
        await query.message.reply_text(
            text=(
                f"🔑 Ключ-конфиг\n\n"
                f"<code>{vless_link}</code>\n\n"
                f"Скопируйте эту ссылку и вставьте в ваше приложение"
            ),
            parse_mode="HTML"
        )

        
        set_vless_test_activated(tg_id)
        
        # Кнопка назад
        await query.message.reply_text(
            "Выберите действие:",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📑 Инструкция и ссылки", callback_data="instructions")],
                [InlineKeyboardButton("◀️ В меню", callback_data="back_to_menu")]               
            ])
        )
        
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
    application = Application.builder().token(token).post_init(post_init).build()
    
    # Регистрируем обработчики
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(button_handler))
    application.add_handler(CommandHandler("send", send_to_user))
    application.add_handler(CommandHandler("broadcast", broadcast))
    
    # Запускаем бота
    logger.info("Bot started!")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()