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
    Find users with autopay whose subscription expires within 1 day,
    charge their saved payment method.
    """
    users = get_autopay_users_due(days_before=1)
    if not users:
        logger.info("[AUTOPAY] No users due for auto-renewal")
        return

    logger.info(f"[AUTOPAY] Processing {len(users)} auto-renewals")

    Configuration.account_id = YOO_KASSA_SHOP_ID
    Configuration.secret_key = YOO_KASSA_SECRET_KEY

    for user in users:
        tg_id = user.get('tg_id') or 0
        user_id = user['id']
        pm_id = user['payment_method_id']
        tariff_id = user.get('autopay_tariff') or 'monthly_30d'
        vpn_type = user.get('autopay_vpn_type') or 'vless'

        tariff = TARIFFS.get(tariff_id)
        if not tariff or tariff.get('is_test'):
            logger.warning(f"[AUTOPAY] Invalid tariff {tariff_id} for user {user_id}, disabling")
            disable_autopay_by_id(user_id)
            continue

        # Apply permanent discount
        price = tariff['price']
        perm_discount = user.get('permanent_discount') or 0
        if perm_discount > 0:
            price = max(1, round(price * (100 - perm_discount) / 100))

        try:
            payment = Payment.create(
                {
                    "amount": {"value": str(price), "currency": "RUB"},
                    "capture": True,
                    "payment_method_id": pm_id,
                    "description": f"Автопродление тарифа {tariff['name']}",
                    "metadata": {
                        "tg_id": str(tg_id),
                        "tariff": tariff_id,
                        "vpn_type": vpn_type,
                        "is_renew": "true",
                        "is_autopayment": "true",
                    },
                },
                str(uuid.uuid4()),
            )

            create_payment(
                payment_id=payment.id,
                tg_id=tg_id,
                tariff=tariff_id,
                amount=price,
                status="pending",
            )

            log_autopay(tg_id, user_id, tariff_id, price, payment.id, "pending")
            logger.info(f"[AUTOPAY] Created payment {payment.id} for user {user_id} (tg:{tg_id}), {price} RUB")

            # Notify user about auto-charge
            if tg_id:
                try:
                    await bot.send_message(
                        chat_id=tg_id,
                        text=(
                            f"🔄 <b>Автопродление подписки</b>\n\n"
                            f"📦 Тариф: {tariff['name']}\n"
                            f"💰 Сумма: {price} ₽\n\n"
                            f"Платёж обрабатывается. Конфиг обновится автоматически.\n\n"
                            f"<i>Отключить автопродление: /autopay</i>"
                        ),
                        parse_mode="HTML",
                    )
                except Exception as e:
                    logger.warning(f"[AUTOPAY] Failed to notify tg:{tg_id}: {e}")

        except Exception as e:
            error_msg = str(e)[:500]
            logger.error(f"[AUTOPAY] Failed for user {user_id}: {error_msg}")
            log_autopay(tg_id, user_id, tariff_id, price, None, "failed", error_msg)

            # Disable autopay after failure
            if tg_id:
                disable_autopay(tg_id)
            else:
                disable_autopay_by_id(user_id)

            # Notify user about failure
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
                        reply_markup={
                            "inline_keyboard": [
                                [{"text": "💎 Тарифы", "callback_data": "tariffs"}]
                            ]
                        },
                    )
                except Exception as e:
                    logger.warning(f"[AUTOPAY] Failed to notify user {user_id} about autopay failure: {e}")

    logger.info("[AUTOPAY] Auto-renewal processing complete")
