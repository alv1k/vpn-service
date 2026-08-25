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
import io
import pytz
import qrcode
from pathlib import Path

START_IMAGE_PATH = Path(__file__).parent / "assets" / "no.png"
_START_IMAGE_FILE_ID: str | None = None

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
from telegram import Update, BotCommand, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.error import BadRequest, NetworkError
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

from config import TELEGRAM_BOT_TOKEN, XUI_HOST, XUI_USERNAME, XUI_PASSWORD, REFERRAL_REWARD_DAYS, REFERRAL_NEWCOMER_DAYS, ADMIN_TG_ID, validate_config
from bot_xui.test_mode import is_test_mode, toggle_test_mode
from bot_xui.utils import XUIClient
from bot_xui.tariffs import TARIFFS
from api.db import (
    get_or_create_user,
    get_all_users_tg_ids,
    get_all_users_with_web_token,
    get_active_subscribers_tg_ids,
    register_user_with_referral,
    get_referral_count,
    get_subscription_until,
    get_web_token,
    get_user_email,
    get_users_expiring_in_days,
    validate_promocode,
    use_promocode,
    create_promocode,
    deactivate_promocode,
    list_active_promocodes,
    set_permanent_discount,
)

from bot_xui.helpers  import (make_main_keyboard, MAIN_MENU_TEXT, MTPROTO_PROXY_LINK,
                              safe_edit_text, safe_edit_text_logged, make_proxy_file, make_qr_bytes,
                              log_and_reply_text, log_and_reply_photo, log_and_reply_document,
                              log_and_send_message, _get_tg_id, _log_message, WEB_BASE_URL)
from bot_xui.views    import (
    show_main_menu, show_tariffs, show_configs,
    show_single_config, show_renew_tariffs,
    build_main_menu_text,
    # show_vless_link,
)
from bot_xui.payment     import process_payment, show_tariff_info, create_yookassa_refund
from bot_xui.vpn_factory import handle_test_awg, handle_test_vless, handle_get_awg_config, handle_get_awg_config_v2, grant_referral_vpn, activate_test_period
from bot_xui.messaging   import send_message_by_tg_id
from bot_xui.receipt     import process_receipt_photo, receipt_callback_handler, init_finance_api
from apscheduler.schedulers.asyncio import AsyncIOScheduler

load_dotenv()

from log_config import setup_logging
setup_logging()
logger = logging.getLogger(__name__)

xui = XUIClient(XUI_HOST, XUI_USERNAME, XUI_PASSWORD)


async def send_start_screen(chat, text: str, reply_markup=None) -> None:
    """
    Шлёт фото `start command.png` с подписью `text` в чат.
    Кэширует file_id после первой отправки, чтобы не читать с диска повторно.
    Падает на чистый текст, если файла нет, caption > 1024 или Telegram отказал.
    """
    global _START_IMAGE_FILE_ID
    tg_id = chat.id if hasattr(chat, 'id') else None
    if not START_IMAGE_PATH.exists() or len(text) > 1024:
        result = await log_and_send_message(chat, text, reply_markup=reply_markup, parse_mode="HTML", scenario="start_screen")
        return
    try:
        if _START_IMAGE_FILE_ID:
            sent = await chat.send_photo(
                photo=_START_IMAGE_FILE_ID, caption=text,
                reply_markup=reply_markup, parse_mode="HTML",
            )
        else:
            with open(START_IMAGE_PATH, "rb") as f:
                sent = await chat.send_photo(
                    photo=f, caption=text,
                    reply_markup=reply_markup, parse_mode="HTML",
                )
            if sent.photo:
                _START_IMAGE_FILE_ID = sent.photo[-1].file_id
        if tg_id:
            await _log_message(tg_id, "bot", "start_screen", text)
    except Exception as e:
        logger.warning(f"send_start_screen photo failed, falling back to text: {e}")
        result = await log_and_send_message(chat, text, reply_markup=reply_markup, parse_mode="HTML", scenario="start_screen")


# ──────────────────────────────────────────────────────────────────────────────
# Команды
# ──────────────────────────────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    tg_id = user.id
    if not _bot_rate_check(tg_id):
        return
    first_name = user.first_name
    last_name = user.last_name

    args = context.args  # list of words after /start

    # Deep link: /start renew → go straight to tariffs
    if args and args[0] == "renew":
        from bot_xui.views import _build_tariff_text_and_keyboard
        register_user_with_referral(tg_id, None, first_name, last_name)
        text, markup = _build_tariff_text_and_keyboard(tg_id, mode="buy")
        await log_and_reply_text(update, text, reply_markup=markup, parse_mode="HTML")
        return

    # Parse referral deep link: /start <referrer_tg_id>
    referrer_tg_id = None
    if args and args[0].isdigit():
        referrer_tg_id = int(args[0])

    referral_applied = register_user_with_referral(tg_id, referrer_tg_id, first_name, last_name)

    if referral_applied:
        newcomer_result = await grant_referral_vpn(tg_id, REFERRAL_NEWCOMER_DAYS, xui)
        web_token = get_web_token(tg_id)
        qr_url = f"{WEB_BASE_URL}/my/{web_token}" if web_token else ""
        if newcomer_result and newcomer_result["action"] == "created" and qr_url:
            bio = make_qr_bytes(qr_url)
            await log_and_reply_photo(update,
                photo=bio,
                caption=(
                    f"🎁 Вы перешли по реферальной ссылке!\n"
                    f"Вам подарено <b>+{REFERRAL_NEWCOMER_DAYS} дня</b> VPN подписки!\n\n"
                    f'📲 <a href="https://344988.snk.wtf/my/{web_token}">Инструкция по подключению</a>'
                ),
                parse_mode="HTML",
                reply_markup=make_main_keyboard(tg_id)
            )
        else:
            await log_and_reply_text(update, 
                "🎁 Вы перешли по реферальной ссылке!\n"
                f"Вам подарено <b>+{REFERRAL_NEWCOMER_DAYS} дня</b> подписки!\n"
                f"Ваш друг тоже получил <b>+{REFERRAL_REWARD_DAYS} дней</b>.",
                parse_mode="HTML",
                reply_markup=make_main_keyboard(tg_id)
            )

        # Grant VPN to referrer (extend or create)
        await grant_referral_vpn(referrer_tg_id, REFERRAL_REWARD_DAYS, xui)

        # Notify referrer
        await send_message_by_tg_id(
            referrer_tg_id,
            f"🎉 По вашей реферальной ссылке зарегистрировался новый пользователь!\n"
            f"Вам начислено <b>+{REFERRAL_REWARD_DAYS} дней</b> VPN подписки.",
            parse_mode="HTML",
            bot=context.bot,
            source="bot_command", scenario="referral_notify",
        )
    else:
        get_or_create_user(tg_id, first_name, last_name)
        if referrer_tg_id:
            # User already registered but clicked a referral link
            await log_and_reply_text(update, 
                "Вы уже зарегистрированы — реферальная ссылка действует только для новых пользователей.\n\n"
                f"Но вы можете пригласить друзей и получить <b>+{REFERRAL_REWARD_DAYS} дня</b> подписки!",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton("💎 Тарифы", callback_data="tariffs"),
                        InlineKeyboardButton("👥 Пригласить", callback_data="referral"),
                    ],
                    [InlineKeyboardButton("◀️ В меню", callback_data="back_to_menu")],
                ])
            )
        else:
            # Обычный новый пользователь без рефералки
            logger.info(f"🐿 New user registered with TG ID {tg_id}")
            
            # Просто показываем приветствие без автоматической выдачи теста
            await send_start_screen(
                update.message.chat, 
                build_main_menu_text(tg_id), 
                make_main_keyboard(tg_id)
            )

