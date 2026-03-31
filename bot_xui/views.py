"""
Все «экраны» бота: главное меню, тарифы, конфиги, инструкции, статистика.
"""
import logging
from datetime import datetime
from io import BytesIO

import qrcode
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from bot_xui.tariffs import TARIFFS
from api.db import get_keys_by_tg_id, get_user_email, is_awg_test_activated, is_vless_test_activated, get_permanent_discount, update_vless_link
from bot_xui.helpers import convert_to_local, make_back_keyboard, make_main_keyboard, MAIN_MENU_TEXT, tariff_emoji, safe_edit_text
from config import ADMIN_TG_ID

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────────────
# Главное меню
# ──────────────────────────────────────────────────────────────────────────────

async def show_main_menu(query):
    await safe_edit_text(query, MAIN_MENU_TEXT, reply_markup=make_main_keyboard())


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
        for key in active_keys:
            if key["vpn_type"] == "softether":
                emoji = "🖥"
            elif "vless" in key["vpn_type"]:
                emoji = "🟢"
            else:
                emoji = "📱"
            text += f"{emoji} <b>{key['client_name']}</b>  ·  до {convert_to_local(key['expires_at'])}\n"

    text += "\n<i>Нажмите, чтобы показать данные подключения:</i>"

    keyboard: list = []
    row: list = []
    for i, key in enumerate(active_keys):
        short = key["client_name"][:15] + ("…" if len(key["client_name"]) > 15 else "")
        cfg   = key.get("vless_link") or ""
        emoji = "🔗" if "vless" in cfg else ("🛡" if "trojan" in cfg else "📱")
        row.append(InlineKeyboardButton(f"{emoji} {short}", callback_data=f"show_key_{key['client_name']}"))
        if len(row) == 2 or i == len(active_keys) - 1:
            keyboard.append(row)
            row = []

    keyboard.append([InlineKeyboardButton("◀️ Назад", callback_data="back_to_menu")])

    await safe_edit_text(query, text, reply_markup=InlineKeyboardMarkup(keyboard))


async def _show_no_configs(query):
    text = (
        "🔑 <b>У вас пока нет конфигов</b>\n\n"
        "Выберите тариф или попробуйте бесплатно — "
        "конфиг будет создан автоматически."
    )
    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🎁 Попробовать бесплатно", callback_data="test_protocol")],
            [InlineKeyboardButton("💎 Выбрать тариф", callback_data="tariffs")],
            [InlineKeyboardButton("◀️ Назад", callback_data="back_to_menu")],
        ]),
        parse_mode="HTML",
    )


# ──────────────────────────────────────────────────────────────────────────────
# Один конфиг с QR
# ──────────────────────────────────────────────────────────────────────────────

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

    back_markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔙 К списку", callback_data="my_configs")]
    ])

    # SoftEther — текстовое сообщение без QR
    if key["vpn_type"] == "softether":
        import json
        try:
            creds = json.loads(key.get("vless_link") or "{}")
        except (json.JSONDecodeError, TypeError):
            creds = {}

        caption = (
            f"🖥 <b>{key['client_name']}</b>  {status[0]} {status[1]}\n"
            f"⏱ До: {convert_to_local(expires_at)}\n\n"
            f"<b>Данные для подключения:</b>\n\n"
            f"Сервер: <code>{creds.get('host', '')}</code>\n"
            f"Порт: <code>{creds.get('port', '')}</code>\n"
            f"Hub: <code>{creds.get('hub', '')}</code>\n"
            f"Логин: <code>{creds.get('username', '')}</code>\n"
            f"Пароль: <code>{creds.get('password', '')}</code>\n"
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

    # VLESS — QR + ссылка подписки
    sub_url = key.get("subscription_link") or ""

    bio = BytesIO()
    bio.name = "qr.png"
    qr = qrcode.QRCode(version=1, box_size=8, border=4)
    qr.add_data(sub_url)
    qr.make(fit=True)
    qr.make_image(fill_color="black", back_color="white").save(bio, "PNG")
    bio.seek(0)

    caption = (
        f"🟢 <b>{key['client_name']}</b>  {status[0]} {status[1]}\n"
        f"⏱ До: {convert_to_local(expires_at)}\n\n"
        f"🔗 <b>Ссылка подписки</b> (нажмите, чтобы скопировать):\n\n"
        f"<code>{sub_url}</code>\n\n"
        f"💡 <i>Скопируйте ссылку или отсканируйте QR-код в приложении</i>"
    )

    HAPP_ROUTING_URL = "https://344988.snk.wtf:2096/ruleset/happ-routing-rules.json"
    keyboard = [
        [InlineKeyboardButton("🔀 Split tunneling (Happ)", callback_data="split_tunneling")],
        [InlineKeyboardButton("🔙 К списку", callback_data="my_configs")],
    ]

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
#         f"  <code>{vless_link}</code>\n"
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
