"""
Логика создания платежей через YooKassa.
"""
import asyncio
import logging
import time
import uuid
from datetime import datetime, timezone, timedelta

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, LabeledPrice
from yookassa import Configuration, Payment, Refund
from bot_xui.yookassa_client import async_payment_create, safe_sync_refund_create

from config import (
    YOO_KASSA_SHOP_ID, YOO_KASSA_SECRET_KEY,
    YOO_KASSA_TEST_SHOP_ID, YOO_KASSA_TEST_SECRET_KEY,
    ADMIN_TG_ID,
    XUI_HOST, XUI_USERNAME, XUI_PASSWORD,
)
from bot_xui.tariffs import TARIFFS
from bot_xui.test_mode import is_test_mode
from bot_xui.helpers import _log_message, safe_edit_text, safe_edit_text_logged, WEB_BASE_URL
from api.db import create_payment, get_permanent_discount

logger = logging.getLogger(__name__)

_payment_lock = asyncio.Lock()


async def show_tariff_info(
    query,
    tariff_id: str,
    vpn_type: str = "vless",
    is_renew: bool = False,
    client_name: str | None = None,
    inbound_id: int | None = None,
    promo: dict | None = None,
):
    """Показывает информацию о тарифе и кнопку 'Оплатить' (callback).
    Платёж НЕ создаётся — пользователь подтверждает выбор на следующем шаге."""
    user_id = query.from_user.id
    tariff = TARIFFS.get(tariff_id)

    if not tariff:
        await safe_edit_text(query, "❌ Тариф не найден")
        return

    price = tariff["price"]
    promo_label = ""
    perm_discount = get_permanent_discount(user_id)
    from api.db import has_winback_discount
    from config import WINBACK_DISCOUNT_PERCENT
    winback_discount = WINBACK_DISCOUNT_PERCENT if has_winback_discount(user_id) else 0
    onetime_discount = promo["value"] if promo and promo.get("value") else 0
    effective_discount = max(perm_discount, onetime_discount, winback_discount)

    if effective_discount > 0:
        price = max(1, round(price * (100 - effective_discount) / 100))
        if winback_discount >= perm_discount and winback_discount >= onetime_discount:
            promo_label = f"\n🏷 Персональная скидка: <b>20%</b>"
        elif onetime_discount >= perm_discount and promo:
            promo_label = f"\n🏷 Промокод <b>{promo['code']}</b>: скидка {effective_discount}%"
        else:
            promo_label = f"\n🏷 Постоянная скидка: <b>{perm_discount}%</b>"

    stars_base = tariff.get("stars_price", 30)
    stars_price = stars_base
    if effective_discount > 0:
        stars_price = max(1, round(stars_base * (100 - effective_discount) / 100))

    if effective_discount > 0:
        price_line = (
            f"💰 Карта РФ: <s>{tariff['price']} ₽</s> → <b>{price} ₽</b>\n"
            f"⭐️ Telegram Stars: <s>{stars_base} ⭐️</s> → <b>{stars_price} ⭐️</b>"
        )
    else:
        price_line = (
            f"💰 Карта РФ: <b>{price} ₽</b>\n"
            f"⭐️ Telegram Stars: <b>{stars_price} ⭐️</b>"
        )

    text = (
        f"💎 <b>Тариф {tariff['name']}</b>\n\n"
        f"{price_line}\n"
        f"⏱ Период: {tariff['period']}\n"
        f"👥 Устройств: {tariff['device_limit']}\n"
        f"{promo_label}\n"
        f"Выберите способ оплаты:\n"
        f"• <b>Карта РФ</b> — через ЮKassa\n"
        f"• <b>Telegram Stars</b> — моментально в приложении (для любых карт, Apple Pay, Google Pay)"
    )

    suffix = "_renew" if is_renew else ""
    callback_data = f"confirm_payment_{tariff_id}{suffix}"
    stars_callback_data = f"confirm_stars_{tariff_id}{suffix}"

    back_btn = (
        InlineKeyboardButton("◀️ Назад в меню", callback_data="back_to_menu")
        if is_renew
        else InlineKeyboardButton("◀️ Назад к тарифам", callback_data="tariffs")
    )

    await safe_edit_text_logged(query, text, "buy_tariff",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("💳 Оплатить картой РФ", callback_data=callback_data)],
            [InlineKeyboardButton(f"⭐️ Оплатить Stars ({stars_price} ⭐️)", callback_data=stars_callback_data)],
            [back_btn],
        ]),
    )