async def test_xui_connection(xui: XUIClient) -> bool:
    """Проверяет соединение с XUI панелью"""
    try:
        # Попробуем получить список clients
        inbounds = await xui.get_inbounds()
        logger.info(f"XUI connection OK, inbounds: {len(inbounds)}")
        return True
    except Exception as e:
        logger.error(f"XUI connection failed: {e}")
        return False

async def refer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text, link = await _refer_text(context, update.effective_user.id)
    qr = _make_qr(link)
    await log_and_reply_photo(update, photo=qr, caption=text, parse_mode="HTML")


def _make_qr(data: str) -> io.BytesIO:
    """Генерирует QR-код и возвращает PNG в BytesIO. Deprecated: используйте make_qr_bytes."""
    return make_qr_bytes(data)


async def _refer_text(context, tg_id: int) -> tuple[str, str]:
    """Возвращает (текст реферальной страницы, ссылку)."""
    bot_info = await context.bot.get_me()
    link     = f"https://t.me/{bot_info.username}?start={tg_id}"

    count            = get_referral_count(tg_id)
    subscription     = get_subscription_until(tg_id)
    subscription_str = subscription.strftime("%d.%m.%Y") if subscription else "не активна"

    web_token = get_web_token(tg_id)
    web_link = f"https://344988.snk.wtf/?ref={web_token}" if web_token else None

    text = (
        f"👥 <b>Пригласите друга</b>\n\n"
        f"Получайте <b>+{REFERRAL_REWARD_DAYS} дней</b> подписки за каждого друга!\n\n"
        f"📱 <b>Ссылка для Telegram:</b>\n"
        f"<code>{link}</code>\n\n"
    )
    if web_link:
        text += (
            f"🌐 <b>Ссылка для сайта:</b>\n"
            f"<code>{web_link}</code>\n\n"
        )
    text += (
        f"Приглашено: <b>{count}</b>"
        + (f"  ·  Заработано: <b>{count * REFERRAL_REWARD_DAYS} дн.</b>" if count else "")
        + f"\n📅 Подписка до: <b>{subscription_str}</b>"
    )
    return text, link



async def post_init(application):
    await init_finance_api()
    await application.bot.set_my_commands([
        BotCommand("start", "Начать взаимодействие с ботом"),
        BotCommand("refer", "Реферальная ссылка и статистика"),
    ])

    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        notify_expiring_subscriptions,
        trigger="cron",
        hour=10,           # каждый день в 10:00
        minute=0,
        timezone=pytz.timezone("Asia/Tokyo"),
        args=[application.bot],
    )

    from bot_xui.autopay import process_autopayments
    scheduler.add_job(
        process_autopayments,
        trigger="cron",
        hour="*",          # каждый час для проверки 3 фаз автоплатежа
        minute=0,
        timezone=pytz.timezone("Asia/Tokyo"),
        args=[application.bot],
    )

    from bot_xui.sharing_monitor import cleanup_stale_ips
    scheduler.add_job(
        cleanup_stale_ips,
        trigger="interval",
        hours=1,
    )

    from api.db import cleanup_expired_sessions
    scheduler.add_job(
        cleanup_expired_sessions,
        trigger="cron",
        hour=4, minute=0,
        timezone=pytz.timezone("Asia/Tokyo"),
    )

    scheduler.start()
    logger.info("[NOTIFY] Subscription expiry + autopay + IP cleanup + session cleanup scheduler started")


async def send_to_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда: /send <tg_id> <сообщение>"""
    if update.effective_user.id != ADMIN_TG_ID:
        await log_and_reply_text(update, "❌ Нет доступа")
        return

    raw = update.message.text.split(maxsplit=2)
    if len(raw) < 3:
        await log_and_reply_text(update, "Использование: /send <tg_id> <сообщение>")
        return

    try:
        tg_id = int(raw[1])
    except ValueError:
        await log_and_reply_text(update, "❌ tg_id должен быть числом")
        return

    ok = await send_message_by_tg_id(tg_id, raw[2], bot=context.bot,
        source="admin_send")
    await log_and_reply_text(update, 
        "✅ Сообщение отправлено" if ok else "❌ Не удалось отправить"
    )


async def testmode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/testmode — переключить тестовый режим оплаты (только админ)."""
    if update.effective_user.id != ADMIN_TG_ID:
        await log_and_reply_text(update, "❌ Нет доступа")
        return

    new_state = toggle_test_mode()
    if new_state:
        text = (
            "🧪 <b>Тестовый режим ВКЛЮЧЁН</b>\n\n"
            "Теперь твои платежи идут через тестовый магазин ЮKassa.\n"
            "Деньги не списываются.\n\n"
            "Тестовая карта: <pre>1111 1111 1111 1026</pre>\n"
            "Срок: любой будущий, CVC: любые 3 цифры\n\n"
            "Обычные пользователи платят через боевой магазин как обычно.\n\n"
            "Для отключения: /testmode"
        )
    else:
        text = (
            "✅ <b>Тестовый режим ВЫКЛЮЧЕН</b>\n\n"
            "Платежи идут через боевой магазин ЮKassa."
        )
    await log_and_reply_text(update, text, parse_mode="HTML")


async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/broadcast <сообщение> — рассылка всем.
    Поддерживает HTML-разметку из Telegram-форматирования (жирный, курсив и т.д.).
    """
    if update.effective_user.id != ADMIN_TG_ID:
        await log_and_reply_text(update, "❌ Нет доступа")
        return

    raw = update.message.text.split(maxsplit=1)
    if len(raw) < 2:
        await log_and_reply_text(update, "Использование: /broadcast <сообщение>\n\nИспользуйте форматирование Telegram (жирный, курсив) — оно сохранится в рассылке.")
        return

    # text_html сохраняет форматирование (bold, italic и т.д.) как HTML-теги
    full_html = update.message.text_html
    # Убираем "/broadcast " из начала
    msg_html = full_html.split(maxsplit=1)[1] if len(full_html.split(maxsplit=1)) > 1 else raw[1]

    users    = get_all_users_tg_ids()
    ok = fail = 0
    for uid in users:
        if await send_message_by_tg_id(uid, msg_html, parse_mode="HTML", bot=context.bot,
                source="admin_broadcast"):
            ok += 1
        else:
            fail += 1

    await log_and_reply_text(update, f"📬 Рассылка завершена\n✅ {ok}\n❌ {fail}")


async def broadcast_ref(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/broadcast_ref — персональная рассылка с реферальной ссылкой на сайт."""
    if update.effective_user.id != ADMIN_TG_ID:
        await log_and_reply_text(update, "❌ Нет доступа")
        return

    users = get_all_users_with_web_token()
    if not users:
        await log_and_reply_text(update, "Нет пользователей с web_token")
        return

    await log_and_reply_text(update, f"📬 Начинаю рассылку {len(users)} пользователям...")

    ok = fail = 0
    for u in users:
        ref_link = f"https://344988.snk.wtf/?ref={u['web_token']}"
        msg = (
            "🚀 <b>VPN теперь доступен через сайт!</b>\n\n"
            "Теперь подключиться можно по email — без Telegram.\n\n"
            "📨 <b>Поделитесь с друзьями</b> — отправьте им вашу персональную ссылку:\n"
            f"<code>{ref_link}</code>\n\n"
            "🎁 <b>Акция 30 марта с 9:00 (Якутск):</b> первые 5 приглашённых "
            "получат <b>20 дней бесплатно</b> вместо 3!\n"
            "А вы — <b>10 дней</b> за каждого друга."
        )
        if await send_message_by_tg_id(u['tg_id'], msg, parse_mode="HTML", bot=context.bot,
                source="admin_broadcast"):
            ok += 1
        else:
            fail += 1

    await log_and_reply_text(update, f"📬 Рассылка завершена\n✅ {ok}\n❌ {fail}")


