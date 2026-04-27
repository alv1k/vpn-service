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
    get_users_expiring_in_days,
    validate_promocode,
    use_promocode,
    create_promocode,
    deactivate_promocode,
    list_active_promocodes,
    set_permanent_discount,
)

from bot_xui.helpers  import make_main_keyboard, MAIN_MENU_TEXT, MTPROTO_PROXY_LINK, safe_edit_text, make_proxy_file
from bot_xui.views    import (
    show_main_menu, show_tariffs, show_configs,
    show_single_config, show_instructions, show_renew_tariffs,
    build_main_menu_text,
    # show_vless_link,
)
from bot_xui.payment     import process_payment
from bot_xui.vpn_factory import handle_test_awg, handle_test_vless, handle_test_softether, handle_get_awg_config, handle_get_softether_config, grant_referral_vpn, activate_test_period
from bot_xui.messaging   import send_message_by_tg_id
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
    if not START_IMAGE_PATH.exists() or len(text) > 1024:
        await chat.send_message(text, reply_markup=reply_markup, parse_mode="HTML")
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
    except Exception as e:
        logger.warning(f"send_start_screen photo failed, falling back to text: {e}")
        await chat.send_message(text, reply_markup=reply_markup, parse_mode="HTML")


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
        await update.message.reply_text(text, reply_markup=markup, parse_mode="HTML")
        return

    # Parse referral deep link: /start <referrer_tg_id>
    referrer_tg_id = None
    if args and args[0].isdigit():
        referrer_tg_id = int(args[0])

    referral_applied = register_user_with_referral(tg_id, referrer_tg_id, first_name, last_name)

    if referral_applied:
        # Grant VPN to newcomer
        newcomer_result = await grant_referral_vpn(tg_id, REFERRAL_NEWCOMER_DAYS, xui)
        if newcomer_result and newcomer_result["action"] == "created":
            from bot_xui.vpn_factory import make_qr_bytes
            bio = make_qr_bytes(newcomer_result["sub_url"])
            await update.message.reply_photo(
                photo=bio,
                caption=(
                    f"🎁 Вы перешли по реферальной ссылке!\n"
                    f"Вам подарено <b>+{REFERRAL_NEWCOMER_DAYS} дня</b> VPN подписки!\n\n"
                    f'📲 <a href="https://344988.snk.wtf/my/{get_web_token(tg_id) or ""}">Инструкция по подключению</a>'
                ),
                parse_mode="HTML",
                reply_markup=make_main_keyboard(tg_id)
            )
        else:
            await update.message.reply_text(
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
        )
    else:
        get_or_create_user(tg_id, first_name, last_name)
        if referrer_tg_id:
            # User already registered but clicked a referral link
            await update.message.reply_text(
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
    await update.message.reply_photo(photo=qr, caption=text, parse_mode="HTML")


def _make_qr(data: str) -> io.BytesIO:
    """Генерирует QR-код и возвращает PNG в BytesIO."""
    img = qrcode.make(data, box_size=8, border=2)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    buf.name = "qr.png"
    return buf


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
        hour=11,           # каждый день в 11:00 (после уведомлений)
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

    ok = await send_message_by_tg_id(tg_id, raw[2], bot=context.bot)
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
    await update.message.reply_text(text, parse_mode="HTML")


async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/broadcast <сообщение> — рассылка всем.
    Поддерживает HTML-разметку из Telegram-форматирования (жирный, курсив и т.д.).
    """
    if update.effective_user.id != ADMIN_TG_ID:
        await update.message.reply_text("❌ Нет доступа")
        return

    raw = update.message.text.split(maxsplit=1)
    if len(raw) < 2:
        await update.message.reply_text("Использование: /broadcast <сообщение>\n\nИспользуйте форматирование Telegram (жирный, курсив) — оно сохранится в рассылке.")
        return

    # text_html сохраняет форматирование (bold, italic и т.д.) как HTML-теги
    full_html = update.message.text_html
    # Убираем "/broadcast " из начала
    msg_html = full_html.split(maxsplit=1)[1] if len(full_html.split(maxsplit=1)) > 1 else raw[1]

    users    = get_all_users_tg_ids()
    ok = fail = 0
    for uid in users:
        if await send_message_by_tg_id(uid, msg_html, parse_mode="HTML", bot=context.bot):
            ok += 1
        else:
            fail += 1

    await update.message.reply_text(f"📬 Рассылка завершена\n✅ {ok}\n❌ {fail}")


async def broadcast_ref(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/broadcast_ref — персональная рассылка с реферальной ссылкой на сайт."""
    if update.effective_user.id != ADMIN_TG_ID:
        await update.message.reply_text("❌ Нет доступа")
        return

    users = get_all_users_with_web_token()
    if not users:
        await update.message.reply_text("Нет пользователей с web_token")
        return

    await update.message.reply_text(f"📬 Начинаю рассылку {len(users)} пользователям...")

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
        if await send_message_by_tg_id(u['tg_id'], msg, parse_mode="HTML", bot=context.bot):
            ok += 1
        else:
            fail += 1

    await update.message.reply_text(f"📬 Рассылка завершена\n✅ {ok}\n❌ {fail}")


async def notify_sub_update(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/notify_sub_update — уведомить активных подписчиков об обновлении ссылки подписки."""
    if update.effective_user.id != ADMIN_TG_ID:
        await update.message.reply_text("❌ Нет доступа")
        return

    text = (
        "📢 <b>Важное обновление!</b>\n\n"
        "Мы обновили конфигурацию VPN.\n\n"
        "Что нужно сделать:\n"
        "1. Откройте бота и нажмите <b>Мои конфиги</b>\n"
        "2. Скопируйте ссылку подписки\n"
        "3. Вставьте её в приложение <b>Happ</b> или <b>Hiddify</b>\n"
        "4. Если у вас уже была ссылка — удалите старую и добавьте новую\n\n"
        "После этого VPN заработает как обычно.\n"
        "По любым вопросам пишите в поддержку."
    )

    users = get_active_subscribers_tg_ids()
    ok = fail = 0
    for uid in users:
        if await send_message_by_tg_id(uid, text, parse_mode="HTML", bot=context.bot):
            ok += 1
        else:
            fail += 1

    await update.message.reply_text(f"📬 Уведомление отправлено активным подписчикам\n✅ {ok}\n❌ {fail}")


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
        await update.message.reply_text("Использование: /promo <КОД>")
        return

    code = context.args[0]
    promo_data, error = validate_promocode(code, tg_id)

    if error:
        await update.message.reply_text(f"❌ {error}")
        return

    if promo_data['type'] == 'days':
        result = await grant_referral_vpn(tg_id, promo_data['value'], xui)
        if not result:
            await update.message.reply_text("❌ Ошибка активации промокода. Попробуйте позже.")
            return

        use_promocode(promo_data['id'], tg_id)

        if result["action"] == "created":
            from bot_xui.vpn_factory import make_qr_bytes
            bio = make_qr_bytes(result["sub_url"])
            await update.message.reply_photo(
                photo=bio,
                caption=(
                    f"🎉 Промокод <b>{code.upper()}</b> активирован!\n"
                    f"Вам подарено <b>+{promo_data['value']} дней</b> VPN подписки!\n\n"
                    f'📲 <a href="https://344988.snk.wtf/my/{get_web_token(tg_id) or ""}">Инструкция по подключению</a>'
                ),
                parse_mode="HTML",
                reply_markup=make_main_keyboard(tg_id)
            )
        else:
            await update.message.reply_text(
                f"🎉 Промокод <b>{code.upper()}</b> активирован!\n"
                f"Вам начислено <b>+{promo_data['value']} дней</b> VPN подписки.",
                parse_mode="HTML",
                reply_markup=make_main_keyboard(tg_id)
            )

    elif promo_data['type'] == 'discount':
        context.user_data["promo"] = {
            "id": promo_data['id'],
            "code": promo_data['code'],
            "value": promo_data['value'],
        }
        await update.message.reply_text(
            f"🎉 Промокод <b>{code.upper()}</b> применён!\n"
            f"Скидка <b>{promo_data['value']}%</b> будет применена к следующей оплате.\n\n"
            f"Выберите тариф:",
            parse_mode="HTML",
            reply_markup=make_main_keyboard(tg_id)
        )

    elif promo_data['type'] == 'permanent_discount':
        set_permanent_discount(tg_id, promo_data['value'])
        use_promocode(promo_data['id'], tg_id)
        await update.message.reply_text(
            f"🎉 Промокод <b>{code.upper()}</b> активирован!\n"
            f"Вам установлена постоянная скидка <b>{promo_data['value']}%</b> на все будущие оплаты.",
            parse_mode="HTML",
            reply_markup=make_main_keyboard(tg_id)
        )


async def addpromo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/addpromo CODE days|discount|permanent_discount VALUE [MAX_USES] [EXPIRES YYYY-MM-DD]"""
    if update.effective_user.id != ADMIN_TG_ID:
        await update.message.reply_text("❌ Нет доступа")
        return

    args = context.args
    if not args or len(args) < 3:
        await update.message.reply_text(
            "Использование:\n"
            "<pre>/addpromo CODE days 7</pre>\n"
            "<pre>/addpromo CODE discount 50 100 2026-04-01</pre>\n\n"
            "Параметры: КОД тип значение [макс_использований] [дата_истечения]",
            parse_mode="HTML"
        )
        return

    code = args[0]
    promo_type = args[1]
    if promo_type not in ('days', 'discount', 'permanent_discount'):
        await update.message.reply_text("❌ Тип должен быть <pre>days</pre>, <pre>discount</pre> или <pre>permanent_discount</pre>", parse_mode="HTML")
        return

    try:
        value = int(args[2])
    except ValueError:
        await update.message.reply_text("❌ Значение должно быть числом")
        return

    max_uses = None
    expires_at = None
    if len(args) >= 4:
        try:
            max_uses = int(args[3])
        except ValueError:
            await update.message.reply_text("❌ Макс. использований должно быть числом")
            return
    if len(args) >= 5:
        expires_at = args[4]

    try:
        create_promocode(code, promo_type, value, max_uses=max_uses, expires_at=expires_at)
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка: {e}")
        return

    await update.message.reply_text(
        f"✅ Промокод <b>{code.upper()}</b> создан\n"
        f"Тип: <b>{promo_type}</b>, значение: <b>{value}</b>"
        + (f", лимит: <b>{max_uses}</b>" if max_uses else "")
        + (f", до: <b>{expires_at}</b>" if expires_at else ""),
        parse_mode="HTML"
    )


async def delpromo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/delpromo CODE — деактивировать промокод."""
    if update.effective_user.id != ADMIN_TG_ID:
        await update.message.reply_text("❌ Нет доступа")
        return

    if not context.args:
        await update.message.reply_text("Использование: /delpromo <КОД>")
        return

    code = context.args[0]
    deactivate_promocode(code)
    await update.message.reply_text(f"✅ Промокод <b>{code.upper()}</b> деактивирован", parse_mode="HTML")


async def promos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/promos — список активных промокодов."""
    if update.effective_user.id != ADMIN_TG_ID:
        await update.message.reply_text("❌ Нет доступа")
        return

    items = list_active_promocodes()
    if not items:
        await update.message.reply_text("Нет активных промокодов")
        return

    text = "📋 <b>Активные промокоды:</b>\n\n"
    for p in items:
        uses = f"{p['used_count']}/{p['max_uses']}" if p['max_uses'] else f"{p['used_count']}/∞"
        exp = p['expires_at'].strftime("%d.%m.%Y") if p['expires_at'] else "∞"
        text += (
            f"<pre>{p['code']}</pre> — {p['type']} <b>{p['value']}</b>"
            f" | исп: {uses} | до: {exp}\n"
        )

    await update.message.reply_text(text, parse_mode="HTML")


# ──────────────────────────────────────────────────────────────────────────────
# Обратная связь
# ──────────────────────────────────────────────────────────────────────────────

WAITING_FEEDBACK: dict[int, float] = {}  # tg_id -> timestamp when feedback was requested
_FEEDBACK_TIMEOUT = 600  # 10 minutes

WAITING_EMAIL: dict[int, float] = {}  # tg_id -> timestamp when email was requested
_EMAIL_TIMEOUT = 600  # 10 minutes

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
    tg_id = update.effective_user.id

    # Ответ админа на пересланное сообщение
    if tg_id == ADMIN_TG_ID and update.message.reply_to_message:
        reply_text = update.message.reply_to_message.text or ""
        # Извлекаем tg_id из пересланного сообщения
        if "ID:" in reply_text:
            try:
                target_id = int(reply_text.split("ID:")[1].split(")")[0].strip())
                await send_message_by_tg_id(
                    target_id,
                    f"💬 <b>Ответ от поддержки:</b>\n\n{update.message.text}",
                    parse_mode="HTML",
                    bot=context.bot,
                )
                await update.message.reply_text("✅ Ответ отправлен")
                return
            except (ValueError, IndexError):
                pass

    import time as _time
    import re as _re

    # Check if user is responding with email
    email_ts = WAITING_EMAIL.get(tg_id)
    if email_ts is not None and (_time.time() - email_ts) <= _EMAIL_TIMEOUT:
        WAITING_EMAIL.pop(tg_id, None)
        text = (update.message.text or "").strip()
        if _re.match(r'^[^@\s]+@[^@\s]+\.[^@\s]+$', text):
            from api.db import execute_query, get_web_token
            execute_query(
                "UPDATE users SET email = %s WHERE tg_id = %s AND (email IS NULL OR email = '')",
                (text, tg_id),
            )
            # Send portal link to the email
            wt = get_web_token(tg_id)
            if wt:
                from api.notifications import send_payment_success_email
                portal_url = f"https://344988.snk.wtf/my/{wt}"
                send_payment_success_email(
                    to=text, tariff_name="", period="",
                    portal_url=portal_url,
                )
            await update.message.reply_text(
                f"✅ Email <b>{text}</b> сохранён!\n\n"
                "Ссылка на личный кабинет отправлена на вашу почту.",
                parse_mode="HTML",
            )
            logger.info(f"Email saved for tg_id={tg_id}: {text}")
            return
        else:
            await update.message.reply_text(
                "❌ Неверный формат email. Попробуйте ещё раз:",
            )
            WAITING_EMAIL[tg_id] = _time.time()  # reset timeout
            return

    ts = WAITING_FEEDBACK.get(tg_id)
    if ts is None or (_time.time() - ts) > _FEEDBACK_TIMEOUT:
        WAITING_FEEDBACK.pop(tg_id, None)
        return

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
        ADMIN_TG_ID, admin_text, parse_mode="HTML", bot=context.bot
    )

    await update.message.reply_text(
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
        await query.edit_message_text("⏳ Слишком много запросов. Подождите немного.")
        return
    data  = query.data

    if data == "my_configs":
        await show_configs(query, xui)

    elif data == "tariffs":
        await show_tariffs(query)

    elif data == "activate_test":
        await activate_test_period(query, xui)

    elif data == "back_to_menu":
        await show_main_menu(query, xui)

    elif data == "instructions":
        await show_instructions(query)

    elif data == "web_portal":
        from api.db import get_web_token
        token = get_web_token(query.from_user.id)
        if token:
            url = f"https://344988.snk.wtf/my/{token}"
            await safe_edit_text(
                query,
                f"🌐 <b>Личный кабинет</b>\n\n"
                f"Работает даже без Telegram. Сохраните в закладки:\n\n"
                f"<code>{url}</code>",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🌐 Открыть", url=url)],
                    [InlineKeyboardButton("◀️ Назад", callback_data="back_to_menu")],
                ]),
            )
        else:
            await safe_edit_text(query, "❌ Ошибка. Попробуйте позже.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Назад", callback_data="back_to_menu")]]))

    elif data == "test_protocol_choose":
        # Explicit protocol choice (from instructions or menu)
        await query.edit_message_text(
            "🎁 <b>Бесплатный тест — 24 часа</b>\n\n"
            "Выберите протокол:\n\n"
            "🟢 <b>VLESS</b> — телефоны, ПК, macOS\n"
            "🖥 <b>SoftEther</b> — Windows (включая XP/7)",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("🟢 VLESS", callback_data="test_vless"),
                    InlineKeyboardButton("🖥 SoftEther", callback_data="test_softether"),
                ],
                [InlineKeyboardButton("◀️ Назад", callback_data="back_to_menu")],
            ])
        )

    elif data == "get_awg_config":
        await handle_get_awg_config(query)

    elif data == "get_softether_config":
        await handle_get_softether_config(query)

    elif data == "test_awg":
        await handle_test_awg(query, xui)

    elif data == "test_vless":
        await handle_test_vless(query, xui)

    elif data == "test_softether":
        await handle_test_softether(query)

    elif data.startswith("show_key_"):
        client_name = data.removeprefix("show_key_")
        await show_single_config(query, client_name, xui)

    elif data == "split_tunneling":
        happ_routing_url = "https://344988.snk.wtf/happ-routing"
        await safe_edit_text(
            query,
            "🔀 <b>Split tunneling для Happ</b>\n\n"
            "Российские сайты будут открываться напрямую, "
            "остальной трафик — через VPN.\n\n"
            "Нажмите кнопку ниже — правила маршрутизации "
            "добавятся в Happ.\n\n"
            "После импорта откройте в Happ:\n"
            "<b>Настройки</b> → <b>Настройки туннеля</b> → <b>Маршрутизация</b>\n"
            "и выберите <b>Tiin Split Rules</b>.",
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
            await query.edit_message_text("❌ Тариф не найден")
            return

        if tariff.get("is_test"):
            # Skip protocol selection — go straight to VLESS
            await handle_test_vless(query, xui)
        else:
            renew_info = context.user_data.get("renew_info", {})
            await process_payment(
                query, tariff_id, "vless",
                is_renew=is_renew,
                client_name=renew_info.get("client_name"),
                inbound_id=renew_info.get("inbound_id"),
                promo=context.user_data.pop("promo", None),
            )

    elif data.startswith("renew_"):
        parts       = data.removeprefix("renew_")
        client_name, inbound_id = parts.rsplit("_", 1)
        await show_renew_tariffs(query, context, inbound_id, client_name)

    elif data == "referral":
        text, link = await _refer_text(context, query.from_user.id)
        qr = _make_qr(link)
        await query.message.delete()
        await context.bot.send_photo(
            chat_id=query.from_user.id,
            photo=qr,
            caption=text,
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("◀️ Назад", callback_data="back_to_menu")]
            ]),
        )

    elif data == "proxy_file":
        await query.edit_message_text(
            "🔗 <b>Прокси для Telegram</b>\n\n"
            "Нажмите кнопку ниже — прокси подключится автоматически.\n"
            "Перешлите файл друзьям, у кого не работает Telegram.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⚡ Подключить прокси", url=MTPROTO_PROXY_LINK)],
                [InlineKeyboardButton("📎 Скачать файл", callback_data="proxy_download")],
                [InlineKeyboardButton("◀️ Назад", callback_data="back_to_menu")],
            ]),
        )

    elif data == "proxy_download":
        proxy = make_proxy_file()
        await query.message.delete()
        await context.bot.send_document(
            chat_id=query.from_user.id,
            document=proxy,
            caption=(
                "📎 <b>Прокси для Telegram</b>\n\n"
                "Скачайте и перешлите друзьям, у кого не работает Telegram."
            ),
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("◀️ Назад", callback_data="back_to_menu")]
            ]),
        )

    elif data == "feedback":
        import time as _time
        WAITING_FEEDBACK[query.from_user.id] = _time.time()
        await safe_edit_text(
            query,
            "✉️ <b>Поддержка</b>\n\n"
            "Напишите ваш вопрос или предложение — мы ответим в ближайшее время.\n\n"
            "👇 Просто отправьте сообщение в чат\n\n"
            "📧 Или напишите на <b>support@tiinservice.ru</b>",
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
        await query.edit_message_text(
            "✅ Автопродление <b>включено</b>.\n\n"
            "Списание произойдёт за 1 день до окончания подписки.\n"
            "Отключить: /autopay",
            parse_mode="HTML",
        )

    elif data == "autopay_off":
        from api.db import disable_autopay
        disable_autopay(query.from_user.id)
        await query.edit_message_text(
            "🔴 Автопродление <b>выключено</b>.\n\n"
            "Включить снова: /autopay",
            parse_mode="HTML",
        )

    elif data == "autopay_remove_card":
        from api.db import remove_payment_method
        remove_payment_method(query.from_user.id)
        await query.edit_message_text(
            "🗑 <b>Карта отвязана</b>\n\n"
            "Автопродление выключено.\n"
            "При следующей оплате карта сохранится заново.",
            parse_mode="HTML",
        )

    elif data == "autopay_manage":
        from api.db import execute_query
        tg_id = query.from_user.id
        user = execute_query(
            "SELECT autopay_enabled, payment_method_id, autopay_tariff FROM users WHERE tg_id = %s",
            (tg_id,), fetch='one',
        )
        if not user or not user.get('payment_method_id'):
            await query.edit_message_text("❌ Карта не привязана", parse_mode="HTML")
            return
        enabled = user['autopay_enabled']
        tariff_id = user.get('autopay_tariff') or 'monthly_30d'
        tariff = TARIFFS.get(tariff_id, {})
        toggle_text = "Выключить" if enabled else "Включить"
        toggle_data = "autopay_off" if enabled else "autopay_on"
        await query.edit_message_text(
            f"🔄 <b>Автопродление</b>\n\n"
            f"Статус: {'✅ Включено' if enabled else '❌ Выключено'}\n"
            f"Тариф: {tariff.get('name', tariff_id)}\n"
            f"💳 Карта сохранена\n\n"
            f"При автопродлении списание происходит за 1 день до окончания подписки.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(f"{'🔴' if enabled else '🟢'} {toggle_text}", callback_data=toggle_data)],
                [InlineKeyboardButton("🗑 Отвязать карту", callback_data="autopay_remove_card")],
            ]),
        )


