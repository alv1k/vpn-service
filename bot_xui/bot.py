#!/usr/bin/env python3
"""
Точка входа бота. Здесь только:
  - инициализация зависимостей,
  - регистрация хэндлеров,
  - диспетчеризация callback_data.
Вся бизнес-логика вынесена в bot/views.py, bot/vpn_factory.py, bot/payment.py.
"""
import logging
import os
import sys
import pytz

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
from telegram import Update, BotCommand, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

from config import TELEGRAM_BOT_TOKEN, XUI_HOST, XUI_USERNAME, XUI_PASSWORD, REFERRAL_REWARD_DAYS, REFERRAL_NEWCOMER_DAYS, ADMIN_TG_ID, validate_config
from bot_xui.test_mode import is_test_mode, toggle_test_mode
from bot_xui.utils import XUIClient
from bot_xui.tariffs import TARIFFS
from api.db import (
    get_or_create_user,
    get_all_users_tg_ids,
    register_user_with_referral,
    get_referral_count,
    get_subscription_until,
    get_users_expiring_in_days,
)

from bot_xui.helpers  import make_main_keyboard, MAIN_MENU_TEXT
from bot_xui.views    import (
    show_main_menu, show_tariffs, show_configs,
    show_single_config, show_instructions, show_renew_tariffs,
)
from bot_xui.payment     import process_payment
from bot_xui.vpn_factory import handle_test_awg, handle_test_vless
from bot_xui.messaging   import send_message_by_tg_id
from apscheduler.schedulers.asyncio import AsyncIOScheduler

load_dotenv()

from log_config import setup_logging
setup_logging()
logger = logging.getLogger(__name__)

xui = XUIClient(XUI_HOST, XUI_USERNAME, XUI_PASSWORD)


# ──────────────────────────────────────────────────────────────────────────────
# Команды
# ──────────────────────────────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    tg_id = user.id
    first_name = user.first_name
    last_name = user.last_name

    args = context.args  # list of words after /start

    # Parse referral deep link: /start <referrer_tg_id>
    referrer_tg_id = None
    if args and args[0].isdigit():
        referrer_tg_id = int(args[0])

    referral_applied = register_user_with_referral(tg_id, referrer_tg_id, first_name, last_name)

    if referral_applied:
        await update.message.reply_text(
            "🎁 Вы перешли по реферальной ссылке!\n"
            f"Вам подарено <b>+{REFERRAL_NEWCOMER_DAYS} дня</b> подписки!\n"
            f"Ваш друг тоже получил <b>+{REFERRAL_REWARD_DAYS} дней</b>.",
            parse_mode="HTML"
        )
    else:
        # User already existed — just a normal /start
        get_or_create_user(tg_id, first_name, last_name)
        text = MAIN_MENU_TEXT
        if tg_id == ADMIN_TG_ID:
            mode = "🧪 ВКЛ" if is_test_mode() else "✅ ВЫКЛ"
            text += f"\n\n⚙️ Тестовый режим: <b>{mode}</b> (/testmode)"
        await update.message.reply_text(text, reply_markup=make_main_keyboard(), parse_mode="HTML")


async def refer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = await _refer_text(context, update.effective_user.id)
    await update.message.reply_text(text, parse_mode="HTML")


async def _refer_text(context, tg_id: int) -> str:
    """Возвращает текст реферальной страницы."""
    bot_info = await context.bot.get_me()
    link     = f"https://t.me/{bot_info.username}?start={tg_id}"

    count            = get_referral_count(tg_id)
    subscription     = get_subscription_until(tg_id)
    subscription_str = subscription.strftime("%d.%m.%Y") if subscription else "не активна"

    return (
        f"👥 <b>Реферальная программа</b>\n\n"
        f"Приглашайте друзей и получайте <b>+{REFERRAL_REWARD_DAYS} дней</b> подписки за каждого!\n\n"
        f"🔗 Ваша ссылка:\n<pre>{link}</pre>\n\n"
        f"📊 Приглашено друзей: <b>{count}</b>\n"
        + (f"🎁 Заработано дней: <b>{count * REFERRAL_REWARD_DAYS}</b>\n" if count else "")
        + f"📅 Подписка до: <b>{subscription_str}</b>"
    )



async def post_init(application):
    await application.bot.set_my_commands([
        BotCommand("start", "Начать взаимодействие с ботом"),
        BotCommand("refer", "Реферальная ссылка и статистика"),
    ])

    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        notify_expiring_subscriptions,
        # trigger="interval",  # ← для проверки
        # seconds=10,          # ← каждые 10 сек
        trigger="cron",
        hour=10,           # каждый день в 10:00
        minute=0,
        timezone=pytz.timezone("Asia/Tokyo"),
        args=[application.bot],
    )
    scheduler.start()
    logger.info("[NOTIFY] Subscription expiry scheduler started")