async def notify_sub_update(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/notify_sub_update — уведомить активных подписчиков об обновлении ссылки подписки."""
    if update.effective_user.id != ADMIN_TG_ID:
        await log_and_reply_text(update, "❌ Нет доступа")
        return

    text = (
        "📢 <b>Важное обновление!</b>\n\n"
        "Мы обновили конфигурацию VPN.\n\n"
        "Что нужно сделать:\n"
        "1. Откройте бота и нажмите <b>Мои конфиги</b>\n"
        "2. Скопируйте ссылку подписки\n"
        "3. Вставьте её в приложение <b>Shadowrocket</b>, <b>Happ</b> или <b>Hiddify</b>\n"
        "4. Если у вас уже была ссылка — удалите старую и добавьте новую\n\n"
        "После этого VPN заработает как обычно.\n"
        "По любым вопросам пишите в поддержку."
    )

    users = get_active_subscribers_tg_ids()
    ok = fail = 0
    for uid in users:
        if await send_message_by_tg_id(uid, text, parse_mode="HTML", bot=context.bot,
                source="admin_notify", scenario="sub_update"):
            ok += 1
        else:
            fail += 1

    await log_and_reply_text(update, f"📬 Уведомление отправлено активным подписчикам\n✅ {ok}\n❌ {fail}")


# ──────────────────────────────────────────────────────────────────────────────
# Промокоды
# ──────────────────────────────────────────────────────────────────────────────

async def promo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/promo <КОД> — активировать промокод."""
    tg_id = update.effective_user.id
    first_name = update.effective_user.first_name
    last_name = update.effective_user.last_name
    get_or_create_user(tg_id, first_name, last_name)

    if not context.args:
        await log_and_reply_text(update, 
            "📝 Отправьте команду в формате:\n"
            "<code>/promo ПРОМОКОД</code>\n\n"
            "Например: <code>/promo WIN123456_ABCDEF</code>",
            parse_mode="HTML",
        )
        return

    code = context.args[0]
    promo_data, error = validate_promocode(code, tg_id)

    if error:
        await log_and_reply_text(update, f"❌ {error}")
        return

    if promo_data['type'] == 'days':
        result = await grant_referral_vpn(tg_id, promo_data['value'], xui)
        if not result:
            await log_and_reply_text(update, "❌ Ошибка активации промокода. Попробуйте позже.")
            return

        use_promocode(promo_data['id'], tg_id)

        if result["action"] == "created":
            web_token = get_web_token(tg_id)
            qr_url = f"{WEB_BASE_URL}/my/{web_token}" if web_token else ""
            if qr_url:
                bio = make_qr_bytes(qr_url)
                await log_and_reply_photo(update,
                    photo=bio,
                    caption=(
                        f"🎉 Промокод <b>{code.upper()}</b> активирован!\n"
                        f"Вам подарено <b>+{promo_data['value']} дней</b> VPN подписки!\n\n"
                        f'📲 <a href="https://344988.snk.wtf/my/{web_token}">Инструкция по подключению</a>'
                    ),
                    parse_mode="HTML",
                    reply_markup=make_main_keyboard(tg_id)
                )
        else:
            await log_and_reply_text(update, 
                f"🎉 Промокод <b>{code.upper()}</b> активирован!\n"
                f"Вам начислено <b>+{promo_data['value']} дней</b> VPN подписки.",
                parse_mode="HTML",
                reply_markup=make_main_keyboard(tg_id)
            )

    elif promo_data['type'] in ('discount', 'winback_discount'):
        # Track activation for win-back follow-up
        from api.db import log_promo_activation
        log_promo_activation(promo_data['id'], tg_id)
        context.user_data["promo"] = {
            "id": promo_data['id'],
            "code": promo_data['code'],
            "value": promo_data['value'],
        }
        await log_and_reply_text(update, 
            f"🎉 Промокод <b>{code.upper()}</b> применён!\n"
            f"Скидка <b>{promo_data['value']}%</b> будет применена к следующей оплате.\n\n"
            f"Выберите тариф:",
            parse_mode="HTML",
            reply_markup=make_main_keyboard(tg_id)
        )

    elif promo_data['type'] == 'permanent_discount':
        set_permanent_discount(tg_id, promo_data['value'])
        use_promocode(promo_data['id'], tg_id)
        await log_and_reply_text(update, 
            f"🎉 Промокод <b>{code.upper()}</b> активирован!\n"
            f"Вам установлена постоянная скидка <b>{promo_data['value']}%</b> на все будущие оплаты.",
            parse_mode="HTML",
            reply_markup=make_main_keyboard(tg_id)
        )

    elif promo_data['type'] == 'loyalty_bonus':
        result = await grant_referral_vpn(tg_id, promo_data['value'], xui)
        if not result:
            await log_and_reply_text(update, "❌ Ошибка активации промокода. Попробуйте позже.")
            return
        use_promocode(promo_data['id'], tg_id)
        await log_and_reply_text(update, 
            f"🎉 Промокод <b>{code.upper()}</b> активирован!\n"
            f"Вам начислено <b>+{promo_data['value']} дней</b> бонуса за лояльность!\n\n"
            f"Спасибо, что остаётесь с нами! 💙",
            parse_mode="HTML",
            reply_markup=make_main_keyboard(tg_id)
        )

    elif promo_data['type'] == 'holiday':
        result = await grant_referral_vpn(tg_id, promo_data['value'], xui)
        if not result:
            await log_and_reply_text(update, "❌ Ошибка активации промокода. Попробуйте позже.")
            return
        use_promocode(promo_data['id'], tg_id)
        if result["action"] == "created":
            web_token = get_web_token(tg_id)
            qr_url = f"{WEB_BASE_URL}/my/{web_token}" if web_token else ""
            if qr_url:
                bio = make_qr_bytes(qr_url)
                await log_and_reply_photo(update,
                    photo=bio,
                    caption=(
                        f"🎉 Праздничный промокод <b>{code.upper()}</b> активирован!\n"
                        f"Вам подарено <b>+{promo_data['value']} дней</b> VPN!\n\n"
                        f'📲 <a href="https://344988.snk.wtf/my/{web_token}">Инструкция по подключению</a>'
                    ),
                    parse_mode="HTML",
                    reply_markup=make_main_keyboard(tg_id)
                )
        else:
            await log_and_reply_text(update, 
                f"🎉 Праздничный промокод <b>{code.upper()}</b> активирован!\n"
                f"Вам начислено <b>+{promo_data['value']} дней</b> VPN!\n\n"
                f"С праздником! 🎄",
                parse_mode="HTML",
                reply_markup=make_main_keyboard(tg_id)
            )

    elif promo_data['type'] == 'referral_boost':
        use_promocode(promo_data['id'], tg_id)
        await log_and_reply_text(update, 
            f"🎉 Промокод <b>{code.upper()}</b> активирован!\n"
            f"Теперь за каждого приглашённого друга вы получите "
            f"<b>+{promo_data['value']} дней</b> вместо стандартных +10!\n\n"
            f"Действует на следующие 5 рефералов.",
            parse_mode="HTML",
            reply_markup=make_main_keyboard(tg_id)
        )


async def addpromo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/addpromo CODE days|discount|permanent_discount VALUE [MAX_USES] [EXPIRES YYYY-MM-DD]"""
    if update.effective_user.id != ADMIN_TG_ID:
        await log_and_reply_text(update, "❌ Нет доступа")
        return

    args = context.args
    if not args or len(args) < 3:
        await log_and_reply_text(update, 
            "Использование:\n"
            "<pre>/addpromo CODE days 7</pre>\n"
            "<pre>/addpromo CODE discount 50 100 2026-04-01</pre>\n\n"
            "Параметры: КОД тип значение [макс_использований] [дата_истечения]",
            parse_mode="HTML"
        )
        return

    code = args[0]
    promo_type = args[1]
    allowed_types = ('days', 'discount', 'permanent_discount', 'winback_discount', 'loyalty_bonus', 'holiday', 'referral_boost')
    if promo_type not in allowed_types:
        await log_and_reply_text(update, f"❌ Тип должен быть одним из: {', '.join(f'<pre>{t}</pre>' for t in allowed_types)}", parse_mode="HTML")
        return

    try:
        value = int(args[2])
    except ValueError:
        await log_and_reply_text(update, "❌ Значение должно быть числом")
        return

    max_uses = None
    expires_at = None
    if len(args) >= 4:
        try:
            max_uses = int(args[3])
        except ValueError:
            await log_and_reply_text(update, "❌ Макс. использований должно быть числом")
            return
    if len(args) >= 5:
        expires_at = args[4]

    try:
        create_promocode(code, promo_type, value, max_uses=max_uses, expires_at=expires_at)
    except Exception as e:
        await log_and_reply_text(update, f"❌ Ошибка: {e}")
        return

    await log_and_reply_text(update, 
        f"✅ Промокод <b>{code.upper()}</b> создан\n"
        f"Тип: <b>{promo_type}</b>, значение: <b>{value}</b>"
        + (f", лимит: <b>{max_uses}</b>" if max_uses else "")
        + (f", до: <b>{expires_at}</b>" if expires_at else ""),
        parse_mode="HTML"
    )


async def delpromo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/delpromo CODE — деактивировать промокод."""
    if update.effective_user.id != ADMIN_TG_ID:
        await log_and_reply_text(update, "❌ Нет доступа")
        return

    if not context.args:
        await log_and_reply_text(update, "Использование: /delpromo <КОД>")
        return

    code = context.args[0]
    deactivate_promocode(code)
    await log_and_reply_text(update, f"✅ Промокод <b>{code.upper()}</b> деактивирован", parse_mode="HTML")


async def promos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/promos — список активных промокодов."""
    if update.effective_user.id != ADMIN_TG_ID:
        await log_and_reply_text(update, "❌ Нет доступа")
        return

    items = list_active_promocodes()
    if not items:
        await log_and_reply_text(update, "Нет активных промокодов")
        return

    text = "📋 <b>Активные промокоды:</b>\n\n"
    for p in items:
        uses = f"{p['used_count']}/{p['max_uses']}" if p['max_uses'] else f"{p['used_count']}/∞"
        exp = p['expires_at'].strftime("%d.%m.%Y") if p['expires_at'] else "∞"
        text += (
            f"<pre>{p['code']}</pre> — {p['type']} <b>{p['value']}</b>"
            f" | исп: {uses} | до: {exp}\n"
        )

    await log_and_reply_text(update, text, parse_mode="HTML")


# ──────────────────────────────────────────────────────────────────────────────
# Обратная связь
# ──────────────────────────────────────────────────────────────────────────────

WAITING_FEEDBACK: dict[int, float] = {}  # tg_id -> timestamp when feedback was requested
_FEEDBACK_TIMEOUT = 600  # 10 minutes



# Bot command rate limiter: max 10 actions per 30 seconds per user
import time as _time
_bot_rate: dict[int, list[float]] = {}
_BOT_RATE_LIMIT = 10
_BOT_RATE_WINDOW = 30


def _bot_rate_check(tg_id: int) -> bool:
    """Returns True if allowed, False if rate-limited."""
    now = _time.time()
    bucket = _bot_rate.get(tg_id, [])
    bucket = [t for t in bucket if now - t < _BOT_RATE_WINDOW]
    if len(bucket) >= _BOT_RATE_LIMIT:
        _bot_rate[tg_id] = bucket
        return False
    bucket.append(now)
    _bot_rate[tg_id] = bucket
    # Periodic cleanup of stale entries
    if len(_bot_rate) > 500:
        for k in list(_bot_rate.keys()):
            _bot_rate[k] = [t for t in _bot_rate[k] if now - t < _BOT_RATE_WINDOW]
            if not _bot_rate[k]:
                del _bot_rate[k]
    return True


async def handle_feedback_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка текстового сообщения от пользователя в режиме обратной связи."""
    if not update.effective_user:
        logger.warning(f"handle_feedback_message: update.effective_user is None, message_id={update.message.message_id if update.message else '?'}")
        return
    tg_id = update.effective_user.id

    # Ответ админа на пересланное сообщение или тикет с веб-портала
    if tg_id == ADMIN_TG_ID and update.message.reply_to_message:
        reply_text = update.message.reply_to_message.text or ""
        import re
        ticket_match = re.search(r"[Тт]икет #(\d+)", reply_text, re.IGNORECASE)
        if ticket_match:
            try:
                ticket_id = int(ticket_match.group(1))
                from api.db import execute_query
                ticket = execute_query(
                    "SELECT id, web_token, tg_id, user_message FROM web_support_tickets WHERE id = %s",
                    (ticket_id,), fetch='one'
                )
                if ticket:
                    execute_query(
                        "UPDATE web_support_tickets SET admin_reply = %s, status = 'answered', replied_at = NOW() WHERE id = %s",
                        (update.message.text, ticket_id)
                    )
                    # Если у пользователя есть telegram, также шлем копию в бот
                    if ticket.get("tg_id"):
                        try:
                            await send_message_by_tg_id(
                                ticket["tg_id"],
                                f"💬 <b>Ответ поддержки на обращение:</b>\n\n{update.message.text}",
                                parse_mode="HTML",
                                bot=context.bot,
                                source="admin_send", scenario="ticket_reply",
                            )
                        except Exception:
                            pass

                    await log_and_reply_text(
                        update,
                        f"✅ Ответ на <b>тикет #{ticket_id}</b> сохранён и отображён на веб-странице пользователя.",
                        parse_mode="HTML"
                    )
                    return
            except Exception as e:
                logger.error(f"Error handling admin ticket reply: {e}")

        # Извлекаем tg_id из пересланного сообщения (стандартный feedback бота)
        if "ID:" in reply_text:
            try:
                target_id = int(reply_text.split("ID:")[1].split(")")[0].strip())
                await send_message_by_tg_id(
                    target_id,
                    f"💬 <b>Ответ от поддержки:</b>\n\n{update.message.text}",
                    parse_mode="HTML",
                    bot=context.bot,
                    source="admin_send", scenario="support_reply",
                )
                await log_and_reply_text(update, "✅ Ответ отправлен")
                return
            except (ValueError, IndexError):
                pass

    import time as _time

    # Логируем ЛЮБОЕ входящее текстовое сообщение пользователя в message_log
    if update.message and update.message.text:
        try:
            from api.db import log_message_sent
            log_message_sent(
                tg_id=tg_id,
                source="user_message",
                scenario="incoming_text",
                message_text=update.message.text,
                status="sent"
            )
        except Exception as e:
            logger.warning(f"Failed to log user message: {e}")

    # Очищаем флаг ожидания если он был
    WAITING_FEEDBACK.pop(tg_id, None)

    user = update.effective_user
    name = user.first_name or ""
    if user.last_name:
        name += f" {user.last_name}"
    username = f"@{user.username}" if user.username else "нет"

    admin_text = (
        f"✉️ <b>Сообщение от пользователя</b>\n\n"
        f"👤 {name} ({username}, ID: {tg_id})\n\n"
        f"💬 {update.message.text}\n\n"
        f"<i>Ответьте на это сообщение, чтобы ответить пользователю</i>"
    )

    await send_message_by_tg_id(
        ADMIN_TG_ID, admin_text, parse_mode="HTML", bot=context.bot,
        source="bot_command", scenario="support_forward",
    )

    await log_and_reply_text(update, 
        "✅ Ваше сообщение отправлено! Мы ответим в ближайшее время.",
        reply_markup=make_main_keyboard(tg_id),
        parse_mode="HTML",
    )


# ──────────────────────────────────────────────────────────────────────────────
# Главный диспетчер callback
# ──────────────────────────────────────────────────────────────────────────────

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not _bot_rate_check(query.from_user.id):
        await safe_edit_text(query, "⏳ Слишком много запросов. Подождите немного.")
        return
    data  = query.data

    if data == "req_new_awg_conf":
        tg_id = query.from_user.id
        from api.db import log_message_sent
        reply_text = (
            "✅ <b>Запрос принят!</b>\n\n"
            "Мы готовим для вас новые файлы конфигурации и отправим их в этот чат в ближайшее время."
        )
        await safe_edit_text_logged(query, reply_text, "support_awg_conf_requested")
        try:
            alert_text = (
                f"🔔 <b>Запрос нового AWG-конфига!</b>\n\n"
                f"👤 Пользователь: {query.from_user.full_name}\n"
                f"🆔 TG ID: <code>{tg_id}</code>\n"
                f"📌 Запросил перевыпуск файлов конфигурации."
            )
            await context.bot.send_message(chat_id=ADMIN_TG_ID, text=alert_text, parse_mode="HTML")
        except Exception as e:
            logger.warning(f"Failed to send admin notification for req_new_awg_conf: {e}")
        return

    if data == "ack_awg_conf_ok":
        tg_id = query.from_user.id
        from api.db import log_message_sent
        reply_text = (
            "👍 <b>Отлично!</b>\n\n"
            "Рады, что всё в порядке. Если возникнут вопросы или понадобится помощь — пишите нам в поддержку."
        )
        await safe_edit_text_logged(query, reply_text, "support_awg_conf_ack")
        try:
            alert_text = (
                f"ℹ️ <b>Ответ пользователя по AWG</b>\n\n"
                f"👤 Пользователь: {query.from_user.full_name}\n"
                f"🆔 TG ID: <code>{tg_id}</code>\n"
                f"📌 Ответил: «Спасибо, не надо» (конфиг не требуется)."
            )
            await context.bot.send_message(chat_id=ADMIN_TG_ID, text=alert_text, parse_mode="HTML")
        except Exception as e:
            logger.warning(f"Failed to send admin notification for ack_awg_conf_ok: {e}")
        return

    if data.startswith("wb_rate:"):
        parts = data.split(":")
        rating = parts[1] if len(parts) > 1 else ""
        tg_id = query.from_user.id
        from api.db import set_winback_discount, log_message_sent
        if rating in ("good", "normal"):
            set_winback_discount(tg_id, True)
            reply_text = (
                "🥳 <b>Рады, что вы с нами!</b>\n\n"
                "Мы закрепили за вами персональную скидку <b>20%</b> на первую оплату любого тарифа!\n"
                "Воспользоваться скидкой можно в любой момент на странице тарифов."
            )
            markup = InlineKeyboardMarkup([
                [InlineKeyboardButton("💎 Выбрать тариф со скидкой 20%", callback_data="tariffs")],
                [InlineKeyboardButton("◀️ Главное меню", callback_data="back_to_menu")]
            ])
            await safe_edit_text_logged(query, reply_text, "winback_survey", reply_markup=markup)
        elif rating == "bad":
            reply_text = (
                "😔 <b>Сожалеем, что у вас возникли сложности!</b>\n\n"
                "Мы уже передали ваше сообщение команде поддержки. Напишите нам напрямую, и мы поможем всё настроить."
            )
            markup = InlineKeyboardMarkup([
                [InlineKeyboardButton("💬 Связаться с поддержкой", url="https://t.me/tiinsupport")],
                [InlineKeyboardButton("◀️ Главное меню", callback_data="back_to_menu")]
            ])
            await safe_edit_text_logged(query, reply_text, "winback_survey", reply_markup=markup)
            
            # Send alert to Admin
            try:
                alert_text = (
                    f"⚠️ <b>Жалоба на качество VPN (Winback 3d)!</b>\n\n"
                    f"👤 Пользователь: {query.from_user.full_name}\n"
                    f"🆔 TG ID: <code>{tg_id}</code>\n"
                    f" Оценка: 👎 Bad / Есть проблемы"
                )
                await context.bot.send_message(chat_id=ADMIN_TG_ID, text=alert_text, parse_mode="HTML")
            except Exception as e:
                logger.warning(f"Failed to send winback bad rating alert: {e}")

    elif data == "my_configs":
        await show_configs(query, xui)

    elif data == "tariffs":
        await show_tariffs(query)

    elif data == "activate_test":
        await activate_test_period(query, xui)

    elif data == "back_to_menu":
        await show_main_menu(query, xui)

    elif data == "web_portal":
        from api.db import get_web_token
        token = get_web_token(query.from_user.id)
        if token:
            url = f"https://344988.snk.wtf/my/{token}"
            text = (
                f"🌐 <b>Личный кабинет</b>\n\n"
                f"Работает даже без Telegram. Сохраните в закладки:\n\n"
                f"<code>{url}</code>"
            )
            await safe_edit_text_logged(
                query, text, "web_portal",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🌐 Открыть", url=url)],
                    [InlineKeyboardButton("◀️ Назад", callback_data="back_to_menu")],
                ]),
            )
        else:
            await safe_edit_text_logged(query, "❌ Ошибка. Попробуйте позже.", "web_portal",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Назад", callback_data="back_to_menu")]]))

    elif data == "test_protocol_choose":
        text = (
            f"🎁 <b>Бесплатный тест — {TARIFFS['test_24h']['period']}</b>\n\n"
            "Выберите протокол:\n\n"
            "🟢 <b>VLESS</b> — телефоны, ПК, macOS"
        )
        await safe_edit_text_logged(query, text, "test_protocol_choose",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🟢 VLESS", callback_data="test_vless")],
                [InlineKeyboardButton("◀️ Назад", callback_data="back_to_menu")],
            ])
        )

    elif data == "get_awg_config":
        await handle_get_awg_config(query)

    elif data == "get_awg_config_v2":
        await handle_get_awg_config_v2(query)

    elif data == "test_awg":
        await handle_test_awg(query, xui)

    elif data == "test_vless":
        await handle_test_vless(query, xui)

    elif data.startswith("show_key_"):
        client_name = data.removeprefix("show_key_")
        await show_single_config(query, client_name, xui)

    elif data == "split_tunneling":
        happ_routing_url = "https://344988.snk.wtf/happ-routing"
        text = (
            "🔀 <b>Split tunneling для Happ</b>\n\n"
            "Российские сайты будут открываться напрямую, "
            "остальной трафик — через VPN.\n\n"
            "Нажмите кнопку ниже — правила маршрутизации "
            "добавятся в Happ.\n\n"
            "После импорта откройте в Happ:\n"
            "<b>Настройки</b> → <b>Настройки туннеля</b> → <b>Маршрутизация</b>\n"
            "и выберите <b>Tiin Split Rules</b>."
        )
        await safe_edit_text_logged(
            query, text, "split_tunneling",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📲 Установить правила в Happ", url=happ_routing_url)],
                [InlineKeyboardButton("🔑 Мои конфиги", callback_data="my_configs")],
                [InlineKeyboardButton("◀️ Назад", callback_data="back_to_menu")],
            ]),
        )

    elif data.startswith("buy_tariff_"):
        parts     = data.removeprefix("buy_tariff_")
        is_renew  = parts.endswith("_renew")
        tariff_id = parts.removesuffix("_renew")

        tariff = TARIFFS.get(tariff_id)
        if not tariff:
            await safe_edit_text(query, "❌ Тариф не найден")
            return

        if tariff.get("is_test"):
            await handle_test_vless(query, xui)
        else:
            renew_info = context.user_data.get("renew_info", {})
            promo = context.user_data.get("promo")  # keep for confirm_payment step
            await show_tariff_info(
                query, tariff_id, "vless",
                is_renew=is_renew,
                client_name=renew_info.get("client_name"),
                inbound_id=renew_info.get("inbound_id"),
                promo=promo,
            )
        await _log_message(query.from_user.id, "bot_menu", "buy_tariff", f"tariff={tariff_id} renew={is_renew}")

    elif data.startswith("confirm_payment_"):
        parts     = data.removeprefix("confirm_payment_")
        is_renew  = parts.endswith("_renew")
        tariff_id = parts.removesuffix("_renew")

        tariff = TARIFFS.get(tariff_id)
        if not tariff:
            await safe_edit_text(query, "❌ Тариф не найден")
            return

        renew_info = context.user_data.get("renew_info", {})
        promo = context.user_data.pop("promo", None)
        await process_payment(
            query, tariff_id, "vless",
            is_renew=is_renew,
            client_name=renew_info.get("client_name"),
            inbound_id=renew_info.get("inbound_id"),
            promo=promo,
        )

    elif data.startswith("renew_"):
        parts       = data.removeprefix("renew_")
        client_name, inbound_id = parts.rsplit("_", 1)
        await show_renew_tariffs(query, context, inbound_id, client_name)

    elif data == "referral":
        text, link = await _refer_text(context, query.from_user.id)
        qr = _make_qr(link)
        try:
            await query.message.delete()
        except Exception:
            pass
        await context.bot.send_photo(
            chat_id=query.from_user.id,
            photo=qr,
            caption=text,
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("◀️ Назад", callback_data="back_to_menu")]
            ]),
        )
        try:
            from api.db import log_message_sent
            log_message_sent(tg_id=query.from_user.id, source="bot_menu",
                             scenario="referral_qr", message_text=text, status='sent')
        except Exception:
            pass

    elif data == "proxy_file":
        text = (
            "🔗 <b>Прокси для Telegram</b>\n\n"
            "Нажмите кнопку ниже — прокси подключится автоматически.\n"
            "Перешлите файл друзьям, у кого не работает Telegram."
        )
        await safe_edit_text_logged(query, text, "proxy_info",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⚡ Подключить прокси", url=MTPROTO_PROXY_LINK)],
                [InlineKeyboardButton("📎 Скачать файл", callback_data="proxy_download")],
                [InlineKeyboardButton("◀️ Назад", callback_data="back_to_menu")],
            ]),
        )

    elif data == "proxy_download":
        proxy = make_proxy_file()
        try:
            await query.message.delete()
        except Exception:
            pass
        await context.bot.send_document(
            chat_id=query.from_user.id,
            document=proxy,
            caption=(
                "📎 <b>Прокси для Telegram</b>\n\n"
                "Скачайте и перешлите друзьям, у кого не работает Telegram."
            ),
            parse_mode="HTML",
        )
        try:
            from api.db import log_message_sent
            log_message_sent(tg_id=query.from_user.id, source="bot_menu",
                             scenario="proxy_download", status='sent')
        except Exception:
            pass

    elif data in ("yt_check_yes", "yt_check_no"):
        status_text = "🟢 Всё работает" if data == "yt_check_yes" else "🔴 Да, соединения нет"
        user = query.from_user
        user_info = f"{user.first_name or ''} {user.last_name or ''}".strip()
        if user.username:
            user_info += f" (@{user.username})"
        user_info += f" [ID: <code>{user.id}</code>]"

        # Отвечаем пользователю
        user_reply = f"Спасибо за отклик! Принято: <b>{status_text}</b>."
        await safe_edit_text_logged(query, user_reply, "yt_check_user_reply")

        # Уведомляем админа
        admin_notice = (
            f"📊 <b>Отклик по проверки YouTube (Windows)</b>\n\n"
            f"👤 <b>Пользователь:</b> {user_info}\n"
            f"📌 <b>Статус:</b> {status_text}"
        )
        if str(user.id) != str(ADMIN_TG_ID):
            await send_message_by_tg_id(
                tg_id=ADMIN_TG_ID,
                text=admin_notice,
                parse_mode="HTML",
                source="bot_system",
                scenario="yt_check_admin_notice",
            )

    elif data.startswith("op_"):
        op_map = {
            "op_mts": "🔴 МТС",
            "op_megafon": "🟢 МегаФон",
            "op_yota": "🔵 Yota",
            "op_beeline": "🟡 Билайн",
            "op_tele2": "⚫ Tele2 / Т-Мобайл",
            "op_other": "🌐 Другой / Домашний провайдер",
        }
        operator_name = op_map.get(data, data.removeprefix("op_"))
        user = query.from_user
        user_info = f"{user.first_name or ''} {user.last_name or ''}".strip()
        if user.username:
            user_info += f" (@{user.username})"
        user_info += f" [ID: <code>{user.id}</code>]"

        # Отвечаем пользователю
        user_reply = f"✅ Спасибо! Ваш оператор записан: <b>{operator_name}</b>.\n\nМы учитываем особенности каждого провайдера для подбора оптимального протокола."
        await safe_edit_text_logged(query, user_reply, "operator_survey_user_reply")

        # Уведомляем админа
        admin_notice = (
            f"📊 <b>Ответ на опрос: Оператор связи</b>\n\n"
            f"👤 <b>Пользователь:</b> {user_info}\n"
            f"📱 <b>Оператор:</b> {operator_name}"
        )
        if str(user.id) != str(ADMIN_TG_ID):
            await send_message_by_tg_id(
                tg_id=ADMIN_TG_ID,
                text=admin_notice,
                parse_mode="HTML",
                source="bot_system",
                scenario="operator_survey_admin_notice",
            )

    elif data == "feedback":
        import time as _time
        WAITING_FEEDBACK[query.from_user.id] = _time.time()
        text = (
            "✉️ <b>Поддержка</b>\n\n"
            "Напишите ваш вопрос или предложение — мы ответим в ближайшее время.\n\n"
            "👇 Просто отправьте сообщение в чат\n\n"
        )
        await safe_edit_text_logged(
            query, text, "feedback_prompt",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("◀️ Отмена", callback_data="back_to_menu")],
            ])
        )

    elif data == "autopay_on":
        from api.db import execute_query
        execute_query(
            "UPDATE users SET autopay_enabled = 1 WHERE tg_id = %s AND payment_method_id IS NOT NULL",
            (query.from_user.id,),
        )
        text = (
            "✅ Автопродление <b>включено</b>.\n\n"
            "Списание произойдёт за 1 день до окончания подписки.\n"
            "Отключить: /autopay"
        )
        await safe_edit_text_logged(query, text, "autopay_on")

    elif data == "autopay_off":
        from api.db import disable_autopay
        disable_autopay(query.from_user.id)
        text = (
            "🔴 Автопродление <b>выключено</b>.\n\n"
            "Включить снова: /autopay"
        )
        await safe_edit_text_logged(query, text, "autopay_off")

    elif data == "autopay_remove_card":
        from api.db import remove_payment_method
        remove_payment_method(query.from_user.id)
        text = (
            "🗑 <b>Карта отвязана</b>\n\n"
            "Автопродление выключено.\n"
            "При следующей оплате карта сохранится заново."
        )
        await safe_edit_text_logged(query, text, "autopay_remove_card")

    elif data == "autopay_change_tariff":
        from api.db import execute_query
        tg_id = query.from_user.id
        user = execute_query(
            "SELECT autopay_tariff FROM users WHERE tg_id = %s",
            (tg_id,), fetch='one',
        )
        current_tariff_id = (user.get('autopay_tariff') if user else None) or 'monthly_30d'

        buttons = []
        for t_id, t_info in TARIFFS.items():
            if t_info.get('is_test'):
                continue
            is_current = (t_id == current_tariff_id)
            mark = "✅ " if is_current else ""
            btn_text = f"{mark}{t_info['name']} — {t_info['price']} ₽"
            buttons.append([InlineKeyboardButton(btn_text, callback_data=f"set_autopay_tariff_{t_id}")])
        buttons.append([InlineKeyboardButton("« Назад в автопродление", callback_data="autopay_manage")])

        text = (
            "⚙️ <b>Выберите тариф для автопродления</b>\n\n"
            "Выбранный тариф будет автоматически продлеваться за 1 день до окончания подписки."
        )
        await safe_edit_text_logged(query, text, "autopay_change_tariff",
            reply_markup=InlineKeyboardMarkup(buttons),
        )

    elif data.startswith("set_autopay_tariff_"):
        from api.db import update_autopay_tariff
        t_id = data.removeprefix("set_autopay_tariff_")
        if t_id in TARIFFS and not TARIFFS[t_id].get("is_test"):
            update_autopay_tariff(query.from_user.id, t_id)
            t_name = TARIFFS[t_id]['name']
            await safe_edit_text(query, f"✅ Тариф автопродления изменён на <b>{t_name}</b>")
        else:
            await safe_edit_text(query, "❌ Неверный тариф")

    elif data == "autopay_manage":
        from api.db import execute_query
        tg_id = query.from_user.id
        user = execute_query(
            "SELECT autopay_enabled, payment_method_id, autopay_tariff FROM users WHERE tg_id = %s",
            (tg_id,), fetch='one',
        )
        if not user or not user.get('payment_method_id'):
            await safe_edit_text(query, "❌ Карта не привязана")
            return
        enabled = user['autopay_enabled']
        tariff_id = user.get('autopay_tariff') or 'monthly_30d'
        tariff = TARIFFS.get(tariff_id, {})
        toggle_text = "Выключить" if enabled else "Включить"
        toggle_data = "autopay_off" if enabled else "autopay_on"
        text = (
            f"🔄 <b>Автопродление</b>\n\n"
            f"Статус: {'✅ Включено' if enabled else '❌ Выключено'}\n"
            f"Тариф: {tariff.get('name', tariff_id)}\n"
            f"💳 Карта сохранена\n\n"
            f"При автопродлении списание происходит за 1 день до окончания подписки."
        )
        await safe_edit_text_logged(query, text, "autopay_manage",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(f"{'🔴' if enabled else '🟢'} {toggle_text}", callback_data=toggle_data)],
                [InlineKeyboardButton("⚙️ Изменить тариф", callback_data="autopay_change_tariff")],
                [InlineKeyboardButton("🗑 Отвязать карту", callback_data="autopay_remove_card")],
            ]),
        )

    elif data.startswith("refund_confirm_"):
        from api.db import (
            get_last_paid_payment, update_payment_status,
            deactivate_key_by_payment, get_user_email, log_message_sent,
        )
        from api.webhook import deactivate_xui_client

        uid = int(data.removeprefix("refund_confirm_"))
        if query.from_user.id != uid:
            await safe_edit_text(query, "❌ Эта кнопка не для вас")
            return

        payment = get_last_paid_payment(uid)
        if not payment:
            await safe_edit_text(query, "❌ Не найден оплаченный платёж")
            return

        payment_id = payment["payment_id"]
        amount = str(payment["amount"])
        client_name = get_user_email(uid, payment_id=payment_id)

        # YooKassa refund
        yoo_ok = create_yookassa_refund(payment_id, amount)
        if not yoo_ok:
            await safe_edit_text(query, "❌ Ошибка создания возврата в YooKassa. Попробуйте позже.")
            return

        # Update DB
        update_payment_status(payment_id, "refunded")
        deactivate_key_by_payment(payment_id)

        # Deactivate XUI client
        if client_name:
            deactivate_xui_client(client_name)

        text = (
            "✅ <b>Возврат оформлен</b>\n\n"
            f"Деньги вернутся на карту в течение 1–3 рабочих дней.\n\n"
            f"Если остались вопросы — нажмите «Написать нам» в меню."
        )
        await safe_edit_text(query, text)

        log_message_sent(tg_id=uid, source="admin_send", scenario="refund_confirm",
                         message_text=text, status="sent")

        # Notify admin
        try:
            await context.bot.send_message(
                chat_id=ADMIN_TG_ID,
                text=f"🔄 <b>Возврат оформлен (Бот)</b>\n\n"
                     f"Пользователь: <code>{uid}</code>" + (f" ({client_name})" if client_name else "") + f"\n"
                     f"Платёж: <code>{payment_id}</code>\n"
                     f"Сумма: {amount} ₽\n"
                     f"YooKassa: OK\n\n"
                     f"⚠️ <i>Проверьте конфиги пользователя (3x-ui / AWG).</i>",
                parse_mode="HTML",
            )
        except Exception:
            pass

    elif data.startswith("refund_decline_"):
        from api.db import log_message_sent

        uid = int(data.removeprefix("refund_decline_"))
        if query.from_user.id != uid:
            await safe_edit_text(query, "❌ Эта кнопка не для вас")
            return

        text = (
            "Хорошо! Если возникнут вопросы или понадобится помощь — "
            "напишите нам через меню «Написать нам»."
        )
        await safe_edit_text(query, text)

        log_message_sent(tg_id=uid, source="admin_send", scenario="refund_decline",
                         message_text=text, status="sent")

        # Notify admin
        try:
            await context.bot.send_message(
                chat_id=ADMIN_TG_ID,
                text=f"❌ <b>Возврат отклонён</b>\n\n"
                     f"Пользователь: <code>{uid}</code>\n"
                     f"Отказался от возврата.",
                parse_mode="HTML",
            )
        except Exception:
            pass


