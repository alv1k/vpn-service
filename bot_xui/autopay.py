"""
Автоплатежи — списание с сохранённой карты при истечении подписки.

3-фазный процесс:
- Фаза 1: Уведомление за 24 часа (за 1 день до списания) с указанием времени целевого списания
- Фаза 2: Уведомление за 3 часа до списания (проверка баланса)
- Фаза 3: Списание (сообщение "Списываем...", выполнение платежа через ЮKassa, обновление статуса на "Успешно" или "Не удалось")
"""
import logging
import uuid
import asyncio

from yookassa import Configuration, Payment

from config import YOO_KASSA_SHOP_ID, YOO_KASSA_SECRET_KEY
from bot_xui.tariffs import TARIFFS
from api.db import (
    get_autopay_users_for_phase1, get_autopay_users_for_phase2,
    get_manual_users_for_phase1, get_manual_users_for_phase2,
    get_autopay_users_due, get_user_target_charge_time, log_autopay,
    create_payment, disable_autopay, disable_autopay_by_id, get_permanent_discount,
    get_payment_status, log_message_sent, execute_query
)

logger = logging.getLogger(__name__)


async def process_autopayments(bot):
    """
    Three-phase autopay process.
    """
    # Prevent concurrent runs (e.g. bot restart + cron overlap)
    import fcntl, os
    lock_path = "/tmp/tiin_autopay.lock"
    try:
        lock_fd = os.open(lock_path, os.O_CREAT | os.O_WRONLY)
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (OSError, IOError):
        logger.warning("[AUTOPAY] Another autopay process is running, skipping")
        return

    try:
        await _process_autopayments_locked(bot)
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)
        try: os.unlink(lock_path)
        except: pass