async def send_to_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда: /send <tg_id> <сообщение>"""
    if update.effective_user.id != ADMIN_TG_ID:
        await update.message.reply_text("❌ Нет доступа")
        return

    raw = update.message.text.split(maxsplit=2)
    if len(raw) < 3:
        await update.message.reply_text("Использование: /send <tg_id> <сообщение>")
        return

    try:
        tg_id = int(raw[1])
    except ValueError:
        await update.message.reply_text("❌ tg_id должен быть числом")
        return

    ok = await send_message_by_tg_id(tg_id, raw[2])
    await update.message.reply_text(
        "✅ Сообщение отправлено" if ok else "❌ Не удалось отправить"
    )


async def testmode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/testmode — переключить тестовый режим оплаты (только админ)."""
    if update.effective_user.id != ADMIN_TG_ID:
        await update.message.reply_text("❌ Нет доступа")
        return

    new_state = toggle_test_mode()
    if new_state:
        text = (
            "🧪 <b>Тестовый режим ВКЛЮЧЁН</b>\n\n"
            "Теперь твои платежи идут через тестовый магазин ЮKassa.\n"
            "Деньги не списываются.\n\n"
            "Тестовая карта: <code>1111 1111 1111 1026</code>\n"
            "Срок: любой будущий, CVC: любые 3 цифры\n\n"
            "Обычные пользователи платят через боевой магазин как обычно.\n\n"
            "Для отключения: /testmode"
        )
    else:
        text = (
            "✅ <b>Тестовый режим ВЫКЛЮЧЕН</b>\n\n"
            "Платежи идут через боевой магазин ЮKassa."
        )
    await update.message.reply_text(text, parse_mode="HTML")


async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/broadcast <сообщение> — рассылка всем."""
    if update.effective_user.id != ADMIN_TG_ID:
        await update.message.reply_text("❌ Нет доступа")
        return

    raw = update.message.text.split(maxsplit=1)
    if len(raw) < 2:
        await update.message.reply_text("Использование: /broadcast <сообщение>")
        return

    users    = get_all_users_tg_ids()
    ok = fail = 0
    for uid in users:
        if await send_message_by_tg_id(uid, raw[1]):
            ok += 1
        else:
            fail += 1

    await update.message.reply_text(f"📬 Рассылка завершена\n✅ {ok}\n❌ {fail}")


# ──────────────────────────────────────────────────────────────────────────────
# Главный диспетчер callback
# ──────────────────────────────────────────────────────────────────────────────

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data  = query.data

    if data == "my_configs":
        await show_configs(query)

    elif data == "tariffs":
        await show_tariffs(query)

    elif data == "back_to_menu":
        await show_main_menu(query)

    elif data == "instructions":
        await show_instructions(query)

    elif data == "test_awg":
        await handle_test_awg(query, xui)

    elif data == "test_vless":
        await handle_test_vless(query, xui)

    elif data.startswith("show_key_"):
        client_name = data.removeprefix("show_key_")
        await show_single_config(query, client_name, xui)

    elif data.startswith("buy_tariff_"):
        parts     = data.removeprefix("buy_tariff_")
        is_renew  = parts.endswith("_renew")
        tariff_id = parts.removesuffix("_renew")

        tariff = TARIFFS.get(tariff_id)
        if not tariff:
            await query.edit_message_text("❌ Тариф не найден")
            return

        if tariff.get("is_test"):
            await handle_test_vless(query, xui)
        else:
            renew_info = context.user_data.get("renew_info", {})
            await process_payment(
                query, tariff_id, "vless",
                is_renew=is_renew,
                client_name=renew_info.get("client_name"),
                inbound_id=renew_info.get("inbound_id"),
            )

    elif data.startswith("renew_"):
        parts       = data.removeprefix("renew_")
        client_name, inbound_id = parts.split("_", 1)
        await show_renew_tariffs(query, context, inbound_id, client_name)

    elif data == "referral":
        text = await _refer_text(context, query.from_user.id)
        await query.edit_message_text(
            text,
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⬅️ Назад", callback_data="back_to_menu")]
            ])
        )


# ──────────────────────────────────────────────────────────────────────────────
# Интервалы
# ──────────────────────────────────────────────────────────────────────────────

async def notify_expiring_subscriptions(bot):
    """Проверяет истекающие подписки и уведомляет пользователей."""
    for days, label in [(3, "3 дня"), (1, "1 день")]:
        users = get_users_expiring_in_days(days)
        for user in users:
            tg_id = user['tg_id']
            until = user['subscription_until'].strftime("%d.%m.%Y")
            try:
                await bot.send_message(
                    chat_id=tg_id,
                    text=(
                        f"⏳ <b>Подписка истекает через {label}!</b>\n\n"
                        f"📅 Дата окончания: <b>{until}</b>\n\n"
                        f"Продлите подписку, чтобы не потерять доступ к VPN."
                    ),
                    parse_mode="HTML",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("🔄 Продлить", callback_data="tariffs")]
                    ])
                )
                logger.info(f"[NOTIFY] Sent expiry warning ({days}d) to {tg_id}")
            except Exception as e:
                logger.warning(f"[NOTIFY] Failed to notify {tg_id}: {e}")

# ──────────────────────────────────────────────────────────────────────────────
# Запуск
# ──────────────────────────────────────────────────────────────────────────────

def main():
    validate_config()

    app = Application.builder().token(TELEGRAM_BOT_TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start",     start))
    app.add_handler(CommandHandler("refer",     refer))
    app.add_handler(CommandHandler("send",      send_to_user))
    app.add_handler(CommandHandler("testmode",  testmode))
    app.add_handler(CommandHandler("broadcast", broadcast))
    app.add_handler(CallbackQueryHandler(button_handler))

    logger.info("Bot started!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()