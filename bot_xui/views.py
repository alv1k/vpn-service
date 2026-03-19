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

_INSTRUCTION_APPS = [
    ("🤖 Hiddify - Android",       "https://play.google.com/store/apps/details?id=app.hiddify.com"),
    ("🤖 Happ - Android",          "https://play.google.com/store/apps/details?id=com.happproxy&hl=ru"),
    ("🍏 Happ - iOS",              "https://apps.apple.com/app/happ-proxy-utility/id6504287215"),
    ("🍏 Streisand - iOS",          "https://apps.apple.com/app/streisand/id6450534064"),
    ("💻 Hiddify - Windows/macOS", "https://github.com/hiddify/hiddify-app/releases"),
    ("📺 VPN4TV - Android TV",     "https://play.google.com/store/apps/details?id=com.vpn4tv.hiddify"),
    ("🖥 SoftEther - Windows",     "https://www.softether-download.com/en.aspx?product=softether"),
]

async def show_instructions(query):
    caption = (
        "📱 <b>Инструкция по подключению:</b>\n\n"
        "<b>1️⃣</b> Выберите приложение для вашей ОС (кнопки ниже)\n"
        "<b>2️⃣</b> Отсканируйте QR-код или скопируйте ссылку\n"
        "<b>3️⃣</b> Подключитесь к VPN\n\n"
        "💬 <b>Поддержка:</b> кнопка «Написать нам» в меню"
    )
    keyboard = [
        [InlineKeyboardButton(label, url=url)] for label, url in _INSTRUCTION_APPS
    ] + [
        [InlineKeyboardButton("◀️ В меню", callback_data="back_to_menu")],
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
    title = "💎 <b>Выберите длительность продления</b>" if mode == "renew" else "💎 <b>Доступные тарифы VPN</b>"
    text = title + "\n\n"

    if perm_discount > 0:
        text += f"🏷 <i>Ваша постоянная скидка: <b>{perm_discount}%</b></i>\n\n"

    if test_tariffs and not (awg_used or vless_used) and mode == "buy":
        text += "🎁 <b>Попробуйте бесплатно</b>\n┌─────────────────────\n"
        for t in test_tariffs:
            text += (
                f"│ ✨ <b>{t['name']}</b>\n"
                f"│    ▸ Цена: <b>{t['price']} ₽</b>\n"
                f"│    ▸ Период: {t['period']}\n"
                f"│    ▸ Устройств: {t['device_limit']}\n"
            )
        text += "└─────────────────────\n\n"

    text += "📦 <b>Основные тарифы</b>\n"
    for i, t in enumerate(regular_tariffs):
        bullet = "├" if i < len(regular_tariffs) - 1 else "└"
        if perm_discount > 0:
            discounted = max(1, round(t['price'] * (100 - perm_discount) / 100))
            ppd = discounted / t["days"] if t.get("days") else 0
            text += f"{bullet}─ <b>{t['name']}</b>\n"
            text += f"{bullet}   💰 <s>{t['price']} ₽</s> → <b>{discounted} ₽</b>  ·  ⏱ {t['period']}  ·  👥 {t['device_limit']} устройств\n"
        else:
            ppd = t["price"] / t["days"] if t.get("days") else 0
            text += f"{bullet}─ <b>{t['name']}</b>\n"
            text += f"{bullet}   💰 {t['price']} ₽  ·  ⏱ {t['period']}  ·  👥 {t['device_limit']} устройств\n"
        if t.get("days", 0) > 3:
            text += f"{bullet}   💫 всего {ppd:.1f} ₽/день\n"
        if t.get("features"):
            text += f"{bullet}   ✨ {', '.join(t['features'])}\n"
        if t.get("days", 0) >= 90:
            text += f"{bullet}   🌟 <b>Самый выгодный!</b>\n"
        if i < len(regular_tariffs) - 1:
            text += f"{bullet}  \n"

    if special_tariffs and tg_id == ADMIN_TG_ID and mode == "buy":
        text += "\n⚙️ <b>Служебные тарифы</b>\n"
        for t in special_tariffs:
            text += f"└─ 🔧 {t['name']}  💰 {t['price']} ₽ · {t['period']}\n"

    text += "\n<i>Выберите подходящий тариф ниже:</i> ⬇️"

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

    back_label = "◀️ Вернуться в меню"
    back_data  = "back_to_menu"
    keyboard.append([InlineKeyboardButton(back_label, callback_data=back_data)])

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

    text = "🔐 <b>Ваши VPN конфиги</b>\n\n"
    if active_keys:
        text += "✅ <b>Активные:</b>\n"
        for i, key in enumerate(active_keys, 1):
            prefix = "├─" if i < len(active_keys) else "└─"
            if key["vpn_type"] == "softether":
                emoji = "🖥"
            elif "vless" in key["vpn_type"]:
                emoji = "🟢"
            else:
                emoji = "📱"
            text += f"{prefix} {emoji} <b>{key['client_name']}</b>\n"
            text += f"{prefix}    ⏱ до: <code>{convert_to_local(key['expires_at'])}</code>\n\n"

    text += "\n<i>Нажмите на конфиг ниже, чтобы показать данные подключения</i> ⬇️"

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

    keyboard.append([InlineKeyboardButton("◀️ В главное меню", callback_data="back_to_menu")])

    await safe_edit_text(query, text, reply_markup=InlineKeyboardMarkup(keyboard))


async def _show_no_configs(query):
    text = (
        "❄️ <b>У вас пока нет активных конфигов</b>\n\n"
        "┌─────────────────────\n"
        "│ Чтобы получить доступ к VPN:\n"
        "│ 1️⃣ Выберите подходящий тариф\n"
        "│ 2️⃣ Оплатите удобным способом\n"
        "│ 3️⃣ Получите готовый конфиг\n"
        "└─────────────────────\n\n"
        "✨ <b>Преимущества:</b>\n"
        "• ⚡️ Высокая скорость\n"
        "• 🔒 Безопасное шифрование\n"
        "• 📱 До 10 устройств\n"
        "• 🌐 Доступ к любым сайтам\n\n"
        "👇 <b>Нажмите на кнопку ниже, чтобы выбрать тариф</b>"
    )
    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔥 Выбрать тариф", callback_data="tariffs")],
            [InlineKeyboardButton("◀️ В меню",        callback_data="back_to_menu")],
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
            f"🖥 <b>{status[0]} Конфиг {key['client_name']}</b>\n\n"
            f"┌─ 📋 <b>Информация</b>\n"
            f"│  ▸ Статус: <b>{status[1]}</b>\n"
            f"│  ▸ Действует до: <code>{convert_to_local(expires_at)}</code>\n"
            f"└─────────────────────\n\n"
            f"📋 <b>Данные для подключения:</b>\n"
            f"┌─────────────────────\n"
            f"│ 🌐 Сервер: <code>{creds.get('host', '')}</code>\n"
            f"│ 🔌 Порт: <code>{creds.get('port', '')}</code>\n"
            f"│ 🏠 Hub: <code>{creds.get('hub', '')}</code>\n"
            f"│ 👤 Логин: <code>{creds.get('username', '')}</code>\n"
            f"│ 🔑 Пароль: <code>{creds.get('password', '')}</code>\n"
            f"└─────────────────────\n"
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
        f"🔐 <b>{status[0]} Конфиг {key['client_name']}</b>\n\n"
        f"┌─ 📋 <b>Информация</b>\n"
        f"│  ▸ Статус: <b>{status[1]}</b>\n"
        f"│  ▸ Действует до: <code>{convert_to_local(expires_at)}</code>\n"
        f"└─ 🔗 <b>Ссылка подписки:</b>\n\n"
        f"👇 <i>Нажмите чтобы скопировать:</i>\n"
        f"┌────────────────────\n"
        f"  <code>{sub_url}</code>\n"
        f"└────────────────────\n\n"
        f"<i>Добавьте в приложение — конфиг будет обновляться автоматически</i>\n"
    )

    caption += "\n💡 <i>Скопируйте ссылку или сохраните QR-код</i>"

    keyboard = [[InlineKeyboardButton("🔙 К списку", callback_data="my_configs")]]

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