async def send_stars_invoice(
    query,
    context,
    tariff_id: str,
    vpn_type: str = "vless",
    is_renew: bool = False,
    client_name: str | None = None,
    inbound_id: int | None = None,
    promo: dict | None = None,
):
    """Отправляет нативный счет в Telegram Stars (валюта XTR)."""
    user_id = query.from_user.id
    tariff = TARIFFS.get(tariff_id)

    if not tariff:
        await safe_edit_text(query, "❌ Тариф не найден")
        return

    stars_price = tariff.get("stars_price", 30)
    perm_discount = get_permanent_discount(user_id)
    from api.db import has_winback_discount
    from config import WINBACK_DISCOUNT_PERCENT
    winback_discount = WINBACK_DISCOUNT_PERCENT if has_winback_discount(user_id) else 0
    onetime_discount = promo["value"] if promo and promo.get("value") else 0
    effective_discount = max(perm_discount, onetime_discount, winback_discount)

    if effective_discount > 0:
        stars_price = max(1, round(stars_price * (100 - effective_discount) / 100))

    # Payload format: stars:{tariff_id}:{renew|new}:{user_id}:{timestamp}
    payload = f"stars:{tariff_id}:{'renew' if is_renew else 'new'}:{user_id}:{int(time.time())}"

    prices = [LabeledPrice(label=f"Тариф {tariff['name']}", amount=stars_price)]

    description = f"Доступ к VPN на {tariff['period']} (до {tariff['device_limit']} устройств). Доступно по всему миру."
    if is_renew:
        description = f"Продление VPN на {tariff['period']}. Доступно по всему миру."

    try:
        await query.answer()
        await context.bot.send_invoice(
            chat_id=user_id,
            title=f"⭐️ {tariff['name']}",
            description=description,
            payload=payload,
            currency="XTR",
            prices=prices,
            provider_token="",
        )
        logger.info(f"Sent Stars invoice for user {user_id}: {stars_price} XTR (tariff: {tariff_id})")
    except Exception as e:
        logger.error(f"Failed to send Stars invoice for user {user_id}: {e}", exc_info=True)
        await query.message.reply_text(
            "❌ Не удалось сформировать счёт в Telegram Stars. Попробуйте позже или выберите оплату картой РФ.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("◀️ В меню", callback_data="back_to_menu")]
            ])
        )


