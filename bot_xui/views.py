"""
Все «экраны» бота: главное меню, тарифы, конфиги, инструкции, статистика.
"""
import logging
from datetime import datetime
from io import BytesIO

import qrcode
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from bot_xui.tariffs import TARIFFS
from api.db import get_keys_by_tg_id, get_user_email, is_awg_test_activated, is_vless_test_activated, get_permanent_discount, update_vless_link, get_web_token, get_user_by_tg_id, get_payment_by_id, get_referral_count
from bot_xui.helpers import convert_to_local, make_back_keyboard, make_main_keyboard, MAIN_MENU_TEXT, tariff_emoji, safe_edit_text, get_user_sub_url
from bot_xui.test_mode import is_test_mode
from config import ADMIN_TG_ID, REFERRAL_REWARD_DAYS, BOT_USERNAME

logger = logging.getLogger(__name__)

_MONTHS_RU = [
    "", "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
]

def _format_date_long(dt: datetime, offset_hours: int = 9) -> str:
    """'25 сентября 2026 г.' — человекочитаемая дата."""
    if dt is None:
        return "∞"
    from datetime import timedelta
    local = dt + timedelta(hours=offset_hours)
    return f"{local.day} {_MONTHS_RU[local.month]} {local.year} г."


# ──────────────────────────────────────────────────────────────────────────────
# Главное меню
# ──────────────────────────────────────────────────────────────────────────────

def build_main_menu_text(tg_id: int) -> str:
    keys = get_keys_by_tg_id(tg_id)

    # Защитный fallback: ключей нет вообще (например, не сработала авто-выдача).
    if not keys:
        return MAIN_MENU_TEXT

    # Берём самый «свежий» ключ, предпочитая ключи с payment_id (для тарифа).
    keys_with_payment = [k for k in keys if k.get("payment_id")]
    key = max(
        keys_with_payment or keys,
        key=lambda k: k.get("expires_at") or datetime.min,
    )

    sub_info, is_test = _build_subscription_info(tg_id, key)
    is_active = bool(key.get("expires_at") and key["expires_at"] > datetime.utcnow())
    expiry_label = "⏱ Истекает" if is_active else "⏱ Истекла"

    ref_count = get_referral_count(tg_id)
    ref_days = ref_count * REFERRAL_REWARD_DAYS

    text = (
        f"⚡️ <b> тииҥ VPN 🐿</b>\n\n"
        f"{sub_info}\n"
        f"{expiry_label} {_format_date_long(key.get('expires_at'))}\n\n"
    )

    token = get_web_token(tg_id)
    if token:
        text += f'🪄 <a href="https://344988.snk.wtf/my/{token}">Гид по подключению</a>\n\n'

    text += (
        f"<blockquote>👥 Бонус за друзей: +{ref_days} дн.</blockquote>\n"
        f"Воспользуйся реферальной ссылкой:\n➡️➡️➡️ <code>https://t.me/{BOT_USERNAME}?start={tg_id}</code> ⬅️"
    )

    if is_active and is_test:
        text += "\n\n⚡ <b>Можно приобрести тариф ☺</b> Жми «Тарифы»"
    elif not is_active:
        text += "\n\n⚡ <b>Подписка истекла.</b> Выбери новый тариф 👇"

    if tg_id == ADMIN_TG_ID:
        mode = "🧪 ВКЛ" if is_test_mode() else "✅ ВЫКЛ"
        text += f"\n\n⚙️ Тестовый режим: <b>{mode}</b> (/testmode)"

    return text


async def show_main_menu(query, xui=None):
    tg_id = query.from_user.id
    if xui is not None:
        from bot_xui.vpn_factory import auto_grant_test_and_notify
        await auto_grant_test_and_notify(tg_id, xui, query.message.reply_photo)

    text = build_main_menu_text(tg_id)
    markup = make_main_keyboard(tg_id)

    # Удаляем старое сообщение и шлём новое с картинкой через bot.send_start_screen
    from bot_xui.bot import send_start_screen
    try:
        await query.message.delete()
    except Exception as e:
        logger.warning(f"show_main_menu delete failed: {e}")
    await send_start_screen(query.message.chat, text, markup)