# ──────────────────────────────────────────────────────────────────────────────
# Интервалы
# ──────────────────────────────────────────────────────────────────────────────

async def notify_expiring_subscriptions(bot):
    """Проверяет истекающие подписки и уведомляет пользователей."""
    notifications = [
        (3, "3 дня", "⏳"),
        (0, "сегодня", "🔴"),
    ]

    for days, label, icon in notifications:
        users = get_users_expiring_in_days(days)
        for user in users:
            tg_id = user['tg_id']
            if not tg_id or user.get('bot_blocked'):
                continue

            # Пропускаем 3-дневное уведомление для тестовых пользователей (без оплат)
            if days == 3:
                from api.db import execute_query
                paid = execute_query(
                    "SELECT COUNT(*) AS cnt FROM payments "
                    "WHERE tg_id = %s AND status = 'paid' AND is_test = 0",
                    (tg_id,), fetch='one'
                )
                if not paid or paid['cnt'] == 0:
                    continue

            email = user.get('email')
            until = user['subscription_until'].strftime("%d.%m.%Y")
            has_autopay = user.get('autopay_enabled') and user.get('payment_method_id')

            # Telegram notification (if user has tg_id)
            if has_autopay:
                # Autopay user — inform about upcoming charge, no manual CTA
                tariff_id = user.get('autopay_tariff') or 'monthly_30d'
                tariff = TARIFFS.get(tariff_id, {})
                tariff_name = tariff.get('name', tariff_id)
                price = tariff.get('price', '?')

                if days == 0:
                    msg = (
                        f"🔄 <b>Сегодня автоматически продлим подписку</b>\n\n"
                        f"📦 Тариф: {tariff_name}\n"
                        f"💰 Сумма: {price} ₽\n\n"
                        f"<i>Отменить автопродление: /autopay</i>"
                    )
                else:
                    msg = (
                        f"🔄 <b>Через {label} автоматически продлим подписку</b>\n\n"
                        f"📦 Тариф: {tariff_name}\n"
                        f"💰 Сумма: {price} ₽\n"
                        f"📅 Окончание: <b>{until}</b>\n\n"
                        f"<i>Отменить автопродление: /autopay</i>"
                    )
                reply_markup = InlineKeyboardMarkup([
                    [InlineKeyboardButton("⚙️ Управление автопродлением", callback_data="autopay_manage")]
                ])
            else:
                # No autopay — standard renewal reminder with instant tariff choices
                if days == 0:
                    msg = (
                        f"🔴 <b>Подписка истекает сегодня!</b>\n\n"
                        f"📅 Окончание: <b>{until}</b>\n\n"
                        f"Выберите тариф для продления:"
                    )
                else:
                    msg = (
                        f"{icon} <b>Подписка истекает через {label}</b>\n\n"
                        f"📅 Окончание: <b>{until}</b>\n\n"
                        f"Выберите тариф для продления:"
                    )
                from bot_xui.views import _build_tariff_text_and_keyboard
                _, reply_markup = _build_tariff_text_and_keyboard(tg_id, mode="renew")

            try:
                await bot.send_message(
                    chat_id=tg_id,
                    text=msg,
                    parse_mode="HTML",
                    reply_markup=reply_markup,
                )
                logger.info(f"[NOTIFY] Sent expiry warning ({days}d) to tg:{tg_id}")
                try:
                    from api.db import log_message_sent
                    log_message_sent(tg_id=tg_id, source="cron_expiry",
                                     scenario=f"expiry_{days}d", message_text=msg, status='sent')
                except Exception:
                    pass
            except Exception as e:
                err_str = str(e).lower()
                is_block = "blocked" in err_str or "deactivated" in err_str
                if is_block:
                    try:
                        from api.db import execute_query
                        execute_query("UPDATE users SET bot_blocked = 1 WHERE tg_id = %s", (tg_id,))
                    except Exception:
                        pass
                logger.warning(f"[NOTIFY] Failed to notify tg:{tg_id}: {e}")
                try:
                    from api.db import log_message_sent
                    log_message_sent(tg_id=tg_id, source="cron_expiry",
                                     scenario=f"expiry_{days}d", message_text=msg,
                                     status='blocked' if is_block else 'failed',
                                     error_text=str(e)[:255])
                except Exception:
                    pass

            # Email notification (for web-only users or as backup)
            if email and not tg_id:
                try:
                    from api.notifications import send_expiry_warning_email
                    send_expiry_warning_email(to=email, days_left=days, expiry_date=until)
                    logger.info(f"[NOTIFY] Sent expiry email ({days}d) to {email}")
                except Exception as e:
                    logger.warning(f"[NOTIFY] Failed to email {email}: {e}")

    # Post-expiry: notify users whose subscription expired yesterday
    from api.db import execute_query as _eq
    expired_yesterday = _eq(
        "SELECT tg_id, subscription_until FROM users "
        "WHERE subscription_until BETWEEN NOW() - INTERVAL 2 DAY AND NOW() - INTERVAL 1 DAY "
        "AND tg_id IS NOT NULL AND tg_id > 0 "
        "AND bot_blocked = 0",
        fetch='all',
    )
    for user in (expired_yesterday or []):
        tg_id = user['tg_id']
        try:
            await bot.send_message(
                chat_id=tg_id,
                text=(
                    "❌ <b>Подписка истекла</b>\n\n"
                    "VPN больше не работает. Продлите подписку, "
                    "чтобы вернуть доступ."
                ),
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("💎 Продлить", callback_data="tariffs")]
                ])
            )
            logger.info(f"[NOTIFY] Sent post-expiry to tg:{tg_id}")
            try:
                from api.db import log_message_sent
                log_message_sent(tg_id=tg_id, source="cron_expiry",
                                 scenario="expiry_0d",
                                 message_text="❌ Подписка истекла\n\nVPN больше не работает. Продлите подписку, чтобы вернуть доступ.",
                                 status='sent')
            except Exception:
                pass

            # Notify admin
            try:
                sub_date = user.get('subscription_until')
                client_name = get_user_email(tg_id)
                await bot.send_message(
                    chat_id=ADMIN_TG_ID,
                    text=(
                        f"⏰ <b>Подписка пользователя истекла</b>\n\n"
                        f"Пользователь: <code>{tg_id}</code>" + (f" ({client_name})" if client_name else "") + f"\n"
                        f"Дата окончания: {sub_date}\n\n"
                        f"⚠️ <i>Подписка завершена. Проверьте конфиги (3x-ui / AWG).</i>"
                    ),
                    parse_mode="HTML"
                )
            except Exception as e:
                logger.warning(f"[NOTIFY] Failed admin expiry notification tg:{tg_id}: {e}")
        except Exception as e:
            err_str = str(e).lower()
            is_block = "blocked" in err_str or "deactivated" in err_str
            if is_block:
                try:
                    execute_query("UPDATE users SET bot_blocked = 1 WHERE tg_id = %s", (tg_id,))
                except Exception:
                    pass
            logger.warning(f"[NOTIFY] Failed post-expiry tg:{tg_id}: {e}")
            try:
                from api.db import log_message_sent
                log_message_sent(tg_id=tg_id, source="cron_expiry",
                                 scenario="expiry_0d",
                                 message_text="❌ Подписка истекла\n\nVPN больше не работает. Продлите подписку, чтобы вернуть доступ.",
                                 status='blocked' if is_block else 'failed',
                                 error_text=str(e)[:255])
            except Exception:
                pass