async def process_successful_stars_payment(update, context):
    """
    Обработчик успешного платежа через Telegram Stars.
    Вызывается MessageHandler(filters.SUCCESSFUL_PAYMENT).
    """
    message = update.effective_message
    sp = message.successful_payment
    user = update.effective_user
    user_id = user.id
    payload = sp.invoice_payload or ""

    if not payload.startswith("stars:"):
        logger.warning(f"Unknown invoice payload received: {payload}")
        return

    parts = payload.split(":")
    tariff_id = parts[1] if len(parts) > 1 else "weekly_7d"
    is_renew = (parts[2] == "renew") if len(parts) > 2 else False

    total_stars = sp.total_amount
    charge_id = sp.telegram_payment_charge_id
    payment_id = f"stars_{charge_id}"

    logger.info(
        f"⭐ Telegram Stars payment confirmed: payment_id={payment_id} | "
        f"user={user_id} ({user.username or user.first_name}) | stars={total_stars} | tariff={tariff_id}"
    )

    tariff = TARIFFS.get(tariff_id)
    if not tariff:
        logger.error(f"Unknown tariff in Stars payment: {tariff_id}")
        tariff = TARIFFS.get("weekly_7d", {"days": 7, "name": "Неделя — 7 дней"})

    duration_days = tariff.get("days", 7)

    # 1. Записываем платеж в MySQL (payments)
    try:
        create_payment(
            payment_id=payment_id,
            tg_id=user_id,
            tariff=tariff_id,
            amount=total_stars,
            status="paid",
            is_test=False,
        )
    except Exception as e:
        logger.warning(f"Error logging Stars payment {payment_id} to DB: {e}")

    # 2. Активируем подписку в MySQL (users.subscription_until)
    subscription_until = None
    try:
        from subscriptions import activate_subscription
        subscription_until = activate_subscription(payment_id)
    except Exception as e:
        logger.error(f"activate_subscription error for {payment_id}: {e}")
        from api.db import get_subscription_until, upsert_user_subscription
        curr = get_subscription_until(user_id)
        now_utc = datetime.now(timezone.utc)
        base = curr if (curr and curr > now_utc) else now_utc
        subscription_until = base + timedelta(days=duration_days)
        upsert_user_subscription(tg_id=user_id, subscription_until=subscription_until)

    # 3. Синхронизируем и создаём/продлеваем VPN в 3x-ui (VLESS, Hysteria, RU node, Inbound 17 AWG)
    try:
        from bot_xui.utils import XUIClient
        from bot_xui.vpn_factory import create_xui_multi_config
        from bot_xui.multi_node import sync_client_to_ru_node
        from bot_xui.awg_manager import get_or_create_3xui_awg_client
        from api.db import sync_expiry

        xui = XUIClient(XUI_HOST, XUI_USERNAME, XUI_PASSWORD)
        sub_until_ms = int(subscription_until.replace(tzinfo=timezone.utc).timestamp() * 1000) if subscription_until.tzinfo is None else int(subscription_until.timestamp() * 1000)
        client_name = f"tiin_{user_id}"

        existing = xui.get_client_by_tg_id(user_id) or xui.get_client_by_email(client_name)
        if existing:
            xui.set_client_expiry(client_name, sub_until_ms, enable=True)
            logger.info(f"Stars: extended existing client {client_name} to {subscription_until}")
        else:
            await create_xui_multi_config(user_id, xui, days=duration_days)
            logger.info(f"Stars: created new multi-config for {user_id}")

        # Синхронизация на RU ноду Selectel SPB
        try:
            sync_client_to_ru_node(
                email=client_name,
                expiry_ms=sub_until_ms,
                enable=True,
                tg_id=user_id,
            )
        except Exception as _ru_err:
            logger.warning(f"Failed to sync Stars extension to RU node: {_ru_err}")

        # Синхронизация на Inbound 17 (AmneziaWG)
        try:
            get_or_create_3xui_awg_client(tg_id=user_id, expiry_ms=sub_until_ms, client_name=f"awg_{user_id}")
        except Exception as _awg_err:
            logger.warning(f"Failed to sync Stars extension to AWG: {_awg_err}")

        # Синхронизация даты истечения в таблице vpn_keys
        sync_expiry(user_id, subscription_until)

    except Exception as e:
        logger.error(f"Error configuring VPN after Stars payment: {e}", exc_info=True)

    # 4. Отправляем пользователю подтверждение и кнопки быстрого подключения
    try:
        from api.db import get_web_token
        web_token = get_web_token(user_id)
        connect_url = f"{WEB_BASE_URL}/go-connect/{web_token}" if web_token else ""

        buttons = []
        if connect_url:
            buttons.append([InlineKeyboardButton("⚡ Подключить в 1 клик", url=connect_url)])
        buttons.append([InlineKeyboardButton("🔑 Мои конфиги", callback_data="my_configs")])
        buttons.append([InlineKeyboardButton("◀️ Главное меню", callback_data="back_to_menu")])

        tz_msk = timezone(timedelta(hours=3))
        expiry_msk = subscription_until.astimezone(tz_msk).strftime("%d.%m.%Y %H:%M") if subscription_until else f"+{duration_days} дн."

        success_text = (
            f"🎉 <b>Оплата Telegram Stars успешно получена!</b>\n\n"
            f"⭐️ Списано: <b>{total_stars} ⭐️</b>\n"
            f"💎 Тариф: <b>{tariff['name']}</b>\n"
            f"⏳ Подписка активна до: <b>{expiry_msk} МСК</b>\n\n"
            f"Приятного пользования быстрым и надёжным интернетом! 🐿"
        )

        await message.reply_text(
            success_text,
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(buttons),
        )
    except Exception as e:
        logger.error(f"Error sending Stars success message to user {user_id}: {e}")



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
        await safe_edit_text(query, "❌ Тариф не найден")
        return

    # Apply discount (permanent, promo, or winback, whichever is higher)
    price = tariff["price"]
    promo_label = ""
    perm_discount = get_permanent_discount(user_id)
    from api.db import has_winback_discount
    from config import WINBACK_DISCOUNT_PERCENT
    winback_discount = WINBACK_DISCOUNT_PERCENT if has_winback_discount(user_id) else 0
    onetime_discount = promo["value"] if promo and promo.get("value") else 0
    effective_discount = max(perm_discount, onetime_discount, winback_discount)

    if effective_discount > 0:
        price = max(1, round(price * (100 - effective_discount) / 100))
        if winback_discount >= perm_discount and winback_discount >= onetime_discount:
            promo_label = f"\n🏷 Персональная скидка: <b>20%</b>"
        elif onetime_discount >= perm_discount and promo:
            promo_label = f"\n🏷 Промокод <b>{promo['code']}</b>: скидка {effective_discount}%"
        else:
            promo_label = f"\n🏷 Постоянная скидка: <b>{perm_discount}%</b>"

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

            payment = await async_payment_create(
                {
                    "amount":       {"value": str(price), "currency": "RUB"},
                    "confirmation": {"type": "redirect", "return_url": "https://t.me/tiin_service_bot"},
                    "capture":      True,
                    "save_payment_method": True,
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

        # Note: promo code is consumed in the webhook handler on successful payment

        create_payment(
            payment_id=payment.id,
            tg_id=user_id,
            tariff=tariff_id,
            amount=price,
            status="pending",
            is_test=use_test,
        )

        logger.info(f"Payment created: {payment.id} for user {user_id} (test={use_test})")

        back_btn = (
            InlineKeyboardButton("◀️ Назад в меню", callback_data="back_to_menu")
            if is_renew else
            InlineKeyboardButton("◀️ Назад к тарифам", callback_data="tariffs")
        )

        test_badge = "🧪 <b>ТЕСТОВЫЙ ПЛАТЁЖ</b> (деньги не списываются)\n\n" if use_test else ""
        price_line = f"💰 Сумма: <s>{tariff['price']} ₽</s> → <b>{price} ₽</b>" if effective_discount > 0 else f"💰 Сумма: {price} ₽"
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

        await safe_edit_text_logged(query, text, "buy_tariff",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("💳 Оплатить", url=payment.confirmation.confirmation_url)],
                [back_btn],
            ]),
        )

    except Exception as e:
        logger.error(f"Payment creation error: {e}")
        await query.message.reply_text(
            "❌ Ошибка создания платежа. Попробуйте позже.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("◀️ В меню", callback_data="back_to_menu")]
            ]),
        )


def create_yookassa_refund(payment_id: str, amount: str) -> bool:
    """Создаёт возврат платежа в YooKassa."""
    try:
        Configuration.account_id = YOO_KASSA_SHOP_ID
        Configuration.secret_key = YOO_KASSA_SECRET_KEY
        refund = safe_sync_refund_create({
            "payment_id": payment_id,
            "amount": {"value": amount, "currency": "RUB"},
        })
        logger.info(f"YooKassa refund created: {refund.id} for payment {payment_id}")
        return True
    except Exception as e:
        logger.error(f"YooKassa refund error for payment {payment_id}: {e}")
        return False