# ──────────────────────────────────────────────────────────────────────────────
# Интервалы
# ──────────────────────────────────────────────────────────────────────────────

async def notify_expiring_subscriptions(bot):
    """Проверяет истекающие подписки и уведомляет пользователей."""
    notifications = [
        (3, "3 дня", "⏳"),
        (1, "1 день", "⚠️"),
        (0, "сегодня", "🔴"),
    ]

    for days, label, icon in notifications:
        users = get_users_expiring_in_days(days)
        for user in users:
            tg_id = user['tg_id']
            email = user.get('email')
            until = user['subscription_until'].strftime("%d.%m.%Y")
            has_autopay = user.get('autopay_enabled') and user.get('payment_method_id')

            # Telegram notification (if user has tg_id)
            if tg_id:
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
                    # No autopay — standard renewal reminder
                    if days == 0:
                        msg = (
                            f"🔴 <b>Подписка истекает сегодня!</b>\n\n"
                            f"📅 Окончание: <b>{until}</b>\n\n"
                            f"Продлите сейчас, чтобы не потерять доступ."
                        )
                    else:
                        msg = (
                            f"{icon} <b>Подписка истекает через {label}</b>\n\n"
                            f"📅 Окончание: <b>{until}</b>\n\n"
                            f"Продлите, чтобы не потерять доступ."
                        )
                    reply_markup = InlineKeyboardMarkup([
                        [InlineKeyboardButton("🔄 Продлить", callback_data="tariffs")]
                    ])

                try:
                    await bot.send_message(
                        chat_id=tg_id,
                        text=msg,
                        parse_mode="HTML",
                        reply_markup=reply_markup,
                    )
                    logger.info(f"[NOTIFY] Sent expiry warning ({days}d) to tg:{tg_id}")
                except Exception as e:
                    logger.warning(f"[NOTIFY] Failed to notify tg:{tg_id}: {e}")

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
        "AND tg_id IS NOT NULL AND tg_id > 0",
        fetch='all',
    )
    for user in (expired_yesterday or []):
        try:
            await bot.send_message(
                chat_id=user['tg_id'],
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
            logger.info(f"[NOTIFY] Sent post-expiry to tg:{user['tg_id']}")
        except Exception as e:
            logger.warning(f"[NOTIFY] Failed post-expiry tg:{user['tg_id']}: {e}")

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
        await update.message.reply_text("❌ Пользователь не найден")
        return

    enabled = user['autopay_enabled']
    has_method = bool(user['payment_method_id'])
    tariff_id = user.get('autopay_tariff') or 'monthly_30d'
    tariff = TARIFFS.get(tariff_id, {})

    if not has_method:
        await update.message.reply_text(
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

    await update.message.reply_text(
        f"🔄 <b>Автопродление</b>\n\n"
        f"Статус: {status}\n"
        f"Тариф: {tariff.get('name', tariff_id)}\n"
        f"💳 Карта сохранена\n\n"
        f"При автопродлении списание происходит за 1 день до окончания подписки.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton(f"{'🔴' if enabled else '🟢'} {toggle_text}", callback_data=toggle_data)],
            [InlineKeyboardButton("🗑 Отвязать карту", callback_data="autopay_remove_card")],
        ]),
    )


# ──────────────────────────────────────────────────────────────────────────────
# Ошибки
# ──────────────────────────────────────────────────────────────────────────────

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    err = context.error
    if isinstance(err, BadRequest) and "Message is not modified" in str(err):
        return  # user double-tapped a button, harmless
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
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_error_handler(error_handler)

    logger.info("Bot started!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()