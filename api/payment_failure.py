"""
Модуль обработки неудачных платежей и автоплатежей.
Изолирован от FastAPI и Pydantic, чтобы безопасно импортироваться как в api, так и в bot_xui.
"""
import json
import logging
import httpx
from config import TELEGRAM_BOT_TOKEN, ADMIN_TG_ID
from bot_xui.tariffs import TARIFFS
from api.db import (
    get_user_paid_payments_count,
    grant_grace_period,
    get_user_by_tg_id,
    log_message_sent,
)

logger = logging.getLogger(__name__)

TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"

YOOKASSA_REASONS_RU = {
    "3d_secure_failed": "Не пройдена аутентификация 3D-Secure (ошибка ввода СМС-кода или отмена)",
    "call_issuer": "Оплата отклонена банком, выпустившим карту (обратитесь в банк)",
    "card_expired": "Истёк срок действия банковской карты",
    "country_forbidden": "Оплата картой этой страны запрещена",
    "fraud_suspected": "Операция заблокирована системой антифрода банка",
    "general_decline": "Платёж отклонён банком без объяснения причин",
    "identification_required": "Требуется идентификация плательщика в банке",
    "insufficient_funds": "Недостаточно средств на счёте или карте",
    "invalid_card_number": "Неверный номер карты",
    "invalid_csc": "Неверно указан код CVC/CVV",
    "issuer_unavailable": "Банк-эмитент временно недоступен",
    "payment_method_limit_exceeded": "Превышен лимит операций по карте/кошельку",
    "payment_method_restricted": "Оплата заблокирована банком (включите онлайн-покупки в приложении банка)",
    "permission_revoked": "Пользователь отозвал разрешение на автосписание",
    "unsupported_mobile_operator": "Оператор связи не поддерживается",
    "expired_on_confirmation": "Истёк срок подтверждения оплаты",
}


async def send_telegram_notification(
    tg_id: int,
    message: str,
    buttons: list = None,
    source: str = "payment_failure",
    scenario: str = None,
):
    """Отправка уведомления в Telegram через HTTP API без зависимости от PTB/FastAPI."""
    if not tg_id:
        return

    data = {
        "chat_id": tg_id,
        "text": message,
        "parse_mode": "HTML",
    }
    if buttons:
        data["reply_markup"] = json.dumps({"inline_keyboard": buttons})

    async with httpx.AsyncClient(timeout=5) as client:
        try:
            response = await client.post(TELEGRAM_API, data=data)
            if response.status_code == 200:
                logger.info(f"📨 Notification sent to user: {tg_id}")
                try:
                    log_message_sent(
                        tg_id=tg_id,
                        source=source,
                        scenario=scenario,
                        message_text=message[:500] if message else None,
                        status="sent",
                    )
                except Exception:
                    pass
            else:
                logger.warning(f"⚠️ Telegram API returned {response.status_code}")
                try:
                    log_message_sent(
                        tg_id=tg_id,
                        source=source,
                        scenario=scenario,
                        message_text=message[:500] if message else None,
                        status="failed",
                        error_text=f"HTTP {response.status_code}"[:255],
                    )
                except Exception:
                    pass
        except Exception as e:
            logger.error(f"❌ Failed to send Telegram notification: {e}")
            try:
                log_message_sent(
                    tg_id=tg_id,
                    source=source,
                    scenario=scenario,
                    message_text=message[:500] if message else None,
                    status="failed",
                    error_text=str(e)[:255],
                )
            except Exception:
                pass


