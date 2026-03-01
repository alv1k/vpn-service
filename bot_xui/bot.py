#!/usr/bin/env python3
"""
Точка входа бота. Здесь только:
  - инициализация зависимостей,
  - регистрация хэндлеров,
  - диспетчеризация callback_data.
Вся бизнес-логика вынесена в bot/views.py, bot/vpn_factory.py, bot/payment.py.
"""
import logging
import sys

sys.path.insert(0, "/home/alvik/vpn-service")

from dotenv import load_dotenv
from telegram import Update, BotCommand
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

from config import TELEGRAM_BOT_TOKEN, XUI_HOST, XUI_USERNAME, XUI_PASSWORD
from bot_xui.utils import XUIClient
from bot_xui.tariffs import TARIFFS
from api.db import get_or_create_user, get_all_users_tg_ids

from bot_xui.helpers  import make_main_keyboard, MAIN_MENU_TEXT
from bot_xui.views    import (
    show_main_menu, show_tariffs, show_configs,
    show_single_config, show_instructions, show_renew_tariffs,
)
from bot_xui.payment     import process_payment
from bot_xui.vpn_factory import handle_test_awg, handle_test_vless
from bot_xui.messaging   import send_message_by_tg_id

load_dotenv()

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

ADMIN_TG_ID = 364224373

xui = XUIClient(XUI_HOST, XUI_USERNAME, XUI_PASSWORD)


# ──────────────────────────────────────────────────────────────────────────────
# Команды
# ──────────────────────────────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    get_or_create_user(update.effective_user.id)
    await update.message.reply_text(MAIN_MENU_TEXT, reply_markup=make_main_keyboard())


async def post_init(application):
    await application.bot.set_my_commands([
        BotCommand("start", "Начать взаимодействие с ботом"),
    ])


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
        f"✅ Сообщение отправлено" if ok else "❌ Не удалось отправить"
    )


async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/broadcast <сообщение> — рассылка всем."""
    if update.effective_user.id != ADMIN_TG_ID:
        await update.message.reply_text("❌ Нет доступа")
        return

    raw = update.message.text.split(maxsplit=1)
    if len(raw) < 2:
        await update.message.reply_text("Использование: /broadcast <сообщение>")
        return

    users  = get_all_users_tg_ids()
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
            # Тестовый тариф → сразу VLESS (AWG можно добавить как отдельную кнопку)
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


# ──────────────────────────────────────────────────────────────────────────────
# Запуск
# ──────────────────────────────────────────────────────────────────────────────

def main():
    if not TELEGRAM_BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN not found")
        return

    app = Application.builder().token(TELEGRAM_BOT_TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start",     start))
    app.add_handler(CommandHandler("send",      send_to_user))
    app.add_handler(CommandHandler("broadcast", broadcast))
    app.add_handler(CallbackQueryHandler(button_handler))

    logger.info("Bot started!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