async def _process_autopayments_locked(bot):
    """Actual 3-phase autopay logic."""
    Configuration.account_id = YOO_KASSA_SHOP_ID
    Configuration.secret_key = YOO_KASSA_SECRET_KEY

    # ----------------------------------------------------
    # PHASE 1: Notice 24h before charge (~1 day before)
    # ----------------------------------------------------
    users_phase1 = get_autopay_users_for_phase1()
    if users_phase1:
        logger.info(f"[AUTOPAY] Phase 1: notifying {len(users_phase1)} users about upcoming charge tomorrow")
        for user in users_phase1:
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

            hour, minute = get_user_target_charge_time(user_id, tg_id)
            time_str = f"{hour:02d}:{minute:02d}"
            msg_text = (
                f"⏰ <b>Автопродление завтра</b>\n\n"
                f"📦 Тариф: {tariff['name']}\n"
                f"💰 Завтра в ~{time_str} будет списано: {price} ₽\n\n"
                f"Подписка истекает завтра. Автоматическое списание пройдет завтра в указанное время.\n\n"
                f"<i>Отключить автопродление: /autopay</i>"
            )

            if tg_id:
                try:
                    await bot.send_message(
                        chat_id=tg_id,
                        text=msg_text,
                        parse_mode="HTML",
                    )
                    log_message_sent(tg_id=tg_id, source="cron_autopay",
                                     scenario="autopay_phase1_reminder", message_text=msg_text, status='sent')
                except Exception as send_err:
                    err_str = str(send_err).lower()
                    is_block = "blocked" in err_str or "deactivated" in err_str
                    if is_block:
                        execute_query("UPDATE users SET bot_blocked = 1 WHERE tg_id = %s", (tg_id,))
                    logger.warning(f"[AUTOPAY] Phase 1 notify failed tg:{tg_id}: {send_err}")
                    log_message_sent(tg_id=tg_id, source="cron_autopay",
                                     scenario="autopay_phase1_reminder", message_text=msg_text,
                                     status='blocked' if is_block else 'failed',
                                     error_text=str(send_err)[:255])
            else:
                log_message_sent(tg_id=user_id, source="cron_autopay",
                                 scenario="autopay_phase1_reminder", message_text=msg_text, status='sent')

    # Non-autopay Phase 1: Notice 24h before subscription expiration
    manual_phase1 = get_manual_users_for_phase1()
    if manual_phase1:
        logger.info(f"[AUTOPAY] Phase 1 manual: notifying {len(manual_phase1)} users without autopay")
        for user in manual_phase1:
            tg_id = user.get('tg_id') or 0
            user_id = user['id']
            msg_text = (
                f"⏰ <b>Подписка истекает завтра</b>\n\n"
                f"Ваш VPN-доступ завершится завтра.\n"
                f"Продлите подписку, чтобы не потерять связь 🌐\n\n"
                f"<i>Продлить: /start</i>"
            )
            if tg_id:
                try:
                    await bot.send_message(
                        chat_id=tg_id,
                        text=msg_text,
                        parse_mode="HTML",
                    )
                    log_message_sent(tg_id=tg_id, source="cron_autopay",
                                     scenario="manual_phase1_reminder", message_text=msg_text, status='sent')
                except Exception as send_err:
                    err_str = str(send_err).lower()
                    is_block = "blocked" in err_str or "deactivated" in err_str
                    if is_block:
                        execute_query("UPDATE users SET bot_blocked = 1 WHERE tg_id = %s", (tg_id,))
                    logger.warning(f"[AUTOPAY] Phase 1 manual notify failed tg:{tg_id}: {send_err}")
                    log_message_sent(tg_id=tg_id, source="cron_autopay",
                                     scenario="manual_phase1_reminder", message_text=msg_text,
                                     status='blocked' if is_block else 'failed',
                                     error_text=str(send_err)[:255])
            else:
                log_message_sent(tg_id=user_id, source="cron_autopay",
                                 scenario="manual_phase1_reminder", message_text=msg_text, status='sent')

    # ----------------------------------------------------
    # PHASE 2: Notice 3 hours before charge
    # ----------------------------------------------------
    users_phase2 = get_autopay_users_for_phase2()
    if users_phase2:
        logger.info(f"[AUTOPAY] Phase 2: warning {len(users_phase2)} users 3 hours before charge")
        for user in users_phase2:
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

            hour, minute = get_user_target_charge_time(user_id, tg_id)
            time_str = f"{hour:02d}:{minute:02d}"
            msg_text = (
                f"⏳ <b>Автопродление подписки через 3 часа</b>\n\n"
                f"📦 Тариф: {tariff['name']}\n"
                f"💰 Скоро будет списано: {price} ₽\n\n"
                f"Списание произойдёт примерно в {time_str}. Пожалуйста, убедитесь, что на карте достаточно средств.\n\n"
                f"<i>Отключить автопродление: /autopay</i>"
            )

            if tg_id:
                try:
                    await bot.send_message(
                        chat_id=tg_id,
                        text=msg_text,
                        parse_mode="HTML",
                    )
                    log_message_sent(tg_id=tg_id, source="cron_autopay",
                                     scenario="autopay_phase2_warning", message_text=msg_text, status='sent')
                except Exception as send_err:
                    err_str = str(send_err).lower()
                    is_block = "blocked" in err_str or "deactivated" in err_str
                    if is_block:
                        execute_query("UPDATE users SET bot_blocked = 1 WHERE tg_id = %s", (tg_id,))
                    logger.warning(f"[AUTOPAY] Phase 2 notify failed tg:{tg_id}: {send_err}")
                    log_message_sent(tg_id=tg_id, source="cron_autopay",
                                     scenario="autopay_phase2_warning", message_text=msg_text,
                                     status='blocked' if is_block else 'failed',
                                     error_text=str(send_err)[:255])
            else:
                log_message_sent(tg_id=user_id, source="cron_autopay",
                                 scenario="autopay_phase2_warning", message_text=msg_text, status='sent')

    # Non-autopay Phase 2: Notice 3 hours before subscription expiration
    manual_phase2 = get_manual_users_for_phase2()
    if manual_phase2:
        logger.info(f"[AUTOPAY] Phase 2 manual: warning {len(manual_phase2)} users 3 hours before expiration")
        for user in manual_phase2:
            tg_id = user.get('tg_id') or 0
            user_id = user['id']
            msg_text = (
                f"⏳ <b>Подписка истекает через 3 часа</b>\n\n"
                f"Ваш VPN-доступ скоро отключится.\n"
                f"Продлите подписку прямо сейчас, чтобы продолжить пользоваться VPN 🌐\n\n"
                f"<i>Продлить: /start</i>"
            )
            if tg_id:
                try:
                    await bot.send_message(
                        chat_id=tg_id,
                        text=msg_text,
                        parse_mode="HTML",
                    )
                    log_message_sent(tg_id=tg_id, source="cron_autopay",
                                     scenario="manual_phase2_warning", message_text=msg_text, status='sent')
                except Exception as send_err:
                    err_str = str(send_err).lower()
                    is_block = "blocked" in err_str or "deactivated" in err_str
                    if is_block:
                        execute_query("UPDATE users SET bot_blocked = 1 WHERE tg_id = %s", (tg_id,))
                    logger.warning(f"[AUTOPAY] Phase 2 manual notify failed tg:{tg_id}: {send_err}")
                    log_message_sent(tg_id=tg_id, source="cron_autopay",
                                     scenario="manual_phase2_warning", message_text=msg_text,
                                     status='blocked' if is_block else 'failed',
                                     error_text=str(send_err)[:255])
            else:
                log_message_sent(tg_id=user_id, source="cron_autopay",
                                 scenario="manual_phase2_warning", message_text=msg_text, status='sent')

    # ----------------------------------------------------
    # PHASE 3: Actual Charge execution
    # ----------------------------------------------------
    users_charge = get_autopay_users_due(days_before=0)
    if users_charge:
        logger.info(f"[AUTOPAY] Phase 3: charging {len(users_charge)} users")
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

            sent_msg = None
            if tg_id:
                try:
                    sent_msg = await bot.send_message(
                        chat_id=tg_id,
                        text=(
                            f"🔄 <b>Списываем средства для продления...</b>\n\n"
                            f"📦 Тариф: {tariff['name']}\n"
                            f"💰 Сумма к оплате: {price} ₽\n\n"
                            f"Пожалуйста, подождите..."
                        ),
                        parse_mode="HTML",
                    )
                except Exception as send_err:
                    err_str = str(send_err).lower()
                    if "blocked" in err_str or "deactivated" in err_str:
                        execute_query("UPDATE users SET bot_blocked = 1 WHERE tg_id = %s", (tg_id,))

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
                real_status = get_payment_status(payment.id)
                if real_status in ("canceled", "paid"):
                    log_status = "canceled" if real_status == "canceled" else "pending"
                    log_autopay(tg_id, user_id, tariff_id, price, payment.id, log_status)
                    if real_status == "canceled":
                        disable_autopay(tg_id)
                        if sent_msg and tg_id:
                            fail_text = (
                                f"❌ <b>Оплата не удалась</b>\n\n"
                                f"Платёжная система отклонила списание {price} ₽ за тариф {tariff['name']}.\n"
                                f"Автопродление отключено."
                            )
                            try:
                                await bot.edit_message_text(
                                    chat_id=tg_id,
                                    message_id=sent_msg.message_id,
                                    text=fail_text,
                                    parse_mode="HTML",
                                )
                                log_message_sent(tg_id=tg_id, source="cron_autopay",
                                                 scenario="autopay_failed", message_text=fail_text, status='sent')
                            except Exception:
                                pass
                    continue

                log_autopay(tg_id, user_id, tariff_id, price, payment.id, "pending")

                if sent_msg and tg_id:
                    success_text = (
                        f"✅ <b>Оплата прошла успешно!</b>\n\n"
                        f"📦 Тариф: {tariff['name']}\n"
                        f"💰 Списано: {price} ₽\n\n"
                        f"Подписка успешно продлена. Спасибо, что вы с нами!"
                    )
                    try:
                        await bot.edit_message_text(
                            chat_id=tg_id,
                            message_id=sent_msg.message_id,
                            text=success_text,
                            parse_mode="HTML",
                        )
                        log_message_sent(tg_id=tg_id, source="cron_autopay",
                                         scenario="autopay_charge_success", message_text=success_text, status='sent')
                    except Exception:
                        pass

                logger.info(f"[AUTOPAY] Phase 3: charged tg:{tg_id}, payment {payment.id}")

            except Exception as e:
                logger.error(f"[AUTOPAY] Phase 3 failed for user {user_id}: {e}")
                log_autopay(tg_id, user_id, tariff_id, price, None, "failed", str(e)[:500])
                if tg_id:
                    disable_autopay(tg_id)
                else:
                    disable_autopay_by_id(user_id)

                if sent_msg and tg_id:
                    fail_text = (
                        f"❌ <b>Оплата не удалась</b>\n\n"
                        f"Не удалось списать {price} ₽ за тариф {tariff['name']}.\n"
                        f"Автопродление отключено.\n\n"
                        f"Вы можете продлить подписку вручную:"
                    )
                    try:
                        await bot.edit_message_text(
                            chat_id=tg_id,
                            message_id=sent_msg.message_id,
                            text=fail_text,
                            parse_mode="HTML",
                            reply_markup={"inline_keyboard": [[{"text": "💎 Тарифы", "callback_data": "tariffs"}]]},
                        )
                        log_message_sent(tg_id=tg_id, source="cron_autopay",
                                         scenario="autopay_failed", message_text=fail_text, status='sent')
                    except Exception as send_err:
                        err_str = str(send_err).lower()
                        if "blocked" in err_str or "deactivated" in err_str:
                            execute_query("UPDATE users SET bot_blocked = 1 WHERE tg_id = %s", (tg_id,))
                        log_message_sent(tg_id=tg_id, source="cron_autopay",
                                         scenario="autopay_failed", message_text=fail_text, status='failed',
                                         error_text=str(send_err)[:255])

    if not users_phase1 and not users_phase2 and not users_charge:
        logger.info("[AUTOPAY] No users due for phase 1, phase 2 or phase 3")

    logger.info("[AUTOPAY] 3-phase auto-renewal processing complete")

