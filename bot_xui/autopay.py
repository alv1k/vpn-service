"""
Автоплатежи — списание с сохранённой карты при истечении подписки.

Запускается из scheduler в bot.py за 1 день до истечения.
"""
import logging
import uuid

from yookassa import Configuration, Payment

from config import YOO_KASSA_SHOP_ID, YOO_KASSA_SECRET_KEY
from bot_xui.tariffs import TARIFFS
from api.db import (
    get_autopay_users_due, log_autopay, create_payment,
    disable_autopay, disable_autopay_by_id, get_permanent_discount,
)

logger = logging.getLogger(__name__)


async def process_autopayments(bot):
    """
    Two-phase autopay:
    - Phase 1 (days_before=1): notify user that charge will happen tomorrow, create pending payment
    - Phase 2 (days_before=0): charge the saved payment method
    """
    Configuration.account_id = YOO_KASSA_SHOP_ID
    Configuration.secret_key = YOO_KASSA_SECRET_KEY

    # Phase 1: notify + create payment (subscription expires tomorrow)
    users_notify = get_autopay_users_due(days_before=1)
    if users_notify:
        logger.info(f"[AUTOPAY] Phase 1: notifying {len(users_notify)} users about upcoming charge")
        for user in users_notify:
            tg_id = user.get('tg_id') or 0
            user_id = user['id']
            tariff_id = user.get('autopay_tariff') or 'monthly_30d'
            tariff = TARIFFS.get(tariff_id)
            if not tariff or tariff.get('is_test'):
                logger.warning(f"[AUTOPAY] Invalid tariff {tariff_id} for user {user_id}, disabling autopay")
                disable_autopay_by_id(user_id)
                continue
            price = tariff['price']
            perm_discount = user.get('permanent_discount') or 0
            if perm_discount > 0:
                price = max(1, round(price * (100 - perm_discount) / 100))

            try:
                payment = Payment.create(
                    {
                        "amount": {"value": str(price), "currency": "RUB"},
                        "capture": True,
                        "payment_method_id": user['payment_method_id'],
                        "description": f"Автопродление тарифа {tariff['name']}",
                        "metadata": {
                            "tg_id": str(tg_id),
                            "tariff": tariff_id,
                            "vpn_type": user.get('autopay_vpn_type') or 'vless',
                            "is_renew": "true",
                            "is_autopayment": "true",
                        },
                    },
                    str(uuid.uuid4()),
                )
                create_payment(payment_id=payment.id, tg_id=tg_id, tariff=tariff_id, amount=price, status="pending")
                log_autopay(tg_id, user_id, tariff_id, price, payment.id, "pending")

                if tg_id:
                    await bot.send_message(
                        chat_id=tg_id,
                        text=(
                            f"⏰ <b>Автопродление завтра</b>\n\n"
                            f"📦 Тариф: {tariff['name']}\n"
                            f"💰 Завтра будет списано: {price} ₽\n\n"
                            f"Подписка истекает через 1 день. Автоматическое списание пройдёт завтра.\n\n"
                            f"<i>Отключить автопродление: /autopay</i>"
                        ),
                        parse_mode="HTML",
                    )
                logger.info(f"[AUTOPAY] Phase 1: notified tg:{tg_id}, payment {payment.id}")
            except Exception as e:
                logger.error(f"[AUTOPAY] Phase 1 failed for user {user_id}: {e}")
                log_autopay(tg_id, user_id, tariff_id, price, None, "failed", str(e)[:500])

    # Phase 2: charge users whose subscription expires today
    users_charge = get_autopay_users_due(days_before=0)
    if users_charge:
        logger.info(f"[AUTOPAY] Phase 2: charging {len(users_charge)} users")
        for user in users_charge:
            tg_id = user.get('tg_id') or 0
            user_id = user['id']
            tariff_id = user.get('autopay_tariff') or 'monthly_30d'
            tariff = TARIFFS.get(tariff_id)
            if not tariff or tariff.get('is_test'):
                continue
            price = tariff['price']
            perm_discount = user.get('permanent_discount') or 0
            if perm_discount > 0:
                price = max(1, round(price * (100 - perm_discount) / 100))

            try:
                payment = Payment.create(
                    {
                        "amount": {"value": str(price), "currency": "RUB"},
                        "capture": True,
                        "payment_method_id": user['payment_method_id'],
                        "description": f"Автопродление тарифа {tariff['name']}",
                        "metadata": {
                            "tg_id": str(tg_id),
                            "tariff": tariff_id,
                            "vpn_type": user.get('autopay_vpn_type') or 'vless',
                            "is_renew": "true",
                            "is_autopayment": "true",
                        },
                    },
                    str(uuid.uuid4()),
                )
                create_payment(payment_id=payment.id, tg_id=tg_id, tariff=tariff_id, amount=price, status="pending")
                log_autopay(tg_id, user_id, tariff_id, price, payment.id, "pending")

                if tg_id:
                    await bot.send_message(
                        chat_id=tg_id,
                        text=(
                            f"🔄 <b>Автопродление подписки</b>\n\n"
                            f"📦 Тариф: {tariff['name']}\n"
                            f"💰 Списано: {price} ₽\n\n"
                            f"Платёж обрабатывается. Конфиг обновится автоматически.\n\n"
                            f"<i>Отключить автопродление: /autopay</i>"
                        ),
                        parse_mode="HTML",
                    )
                logger.info(f"[AUTOPAY] Phase 2: charged tg:{tg_id}, payment {payment.id}")
            except Exception as e:
                logger.error(f"[AUTOPAY] Phase 2 failed for user {user_id}: {e}")
                log_autopay(tg_id, user_id, tariff_id, price, None, "failed", str(e)[:500])
                if tg_id:
                    disable_autopay(tg_id)
                else:
                    disable_autopay_by_id(user_id)
                if tg_id:
                    try:
                        await bot.send_message(
                            chat_id=tg_id,
                            text=(
                                f"❌ <b>Автопродление не удалось</b>\n\n"
                                f"Не удалось списать {price} ₽ за тариф {tariff['name']}.\n"
                                f"Автопродление отключено.\n\n"
                                f"Продлите подписку вручную:"
                            ),
                            parse_mode="HTML",
                            reply_markup={"inline_keyboard": [[{"text": "💎 Тарифы", "callback_data": "tariffs"}]]},
                        )
                    except Exception:
                        pass

    if not users_notify and not users_charge:
        logger.info("[AUTOPAY] No users due for auto-renewal")

    logger.info("[AUTOPAY] Auto-renewal processing complete")