async def handle_failed_payment(
    payment_id: str,
    tg_id: int,
    tariff: str,
    amount: float,
    reason: str = "",
    party: str = "",
):
    """
    Smart payment failure handler:
    1. Checks if client is an existing customer (paid_count >= 1).
    2. Grants 12-hour grace period if eligible.
    3. Sends tailored smart error message with exact bank failure explanation.
    4. Provides 1-click fallback button for SBP / card payment.
    5. Sends high-priority Telegram alert to admin for regular customers (paid_count >= 2).
    """
    tariff_info = TARIFFS.get(tariff, {})
    tariff_name = tariff_info.get("name", tariff)
    try:
        amount_int = int(float(amount or tariff_info.get("price", 199)))
    except (ValueError, TypeError):
        amount_int = tariff_info.get("price", 199)

    paid_count = get_user_paid_payments_count(tg_id) if tg_id else 0
    grace_granted = False
    grace_expiry = None

    if tg_id and paid_count >= 1:
        grace_expiry = grant_grace_period(tg_id, hours=12)
        if grace_expiry:
            grace_granted = True

    # 1. Smart reason explanation
    if reason == "payment_method_restricted":
        reason_explanation = (
            "Банк заблокировал списание. Чаще всего в приложении банка (Т-Банк, Сбер) "
            "выключена опция <i>«Покупки в интернете»</i> или исчерпан лимит на онлайн-операции."
        )
    elif reason == "insufficient_funds":
        reason_explanation = (
            f"На привязанной карте недостаточно средств для списания {amount_int} ₽."
        )
    elif reason in ("expired_on_confirmation", "3d_secure_failed"):
        reason_explanation = (
            "Истёк срок ввода кода подтверждения (3D-Secure) или код был введён с ошибкой."
        )
    else:
        reason_ru = YOOKASSA_REASONS_RU.get(reason, reason or "Отклонено банком")
        reason_explanation = f"Платёж отклонён платёжной системой: <i>{reason_ru}</i>."

    time_str = grace_expiry.strftime("%H:%M") if grace_expiry else "12 часов"

    # 2. Build user message
    user_message = (
        f"❌ <b>Оплата не прошла</b>\n\n"
        f"📦 Тариф: <b>{tariff_name}</b> ({amount_int} ₽)\n"
        f"💳 <b>Причина:</b> {reason_explanation}\n\n"
    )
    if grace_granted:
        user_message += (
            f"🛡️ <b>Мы сохранили ваш доступ к VPN на 12 часов (до ~{time_str})</b>, "
            f"чтобы вы не оставались без связи и могли спокойно настроить карту.\n\n"
        )
    user_message += (
        f"<b>Как восстановить подписку:</b>\n"
        f"• Проверьте настройки карты в приложении банка, либо\n"
        f"• Оплатите удобным способом через <b>СБП</b> или другой картой по кнопке ниже 👇"
    )

    user_buttons = [
        [{"text": f"⚡ Продлить через СБП / Карту ({amount_int} ₽)", "callback_data": f"buy_tariff_{tariff}"}]
    ]

    if tg_id:
        await send_telegram_notification(
            tg_id,
            user_message,
            buttons=user_buttons,
            source="webhook",
            scenario="payment_canceled_smart",
        )

    # 3. High-priority admin alert for regular customers (paid_count >= 2)
    if paid_count >= 2 and ADMIN_TG_ID:
        u_data = get_user_by_tg_id(tg_id) if tg_id else None
        user_name = f"{u_data.get('first_name') or ''} {u_data.get('last_name') or ''}".strip() if u_data else ""

        reason_ru = YOOKASSA_REASONS_RU.get(reason, reason or "Не указана")
        admin_alert = (
            f"🚨 <b>Сбой автоплатежа у постоянного клиента!</b>\n\n"
            f"👤 Пользователь: <b>{user_name or 'Без имени'}</b> (<code>tg:{tg_id}</code>)\n"
            f"⭐ Успешных оплат в истории: <b>{paid_count}</b>\n"
            f"📦 Тариф: <b>{tariff_name}</b> ({amount_int} ₽)\n"
            f"❌ Причина отказа: <b>{reason_ru}</b> (<code>{reason or 'none'}</code>)\n"
        )
        if grace_granted:
            admin_alert += f"🛡️ Грейс-период: <b>Активирован на 12ч</b> (до ~{time_str})\n"
        else:
            admin_alert += "⚠️ Грейс-период: <i>Не активирован</i>\n"
        admin_alert += f"🆔 <code>{payment_id}</code>"

        admin_buttons = [
            [
                {"text": "🛡️ Продлить ещё на 12ч", "callback_data": f"admin_grace_12h_{tg_id}"},
                {"text": "👤 Написать клиенту", "url": f"tg://user?id={tg_id}"},
            ]
        ]
        await send_telegram_notification(
            ADMIN_TG_ID,
            admin_alert,
            buttons=admin_buttons,
            source="webhook",
            scenario="admin_regular_customer_failure_alert",
        )