# ──────────────────────────────────────────────────────────────────────────────
# Автопродление
# ──────────────────────────────────────────────────────────────────────────────

async def autopay_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /autopay — показать статус и переключить автопродление."""
    tg_id = update.effective_user.id
    from api.db import execute_query
    user = execute_query(
        "SELECT autopay_enabled, payment_method_id, autopay_tariff FROM users WHERE tg_id = %s",
        (tg_id,), fetch='one',
    )
    if not user:
        await log_and_reply_text(update, "❌ Пользователь не найден")
        return

    enabled = user['autopay_enabled']
    has_method = bool(user['payment_method_id'])
    tariff_id = user.get('autopay_tariff') or 'monthly_30d'
    tariff = TARIFFS.get(tariff_id, {})

    if not has_method:
        await log_and_reply_text(update, 
            "🔄 <b>Автопродление</b>\n\n"
            "У вас нет сохранённой карты.\n"
            "Оплатите любой тариф — карта сохранится автоматически, "
            "и автопродление будет включено.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("💎 Тарифы", callback_data="tariffs")]
            ]),
        )
        return

    status = "✅ Включено" if enabled else "❌ Выключено"
    toggle_text = "Выключить" if enabled else "Включить"
    toggle_data = "autopay_off" if enabled else "autopay_on"

    await log_and_reply_text(update, 
        f"🔄 <b>Автопродление</b>\n\n"
        f"Статус: {status}\n"
        f"Тариф: {tariff.get('name', tariff_id)}\n"
        f"💳 Карта сохранена\n\n"
        f"При автопродлении списание происходит за 1 день до окончания подписки.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton(f"{'🔴' if enabled else '🟢'} {toggle_text}", callback_data=toggle_data)],
            [InlineKeyboardButton("⚙️ Изменить тариф", callback_data="autopay_change_tariff")],
            [InlineKeyboardButton("🗑 Отвязать карту", callback_data="autopay_remove_card")],
        ]),
    )


# ──────────────────────────────────────────────────────────────────────────────
# Ошибки
# ──────────────────────────────────────────────────────────────────────────────

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    err = context.error
    if isinstance(err, BadRequest):
        if "Message is not modified" in str(err):
            return
        if "Message can't be deleted" in str(err):
            return
        if "message to delete not found" in str(err):
            return
    if isinstance(err, NetworkError):
        logger.warning("Network error: %s", err)
        return
    logger.error("Unhandled exception:", exc_info=context.error)


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
    app.add_handler(CommandHandler("broadcast_ref", broadcast_ref))
    app.add_handler(CommandHandler("notify_sub_update", notify_sub_update))
    app.add_handler(CommandHandler("promo",     promo))
    app.add_handler(CommandHandler("addpromo",  addpromo))
    app.add_handler(CommandHandler("delpromo",  delpromo))
    app.add_handler(CommandHandler("promos",    promos))
    app.add_handler(CommandHandler("autopay",   autopay_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_feedback_message))
    app.add_handler(MessageHandler(filters.PHOTO, process_receipt_photo))
    app.add_handler(CallbackQueryHandler(receipt_callback_handler, pattern="^rcpt_"))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_error_handler(error_handler)

    logger.info("Bot started!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()