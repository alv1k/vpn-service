"""
Все «экраны» бота: главное меню, тарифы, конфиги, инструкции, статистика.
"""
import logging
from datetime import datetime
from io import BytesIO

import qrcode
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from bot_xui.tariffs import TARIFFS
from api.db import get_keys_by_tg_id, get_user_email, is_awg_test_activated, is_vless_test_activated
from bot_xui.helpers import convert_to_local, make_back_keyboard, make_main_keyboard, MAIN_MENU_TEXT, tariff_emoji, safe_edit_text

logger = logging.getLogger(__name__)

ADMIN_TG_ID = 364224373


# ──────────────────────────────────────────────────────────────────────────────
# Главное меню
# ──────────────────────────────────────────────────────────────────────────────

async def show_main_menu(query):
    await safe_edit_text(query, MAIN_MENU_TEXT, reply_markup=make_main_keyboard())


# ──────────────────────────────────────────────────────────────────────────────
# Инструкция
# ──────────────────────────────────────────────────────────────────────────────

_INSTRUCTION_APPS = [
    ("🤖 Amnezia VPN - Android",  "https://play.google.com/store/apps/details?id=org.amnezia.vpn&hl=ru"),
    ("🤖 v2rayTun - Android",      "https://play.google.com/store/apps/details?id=com.v2raytun.android"),
    ("🍎 v2RayTun app - iOS",      "https://apps.apple.com/ru/app/v2raytun/id6476628951"),
    ("🍎 V2Box app - iOS",         "https://apps.apple.com/us/app/v2box-v2ray-client/id6446814690"),
    ("💻 NekoRay - macOS",         "https://en.nekoray.org/"),
    ("💻 Fox VPN - macOS",         "https://bestfoxapp.com/en/products/mac"),
    ("🖥 Hiddify - Windows",       "https://hiddify.com/"),
    ("💻 NekoRay - Windows",       "https://en.nekoray.org/"),
    ("📺 VPN4TV: VPN для ТВ - TV", "https://play.google.com/store/apps/details?id=com.vpn4tv.hiddify"),
]