# ──────────────────────────────────────────────────────────────────────────────
# Инструкция
# ──────────────────────────────────────────────────────────────────────────────

_INSTRUCTION_APPS_ANDROID = [
    ("Hiddify", "https://play.google.com/store/apps/details?id=app.hiddify.com"),
    ("Happ",    "https://play.google.com/store/apps/details?id=com.happproxy&hl=ru"),
]
_INSTRUCTION_APPS_IOS = [
    ("Happ",      "https://apps.apple.com/app/happ-proxy-utility/id6504287215"),
    ("Streisand", "https://apps.apple.com/app/streisand/id6450534064"),
]
_INSTRUCTION_APPS_DESKTOP = [
    ("Hiddify (Win/Mac)", "https://github.com/hiddify/hiddify-app/releases"),
    ("SoftEther (Win)",   "https://www.softether-download.com/en.aspx?product=softether"),
]
_INSTRUCTION_APPS_TV = [
    ("VPN4TV", "https://play.google.com/store/apps/details?id=com.vpn4tv.hiddify"),
]

async def show_instructions(query):
    caption = (
        "📖 <b>Как подключиться</b>\n\n"
        "<b>1.</b> Скачайте приложение для вашего устройства\n"
        "<b>2.</b> Откройте <b>Мои конфиги</b> → скопируйте ссылку или QR\n"
        "<b>3.</b> Вставьте в приложение и подключитесь\n\n"
        "👇 <b>Выберите ваше устройство:</b>"
    )
    keyboard = [
        [InlineKeyboardButton(f"🤖 {label}", url=url) for label, url in _INSTRUCTION_APPS_ANDROID],
        [InlineKeyboardButton(f"🍏 {label}", url=url) for label, url in _INSTRUCTION_APPS_IOS],
        [InlineKeyboardButton(f"💻 {label}", url=url) for label, url in _INSTRUCTION_APPS_DESKTOP],
        [InlineKeyboardButton(f"📺 {label}", url=url) for label, url in _INSTRUCTION_APPS_TV],
        [InlineKeyboardButton("🖥 Windows XP/7 (SoftEther)", callback_data="test_protocol_choose")],
        [InlineKeyboardButton("◀️ Назад", callback_data="back_to_menu")],
    ]

    await query.edit_message_text(caption, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")


# ──────────────────────────────────────────────────────────────────────────────
# Тарифы
# ──────────────────────────────────────────────────────────────────────────────

def _build_tariff_text_and_keyboard(tg_id: int, mode: str = "buy") -> tuple[str, InlineKeyboardMarkup]:
    """
    Строит текст и клавиатуру для экрана тарифов.
    mode='buy'   → callback_data='buy_tariff_{id}'
    mode='renew' → callback_data='buy_tariff_{id}_renew'
    """
    awg_used   = is_awg_test_activated(tg_id)
    vless_used = is_vless_test_activated(tg_id)
    perm_discount = get_permanent_discount(tg_id)

    test_tariffs    = []
    regular_tariffs = []
    special_tariffs = []

    for tid, tariff in TARIFFS.items():
        info = {**tariff, "id": tid}
        if tariff.get("is_test"):
            test_tariffs.append(info)
        elif tid == "admin_test":
            special_tariffs.append(info)
        else:
            regular_tariffs.append(info)

    regular_tariffs.sort(key=lambda x: x.get("days", 0))

    # ── Текст ──
    if mode == "renew":
        text = "🔄 <b>Продление подписки</b>\n\n"
    else:
        text = "💎 <b>Тарифы VPN</b>\n\n"

    if perm_discount > 0:
        text += f"🏷 Ваша скидка: <b>{perm_discount}%</b>\n\n"

    if test_tariffs and not (awg_used or vless_used) and mode == "buy":
        for t in test_tariffs:
            text += f"🎁 <b>{t['name']}</b> — бесплатно\n\n"

    for i, t in enumerate(regular_tariffs):
        if perm_discount > 0:
            discounted = max(1, round(t['price'] * (100 - perm_discount) / 100))
            ppd = discounted / t["days"] if t.get("days") else 0
            price_str = f"<s>{t['price']}₽</s> <b>{discounted}₽</b>"
        else:
            ppd = t["price"] / t["days"] if t.get("days") else 0
            price_str = f"<b>{t['price']}₽</b>"

        best = "  ⭐️" if t.get("days", 0) >= 365 else ""
        text += f"▸ <b>{t['period']}</b> — {price_str}"
        if t.get("days", 0) > 3:
            text += f"  ({ppd:.1f}₽/день)"
        text += f"{best}\n"

    if special_tariffs and tg_id == ADMIN_TG_ID and mode == "buy":
        text += "\n"
        for t in special_tariffs:
            text += f"🔧 {t['name']} — {t['price']}₽\n"

    text += "\n👥 До 10 устройств на любом тарифе"

    # ── Клавиатура ──
    keyboard = []
    suffix = "_renew" if mode == "renew" else ""

    if test_tariffs and not (awg_used or vless_used) and mode == "buy":
        keyboard.append([
            InlineKeyboardButton(f"🎁 {t['name']} (0 ₽)", callback_data=f"buy_tariff_{t['id']}{suffix}")
            for t in test_tariffs
        ])

    row: list = []
    for i, t in enumerate(regular_tariffs):
        if perm_discount > 0:
            btn_price = max(1, round(t['price'] * (100 - perm_discount) / 100))
            label = f"{tariff_emoji(t.get('days', 0))} {t['days']}дн | {btn_price}₽"
        else:
            label = f"{tariff_emoji(t.get('days', 0))} {t['days']}дн | {t['price']}₽"
        btn = InlineKeyboardButton(
            label,
            callback_data=f"buy_tariff_{t['id']}{suffix}",
        )
        row.append(btn)
        if len(row) == 2 or i == len(regular_tariffs) - 1:
            keyboard.append(row)
            row = []

    if special_tariffs and tg_id == ADMIN_TG_ID and mode == "buy":
        keyboard.append([
            InlineKeyboardButton(f"🔧 {t['price']}₽", callback_data=f"buy_tariff_{t['id']}")
            for t in special_tariffs
        ])

    keyboard.append([InlineKeyboardButton("◀️ Назад", callback_data="back_to_menu")])

    return text, InlineKeyboardMarkup(keyboard)


async def show_tariffs(query):
    text, markup = _build_tariff_text_and_keyboard(query.from_user.id, mode="buy")
    await query.edit_message_text(text, reply_markup=markup, parse_mode="HTML")


async def show_renew_tariffs(query, context, inbound_id: int, client_name: str):
    """Клавиатура продления — сохраняет контекст и показывает тарифы."""
    context.user_data["renew_info"] = {"inbound_id": inbound_id, "client_name": client_name}
    text, markup = _build_tariff_text_and_keyboard(query.from_user.id, mode="renew")
    await query.message.reply_text(text, reply_markup=markup, parse_mode="HTML")


# ──────────────────────────────────────────────────────────────────────────────
# Список конфигов
# ──────────────────────────────────────────────────────────────────────────────

def _refresh_vless_links(tg_id: int, xui):
    """Получить актуальную vless:// ссылку с панели 3x-ui и обновить в БД."""
    import base64
    import requests

    try:
        sub_url = xui.get_client_subscription_url(tg_id)
        if not sub_url:
            return

        resp = requests.get(sub_url, timeout=10)
        if resp.status_code != 200:
            return

        vless_link = base64.b64decode(resp.text.strip()).decode().strip()
        if not vless_link.startswith("vless://"):
            return

        update_vless_link(tg_id, vless_link)
    except Exception as e:
        logger.error(f"Failed to refresh vless links for {tg_id}: {e}")


_PROTOCOL_LABELS = {
    "softether": ("🖥", "SoftEther"),
    "vless":     ("🟢", "VLESS"),
    "awg":       ("📱", "AmneziaWG"),
}


def _pretty_config_label(key: dict, short: bool = False) -> tuple[str, str]:
    """
    Возвращает (emoji, человекочитаемое название) для VPN-ключа.
    short=True — компактная версия (period вместо полного названия тарифа) для inline-кнопок.
    """
    vpn_type = (key.get("vpn_type") or "").lower()
    emoji, protocol = _PROTOCOL_LABELS.get(vpn_type, ("🔑", vpn_type.upper() or "VPN"))

    # Тестовый VLESS (client_name = tiin_<tg_id>) — нет payment_id
    if vpn_type == "vless" and (key.get("client_name") or "").startswith("tiin_") and not key.get("payment_id"):
        return emoji, f"{protocol} — Тестовый"

    tariff_name = ""
    period = ""
    payment_id = key.get("payment_id")
    if payment_id:
        payment = get_payment_by_id(payment_id)
        if payment:
            tariff = TARIFFS.get(payment.get("tariff", ""))
            if tariff:
                tariff_name = tariff.get("name", "")
                period = tariff.get("period", "")

    suffix = (period or tariff_name) if short else (tariff_name or period)
    return emoji, f"{protocol} — {suffix}" if suffix else protocol


async def show_configs(query, xui=None):
    tg_id = query.from_user.id

    # Обновить vless_link из панели (в executor, чтобы не блокировать event loop)
    if xui:
        import asyncio
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, _refresh_vless_links, tg_id, xui)

    keys  = get_keys_by_tg_id(tg_id)

    if not keys:
        await _show_no_configs(query)
        return

    now = datetime.utcnow()
    active_keys  = [k for k in keys if not k["expires_at"] or k["expires_at"] > now]
    expired_keys = [k for k in keys if k["expires_at"] and k["expires_at"] <= now]

    if not active_keys and not expired_keys:
        await _show_no_configs(query)
        return

    text = "🔑 <b>Ваши конфиги</b>\n\n"
    if active_keys:
        rows = []
        for key in active_keys:
            emoji, label = _pretty_config_label(key)
            date = convert_to_local(key['expires_at'])
            rows.append((emoji, label, date))
        max_label = max(len(label) for _, label, _ in rows)
        lines = [f"{emoji} {label:<{max_label}}  ·  до {date}" for emoji, label, date in rows]
        text += "<pre>" + "\n".join(lines) + "</pre>\n"

    text += "\n<i>Нажмите, чтобы показать данные подключения:</i>"

    keyboard: list = []
    row: list = []
    for i, key in enumerate(active_keys):
        emoji, label = _pretty_config_label(key, short=True)
        row.append(InlineKeyboardButton(f"{emoji} {label}", callback_data=f"show_key_{key['client_name']}"))
        if len(row) == 2 or i == len(active_keys) - 1:
            keyboard.append(row)
            row = []

    # Additional protocol buttons for active subscribers
    if active_keys:
        has_awg = any(k['vpn_type'] == 'awg' for k in active_keys)
        has_se  = any(k['vpn_type'] == 'softether' for k in active_keys)
        extra_row = []
        if not has_awg:
            extra_row.append(InlineKeyboardButton("➕ AmneziaWG", callback_data="get_awg_config"))
        if not has_se:
            extra_row.append(InlineKeyboardButton("➕ SoftEther", callback_data="get_softether_config"))
        if extra_row:
            keyboard.append(extra_row)

    keyboard.append([InlineKeyboardButton("◀️ Назад", callback_data="back_to_menu")])

    await safe_edit_text(query, text, reply_markup=InlineKeyboardMarkup(keyboard))


