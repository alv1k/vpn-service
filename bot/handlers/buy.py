from aiogram import Router, F
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from config import YOO_KASSA_SHOP_ID, YOO_KASSA_SECRET_KEY
from api.db import create_payment
from yookassa import Payment, Configuration
import uuid
import requests
import logging
from bot.tariffs import TARIFFS

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


router = Router()

Configuration.account_id = YOO_KASSA_SHOP_ID
Configuration.secret_key = YOO_KASSA_SECRET_KEY
YOOKASSA_URL = "https://api.yookassa.ru/v3/payments"

@router.callback_query(F.data.startswith("buy_"))
async def buy_handler(callback: CallbackQuery):
    await callback.answer()  # обязательно отвечаем Telegram

    # Получаем тариф из callback.data
    tg_id = callback.from_user.id
    tariff_id = callback.data.replace("buy_", "")
    tariff = TARIFFS.get(tariff_id)

    amount = tariff['price']
    
    try:
        # 2️⃣ Создаём платеж через YooKassa SDK
        payment = Payment.create(
            {
                "amount": {"value": str(amount), "currency": "RUB"},
                "confirmation": {"type": "redirect", "return_url": "https://t.me/tiin_service_bot"},
                "capture": True,
                "description": f"Оплата тарифа {tariff_id}",
                "metadata": {"tg_id": str(tg_id), "tariff": str(tariff_id)}
            },
            uuid.uuid4()
        )

        # 3️⃣ Сохраняем платеж в БД
        create_payment(payment.id, tg_id, tariff_id, amount, status="pending")
        payment_url = payment.confirmation.confirmation_url

    except Exception as e:
        # Если что-то пошло не так        
        await callback.answer(
            "❌ Не удалось создать платёж: {e}",
            show_alert=True
        )
        print("❌ Buy handler error:", e)
        return

    
    # Отправляем ссылку пользователю
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="💳 Перейти к оплате",
                    url=payment_url
                )
            ]
            # [
            #     InlineKeyboardButton(
            #         text="🔐 Получить VPN",
            #         callback_data="get_vpn"
            #     )
            # ]
        ]
    )

    await callback.message.answer(
        f"💳 *Оплата тарифа*\n\n"
        f"📦 {tariff['name']}\n"
        f"💰 {tariff['price']} ₽\n"
        f"⏳ {tariff['period']}\n\n",
        # "После оплаты нажмите *«Получить VPN»*.",
        reply_markup=kb,
        parse_mode="Markdown"
    )