async def show_instructions(query):
    caption = (
        "📱 <b>Инструкция по подключению:</b>\n\n"
        "<b>1️⃣</b> Выберите приложение для вашей ОС (кнопки ниже)\n"
        "<b>2️⃣</b> Отсканируйте QR-код или скопируйте ссылку\n"
        "<b>3️⃣</b> Подключитесь к VPN\n\n"
        "💬 <b>Поддержка:</b> @al_v1k"
    )
    keyboard = [
        [InlineKeyboardButton(label, url=url)] for label, url in _INSTRUCTION_APPS
    ] + [[InlineKeyboardButton("◀️ В меню", callback_data="back_to_menu")]]

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
    title = "💎 **Выберите длительность продления**" if mode == "renew" else "💎 **Доступные тарифы VPN**"
    text = title + "\n\n"

    if test_tariffs and not (awg_used or vless_used) and mode == "buy":
        text += "🎁 **Попробуйте бесплатно**\n┌─────────────────────\n"
        for t in test_tariffs:
            text += (
                f"│ ✨ **{t['name']}**\n"
                f"│    ▸ Цена: **{t['price']} ₽**\n"
                f"│    ▸ Период: {t['period']}\n"
                f"│    ▸ Устройств: {t['device_limit']}\n"
            )
        text += "└─────────────────────\n\n"

    text += "📦 **Основные тарифы**\n"
    for i, t in enumerate(regular_tariffs):
        bullet = "├" if i < len(regular_tariffs) - 1 else "└"
        ppd = t["price"] / t["days"] if t.get("days") else 0
        text += f"{bullet}─ **{t['name']}**\n"
        text += f"{bullet}   💰 {t['price']} ₽  ·  ⏱ {t['period']}  ·  👥 {t['device_limit']} устройств\n"
        if t.get("days", 0) > 3:
            text += f"{bullet}   💫 всего {ppd:.1f} ₽/день\n"
        if t.get("features"):
            text += f"{bullet}   ✨ {', '.join(t['features'])}\n"
        if t.get("days", 0) >= 90:
            text += f"{bullet}   🌟 **Самый выгодный!**\n"
        if i < len(regular_tariffs) - 1:
            text += f"{bullet}  \n"

    if special_tariffs and tg_id == ADMIN_TG_ID and mode == "buy":
        text += "\n⚙️ **Служебные тарифы**\n"
        for t in special_tariffs:
            text += f"└─ 🔧 {t['name']}  💰 {t['price']} ₽ · {t['period']}\n"

    text += "\n_Выберите подходящий тариф ниже:_ ⬇️"

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
        btn = InlineKeyboardButton(
            f"{tariff_emoji(t.get('days', 0))} {t['days']}дн | {t['price']}₽",
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

    back_label = "◀️ Вернуться в меню"
    back_data  = "back_to_menu"
    keyboard.append([InlineKeyboardButton(back_label, callback_data=back_data)])

    return text, InlineKeyboardMarkup(keyboard)


async def show_tariffs(query):
    text, markup = _build_tariff_text_and_keyboard(query.from_user.id, mode="buy")
    await query.edit_message_text(text, reply_markup=markup, parse_mode="Markdown")


async def show_renew_tariffs(query, context, inbound_id: int, client_name: str):
    """Клавиатура продления — сохраняет контекст и показывает тарифы."""
    context.user_data["renew_info"] = {"inbound_id": inbound_id, "client_name": client_name}
    text, markup = _build_tariff_text_and_keyboard(query.from_user.id, mode="renew")
    await query.message.reply_text(text, reply_markup=markup, parse_mode="Markdown")


# ──────────────────────────────────────────────────────────────────────────────
# Список конфигов
# ──────────────────────────────────────────────────────────────────────────────

async def show_configs(query):
    tg_id = query.from_user.id
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

    text = "🔐 **Ваши VPN конфиги**\n\n"
    if active_keys:
        text += "✅ **Активные:**\n"
        for i, key in enumerate(active_keys, 1):
            prefix = "├─" if i < len(active_keys) else "└─"
            emoji  = "📱" if "vless" in key["vpn_type"] else "🖥"
            text += f"{prefix} {emoji} **{key['client_name']}**\n"
            text += f"{prefix}    ⏱ до: `{convert_to_local(key['expires_at'])}`\n"

            cfg = key.get("config") or ""
            proto = "🔗 VLESS" if "vless" in cfg else ("🛡 Trojan" if "trojan" in cfg else "📱")
            text += f"{prefix}    {proto}\n"
            if i < len(active_keys):
                text += f"{prefix}  \n"

    text += "\n_Нажмите на конфиг ниже, чтобы показать QR-код и ссылку_ ⬇️"

    keyboard: list = []
    row: list = []
    for i, key in enumerate(active_keys):
        short = key["client_name"][:15] + ("…" if len(key["client_name"]) > 15 else "")
        cfg   = key.get("config") or ""
        emoji = "🔗" if "vless" in cfg else ("🛡" if "trojan" in cfg else "📱")
        row.append(InlineKeyboardButton(f"{emoji} {short}", callback_data=f"show_key_{key['client_name']}"))
        if len(row) == 2 or i == len(active_keys) - 1:
            keyboard.append(row)
            row = []

    keyboard.append([InlineKeyboardButton("🆕 Новый конфиг", callback_data="tariffs")])
    keyboard.append([InlineKeyboardButton("◀️ В главное меню", callback_data="back_to_menu")])

    await safe_edit_text(query, text, reply_markup=InlineKeyboardMarkup(keyboard))


async def _show_no_configs(query):
    text = (
        "❄️ **У вас пока нет активных конфигов**\n\n"
        "┌─────────────────────\n"
        "│ Чтобы получить доступ к VPN:\n"
        "│ 1️⃣ Выберите подходящий тариф\n"
        "│ 2️⃣ Оплатите удобным способом\n"
        "│ 3️⃣ Получите готовый конфиг\n"
        "└─────────────────────\n\n"
        "✨ **Преимущества:**\n"
        "• ⚡️ Высокая скорость\n"
        "• 🔒 Безопасное шифрование\n"
        "• 📱 До 10 устройств\n"
        "• 🌐 Доступ к любым сайтам\n\n"
        "👇 **Нажмите на кнопку ниже, чтобы выбрать тариф**"
    )
    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔥 Выбрать тариф", callback_data="tariffs")],
            [InlineKeyboardButton("◀️ В меню",        callback_data="back_to_menu")],
        ]),
        parse_mode="Markdown",
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

    vless_link = key["config"]
    expires_at = key["expires_at"]
    is_active  = not expires_at or expires_at > datetime.utcnow()
    status     = ("✅", "Активен") if is_active else ("❌", "Истек")

    bio = BytesIO()
    bio.name = "qr.png"
    qr = qrcode.QRCode(version=1, box_size=8, border=4)
    qr.add_data(vless_link)
    qr.make(fit=True)
    qr.make_image(fill_color="black", back_color="white").save(bio, "PNG")
    bio.seek(0)

    protocol = "Trojan" if "trojan" in vless_link else ("Shadowsocks" if "shadowsocks" in vless_link else "VLESS")

    caption = (
        f"🔐 <b>{status[0]} Конфиг {key['client_name']}</b>\n\n"
        f"┌─ 📋 <b>Информация</b>\n"
        f"│  ▸ Протокол: <b>{protocol}</b>\n"
        f"│  ▸ Статус: <b>{status[1]}</b>\n"
        f"│  ▸ Действует до: <code>{convert_to_local(expires_at)}</code>\n"
        f"└─ 🔧 <b>Ссылка для подключения:</b>\n"
        f"<pre>{vless_link}</pre>\n\n"
        "💡 <i>Скопируйте ссылку или сохраните QR-код</i>"
    )

    await query.message.reply_photo(
        photo=bio,
        caption=caption,
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 К списку", callback_data="my_configs")]
        ]),
    )