async def _show_no_configs(query):
    text = (
        "🔑 <b>У вас пока нет активных конфигов</b>\n\n"
        "Выберите тариф — конфиг будет создан автоматически."
    )
    buttons = [
        [InlineKeyboardButton("💎 Выбрать тариф", callback_data="tariffs")],
        [InlineKeyboardButton("◀️ Назад", callback_data="back_to_menu")],
    ]
    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(buttons),
        parse_mode="HTML",
    )


# ──────────────────────────────────────────────────────────────────────────────
# Один конфиг с QR
# ──────────────────────────────────────────────────────────────────────────────

def _build_subscription_info(tg_id: int, key: dict) -> tuple[str, bool]:
    """Строит блок информации о подписке: имя, тариф, устройства, трафик.
    Возвращает (text, is_test)."""
    user = get_user_by_tg_id(tg_id)
    first_name = (user or {}).get("first_name") or ""

    # Определяем тариф через payment_id (fallback: другие ключи пользователя)
    tariff_name = ""
    device_limit = 10
    is_test = False
    payment_id = key.get("payment_id")
    if not payment_id:
        # Ищем payment_id среди других активных ключей
        all_keys = get_keys_by_tg_id(tg_id)
        for k in all_keys:
            if k.get("payment_id"):
                payment_id = k["payment_id"]
                break
    if payment_id:
        payment = get_payment_by_id(payment_id)
        if payment:
            tariff_key = payment.get("tariff", "")
            tariff = TARIFFS.get(tariff_key)
            if tariff:
                tariff_name = tariff["name"]
                device_limit = tariff.get("device_limit", 10)
                is_test = tariff.get("is_test", False)

    lines = []
    if first_name:
        lines.append(f"Привет, <b>{first_name} 💫</b>\n")

    quote_lines = []
    if tariff_name:
        quote_lines.append(f"📦 Тариф: {tariff_name}")
    quote_lines.append(f"📱 Устройств: до {device_limit}")
    quote_lines.append(f"📊 Трафик: {'10 ГБ' if is_test else '♾ Безлимит'}")
    lines.append("<blockquote>" + "\n".join(quote_lines) + "</blockquote>")

    return "\n".join(lines), is_test


