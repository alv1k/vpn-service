"""
Логика создания платежей через YooKassa.
"""
import asyncio
import logging
import uuid

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from yookassa import Configuration, Payment

from config import (
    YOO_KASSA_SHOP_ID, YOO_KASSA_SECRET_KEY,
    YOO_KASSA_TEST_SHOP_ID, YOO_KASSA_TEST_SECRET_KEY,
    ADMIN_TG_ID,
)
from bot_xui.tariffs import TARIFFS
from bot_xui.test_mode import is_test_mode
from api.db import create_payment, use_promocode

logger = logging.getLogger(__name__)

_payment_lock = asyncio.Lock()


async def process_payment(
    query,
    tariff_id: str,
    vpn_type: str,
    is_renew: bool = False,
    client_name: str | None = None,
    inbound_id: int | None = None,
    promo: dict | None = None,
):
    """Создаёт платёж в YooKassa и отправляет пользователю ссылку."""
    user_id = query.from_user.id
    tariff  = TARIFFS.get(tariff_id)

    if not tariff:
        await query.edit_message_text("❌ Тариф не найден")
        return

    # Apply promo discount
    price = tariff["price"]
    promo_label = ""
    if promo and promo.get("value"):
        discount = promo["value"]
        price = max(1, round(price * (100 - discount) / 100))
        promo_label = f"\n🏷 Промокод <b>{promo['code']}</b>: скидка {discount}%"

    # Тестовый режим — только для админа
    use_test = (user_id == ADMIN_TG_ID and is_test_mode())

    try:
        async with _payment_lock:
            if use_test:
                Configuration.account_id = YOO_KASSA_TEST_SHOP_ID
                Configuration.secret_key = YOO_KASSA_TEST_SECRET_KEY
            else:
                Configuration.account_id = YOO_KASSA_SHOP_ID
                Configuration.secret_key = YOO_KASSA_SECRET_KEY

            payment = Payment.create(
                {
                    "amount":       {"value": str(price), "currency": "RUB"},
                    "confirmation": {"type": "redirect", "return_url": "https://t.me/tiin_service_bot"},
                    "capture":      True,
                    "description":  f"{'Продление' if is_renew else 'Оплата'} тарифа {tariff['name']}",
                    "metadata": {
                        "tg_id":       str(user_id),
                        "tariff":      tariff_id,
                        "vpn_type":    vpn_type,
                        "username":    query.from_user.username or "",
                        "is_renew":    "true" if is_renew else "false",
                        "client_name": client_name or "",
                        "inbound_id":  str(inbound_id) if inbound_id else "",
                        "test_mode":   "true" if use_test else "false",
                        "promo_code":  promo["code"] if promo else "",
                    },
                },
                str(uuid.uuid4()),  # idempotency key
            )

        if promo:
            use_promocode(promo["id"], user_id)

        create_payment(
            payment_id=payment.id,
            tg_id=user_id,
            tariff=tariff_id,
            amount=price,
            status="pending",
        )

        logger.info(f"Payment created: {payment.id} for user {user_id} (test={use_test})")

        back_btn = (
            InlineKeyboardButton("◀️ Назад в меню", callback_data="back_to_menu")
            if is_renew else
            InlineKeyboardButton("◀️ Назад к тарифам", callback_data="tariffs")
        )

        test_badge = "🧪 <b>ТЕСТОВЫЙ ПЛАТЁЖ</b> (деньги не списываются)\n\n" if use_test else ""
        price_line = f"💰 Сумма: <s>{tariff['price']} ₽</s> → <b>{price} ₽</b>" if promo else f"💰 Сумма: {price} ₽"
        text = (
            f"{test_badge}"
            f"💳 <b>Оплата тарифа {tariff['name']}</b>\n\n"
            f"{price_line}\n"
            f"⏱ Период: {tariff['period']}\n"
            f"👥 Устройств: {tariff['device_limit']}\n"
            f"{promo_label}\n\n"
            f"Нажмите кнопку для перехода к оплате.\n"
            f"После оплаты конфиг придёт автоматически."
        )

        await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("💳 Оплатить", url=payment.confirmation.confirmation_url)],
                [back_btn],
            ]),
            parse_mode="HTML",
        )

    except Exception as e:
        logger.error(f"Payment creation error: {e}")
        await query.message.reply_text(
            "❌ Ошибка создания платежа. Попробуйте позже.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("◀️ В меню", callback_data="back_to_menu")]
            ]),
        )