async def show_single_config(query, client_name: str, xui):
    tg_id = query.from_user.id
    keys  = get_keys_by_tg_id(tg_id)
    key   = next((k for k in keys if k["client_name"] == client_name), None)

    if not key:
        await query.answer("❌ Конфиг не найден", show_alert=True)
        return

    expires_at = key["expires_at"]
    is_active  = not expires_at or expires_at > datetime.utcnow()
    status     = ("✅", "Активен") if is_active else ("❌", "Истек")
    sub_info, is_test_tariff = _build_subscription_info(tg_id, key)
    pretty_emoji, pretty_label = _pretty_config_label(key)

    back_buttons = []
    if is_test_tariff:
        back_buttons.append([InlineKeyboardButton("⚡️ Безлимит трафик — от 199 ₽", callback_data="tariffs")])
    back_buttons.append([InlineKeyboardButton("🔙 К списку", callback_data="my_configs")])
    back_markup = InlineKeyboardMarkup(back_buttons)

    # SoftEther — текстовое сообщение без QR
    if key["vpn_type"] == "softether":
        import json
        try:
            creds = json.loads(key.get("vless_link") or "{}")
        except (json.JSONDecodeError, TypeError):
            creds = {}

        caption = (
            f"{sub_info}\n\n"
            f"{pretty_emoji} <b>{pretty_label}</b>  {status[0]} {status[1]}\n"
            f"⏱ До: {convert_to_local(expires_at)}\n\n"
            f"<b>Данные для подключения:</b>\n\n"
            f"Сервер: <pre>{creds.get('host', '')}</pre>\n"
            f"Порт: <pre>{creds.get('port', '')}</pre>\n"
            f"Hub: <pre>{creds.get('hub', '')}</pre>\n"
            f"Логин: <pre>{creds.get('username', '')}</pre>\n"
            f"Пароль: <pre>{creds.get('password', '')}</pre>\n"
        )
        await query.message.delete()

        # Отправляем .vpn файл если есть
        vpn_file_content = key.get("vpn_file")
        if vpn_file_content:
            vpn_bio = BytesIO(vpn_file_content.encode("utf-8"))
            vpn_bio.name = f"tiin_vpn_{creds.get('username', 'config')}.vpn"
            await query.message.chat.send_document(
                document=vpn_bio,
                caption=caption,
                parse_mode="HTML",
                reply_markup=back_markup,
            )
        else:
            await query.message.chat.send_message(
                text=caption,
                parse_mode="HTML",
                reply_markup=back_markup,
            )
        return

    # AWG — отправить .conf файл
    if key["vpn_type"] == "awg":
        conf_text = key.get("vless_link") or ""
        if not conf_text:
            await query.answer("❌ Конфиг не найден", show_alert=True)
            return

        caption = (
            f"{sub_info}\n\n"
            f"{pretty_emoji} <b>{pretty_label}</b>  {status[0]} {status[1]}\n"
            f"⏱ До: {convert_to_local(expires_at)}\n\n"
            f"💡 <i>Импортируйте файл в AmneziaVPN</i>"
        )

        conf_bio = BytesIO(conf_text.encode("utf-8"))
        conf_bio.name = f"{key['client_name']}.conf"

        await query.message.delete()
        await query.message.chat.send_document(
            document=conf_bio,
            caption=caption,
            parse_mode="HTML",
            reply_markup=back_markup,
        )
        return

    # VLESS — QR + ссылка подписки (через прокси-эндпоинт, который переписывает remark)
    sub_url = get_user_sub_url(tg_id) or key.get("subscription_link") or ""

    bio = BytesIO()
    bio.name = "qr.png"
    qr = qrcode.QRCode(version=1, box_size=8, border=4)
    qr.add_data(sub_url)
    qr.make(fit=True)
    qr.make_image(fill_color="black", back_color="white").save(bio, "PNG")
    bio.seek(0)

    caption = (
        f"{sub_info}\n\n"
        f"{pretty_emoji} <b>{pretty_label}</b>  {status[0]} {status[1]}\n"
        f"⏱ До: {convert_to_local(expires_at)}\n\n"
        f"📎 <b>Ссылка подписки</b> (нажмите, чтобы скопировать):\n\n"
        f"➡️➡️➡️<code>{sub_url}</code>⬅️\n\n"
        f"💡 <i>Скопируйте ссылку или отсканируйте QR-код в приложении</i>"
    )

    HAPP_ROUTING_URL = "https://344988.snk.wtf:2096/ruleset/happ-routing-rules.json"
    keyboard = [
        [InlineKeyboardButton("🔀 Split tunneling (Happ)", callback_data="split_tunneling")],
    ]
    if is_test_tariff:
        keyboard.append([InlineKeyboardButton("⚡️ Безлимит трафик — от 199 ₽", callback_data="tariffs")])
    keyboard.append([InlineKeyboardButton("🔙 К списку", callback_data="my_configs")])

    await query.message.delete()
    await query.message.chat.send_photo(
        photo=bio,
        caption=caption,
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


# async def show_vless_link(query, client_name: str):
#     """Показать VLESS ссылку с QR-кодом."""
#     tg_id = query.from_user.id
#     keys = get_keys_by_tg_id(tg_id)
#     key = next((k for k in keys if k["client_name"] == client_name), None)
#
#     if not key:
#         await query.answer("❌ Конфиг не найден", show_alert=True)
#         return
#
#     vless_link = key.get("vless_link") or ""
#     if not vless_link.startswith("vless://"):
#         await query.answer("❌ VLESS ссылка недоступна", show_alert=True)
#         return
#
#     bio = BytesIO()
#     bio.name = "qr.png"
#     qr = qrcode.QRCode(version=1, box_size=8, border=4)
#     qr.add_data(vless_link)
#     qr.make(fit=True)
#     qr.make_image(fill_color="black", back_color="white").save(bio, "PNG")
#     bio.seek(0)
#
#     caption = (
#         f"🔗 <b>VLESS ссылка — {key['client_name']}</b>\n\n"
#         f"👇 <i>Нажмите чтобы скопировать:</i>\n"
#         f"┌────────────────────\n"
#         f"  <pre>{vless_link}</pre>\n"
#         f"└────────────────────\n\n"
#         f"<i>Используйте если подписка не работает.\n"
#         f"Ссылка не обновляется автоматически.</i>"
#     )
#
#     keyboard = InlineKeyboardMarkup([
#         [InlineKeyboardButton("🔙 К конфигу", callback_data=f"show_key_{client_name}")],
#     ])
#
#     await query.message.delete()
#     await query.message.chat.send_photo(
#         photo=bio,
#         caption=caption,
#         parse_mode="HTML",
#         reply_markup=keyboard,
#     )
