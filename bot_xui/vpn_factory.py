"""
Фабрика VPN-конфигов: создание AWG, VLESS, сохранение в БД.
"""
import json
import logging
import secrets
import time
import uuid
import httpx
from datetime import datetime, timedelta, timezone
from io import BytesIO

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from config import (
    AMNEZIA_WG_API_URL, AMNEZIA_WG_API_PASSWORD,
    VLESS_DOMAIN, VLESS_PORT, VLESS_PATH,
    VLESS_PBK, VLESS_SID, VLESS_SNI, VLESS_INBOUND_ID,
    HYSTERIA_PORT, HYSTERIA_SNI, HYSTERIA_INBOUND_ID,
    VLESS_HTTP_PORT, VLESS_XHTTP_PBK, VLESS_XHTTP_SID, VLESS_HTTP_INBOUND_ID, VLESS_WS_INBOUND_ID, VLESS_REALITY_V1_INBOUND_ID,
    ACTIVE_INBOUND_IDS, SERVER_LOCATION,
)
from bot_xui.utils import XUIClient, generate_vless_link, generate_hysteria2_link
from bot_xui.helpers import make_back_keyboard, _log_message, safe_edit_text, safe_edit_text_logged, make_qr_bytes, WEB_BASE_URL
from bot_xui.tariffs import TARIFFS
from api.db import (
    upsert_vpn_key, set_awg_test_activated, set_vless_test_activated,
    is_awg_test_activated, is_vless_test_activated,
    get_keys_by_tg_id, sync_expiry, get_subscription_until,
    get_web_token,
)

logger = logging.getLogger(__name__)



async def create_awg_config(tg_id: int, client_name: str = None) -> dict:
    """
    Создаёт клиента в AmneziaWG и возвращает dict с полями:
        client_name, client_id, client_ip, config
    Бросает RuntimeError при любой ошибке.
    """
    if client_name is None:
        client_name = f"test-{tg_id}-{uuid.uuid4().hex[:8]}"

    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.post(
            f"{AMNEZIA_WG_API_URL}/api/session",
            json={"password": AMNEZIA_WG_API_PASSWORD},
        )
        r.raise_for_status()

        r = await client.post(
            f"{AMNEZIA_WG_API_URL}/api/wireguard/client",
            json={"name": client_name},
        )
        r.raise_for_status()

        r = await client.get(f"{AMNEZIA_WG_API_URL}/api/wireguard/client")
        r.raise_for_status()

        client_id = client_ip = None
        for c in r.json():
            if c.get("name") == client_name:
                client_id = c["id"]
                client_ip = c.get("address")
                break

        if not client_id:
            raise RuntimeError("Клиент не найден после создания")

        r = await client.get(
            f"{AMNEZIA_WG_API_URL}/api/wireguard/client/{client_id}/configuration"
        )
        r.raise_for_status()

        config_text = r.text
        if not config_text:
            raise RuntimeError("Пустая конфигурация AWG")

    return {"client_name": client_name, "client_id": client_id,
            "client_ip": client_ip, "config": config_text}


def _get_dynamic_remark(expires_at: datetime) -> str:
    """Генерирует понятный Remark: '🐿 TIIN 🇩🇪 | до 25.05'"""
    from config import SERVER_LOCATION
    flag = {
        "Germany": "🇩🇪",
        "Netherlands": "🇳🇱",
        "Finland": "🇫🇮",
        "USA": "🇺🇸",
        "Russia": "🇷🇺",
        "Japan": "🇯🇵",
        "Singapore": "🇸🇬",
        "France": "🇫🇷",
        "UK": "🇬🇧",
        "Canada": "🇨🇦",
    }.get(SERVER_LOCATION, "🌍")
    base = f"🐿 TIIN {flag} {SERVER_LOCATION}"
    if not expires_at:
        return f"{base} | ♾"
    days_left = (expires_at - datetime.now(timezone.utc)).days
    if days_left < 0:
        return f"{base} | ✗"
    if days_left == 0:
        return f"{base} | сегодня"
    return f"{base} | {days_left}дн"


async def create_xui_multi_config(tg_id: int, xui: XUIClient, days: int = None) -> dict:
    """
    Создаёт VLESS и Hysteria клиентов через новый API 3x-ui (v3.x+).
    Один запрос /panel/api/clients/add создаёт клиента и привязывает к обоим inbounds.
    Возвращает dict с полями:
        client_email, client_uuid, vless_link, hysteria_link, expires_at, sub_id
    Бросает RuntimeError при критической ошибке.
    """
    client_email = f"tiin_{tg_id}"
    client_uuid = str(uuid.uuid4())
    tz_tokyo = timezone(timedelta(hours=9))

    if days is None:
        days_to_add = TARIFFS["test_24h"]["hours"] / 24
    else:
        days_to_add = days

    raw_end = datetime.now(timezone.utc) + timedelta(days=days_to_add)
    end_tokyo = raw_end.astimezone(tz_tokyo).replace(hour=23, minute=59, second=59, microsecond=0)
    expiry_ms = int(end_tokyo.timestamp() * 1000)
    expires_at = end_tokyo.astimezone(timezone.utc)

    existing = xui.get_client_by_email(client_email)
    if existing:
        logger.info(f"Client for {client_email} already exists, reusing")
        xui.extend_client_expiry(existing['inbound_id'], existing['client'], expiry_ms - int(time.time() * 1000))
        sub_url = xui.get_client_subscription_url(tg_id)
        if sub_url:
            sub_id = sub_url.split('/')[-1]
        else:
            raise RuntimeError(f"Клиент {client_email} уже существует, но не удалось получить subId")
        client_uuid = existing['client'].get('uuid', client_uuid)
        hysteria_auth = existing['client'].get('auth') or client_uuid
    else:
        result = xui.create_client(
            email=client_email,
            tg_id=tg_id,
            expiry_time=expiry_ms,
            inbound_ids=ACTIVE_INBOUND_IDS,
        )

        if not result.get("success"):
            raise RuntimeError(f"Не удалось создать клиента: {result.get('msg', '')}")
        sub_id = result["subId"]
        if result.get("uuid"):
            client_uuid = result["uuid"]
        hysteria_auth = result.get("auth") or client_uuid

    dynamic_remark = _get_dynamic_remark(expires_at)

    vless_link = generate_vless_link(
        client_id=client_uuid,
        domain=VLESS_DOMAIN,
        port=VLESS_PORT,
        path=VLESS_PATH,
        client_name=client_email,
        pbk=VLESS_PBK,
        sid=VLESS_SID,
        sni=VLESS_SNI,
        fp="chrome",
        spx="/",
        remark=dynamic_remark,
    )

    hysteria_link = generate_hysteria2_link(
        auth=hysteria_auth,
        domain=VLESS_DOMAIN,
        port=HYSTERIA_PORT,
        client_name=dynamic_remark,
        sni=HYSTERIA_SNI,
        insecure=0
    )

    xhttp_link = generate_vless_link(
        client_id=client_uuid,
        domain=VLESS_DOMAIN,
        port=VLESS_HTTP_PORT,
        path="/api/v1/updates",
        client_name=client_email,
        pbk=VLESS_XHTTP_PBK or VLESS_PBK,
        sid=VLESS_XHTTP_SID or VLESS_SID,
        sni=VLESS_SNI,
        fp="firefox",
        spx="/",
        remark=dynamic_remark,
        network="xhttp",
    )

    return {
        "client_email": client_email,
        "client_uuid": client_uuid,
        "vless_link": vless_link,
        "xhttp_link": xhttp_link,
        "hysteria_link": hysteria_link,
        "expires_at": expires_at,
        "sub_id": sub_id,
    }


# ──────────────────────────────────────────────────────────────────────────────
# Referral VPN reward
# ──────────────────────────────────────────────────────────────────────────────

async def create_vless_config(tg_id: int, xui: XUIClient) -> dict:
    """Обертка для обратной совместимости старых тестов."""
    data = await create_xui_multi_config(tg_id, xui)
    return {
        "client_email": data["client_email"],
        "client_uuid": data["client_uuid"],
        "vless_link": data["vless_link"],
        "expires_at": data["expires_at"]
    }


async def grant_referral_vpn(tg_id: int, days: int, xui: XUIClient) -> dict | None:
    """
    Выдаёт или продлевает VPN за реферальную награду.
    Если у пользователя есть активный XUI конфиг — продлевает его в обоих инбаундах.
    Если нет — создаёт новый (VLESS + Hysteria).
    Возвращает dict с информацией о конфиге или None при ошибке.
    """
    try:
        duration_ms = days * 86400 * 1000

        # Check for existing active config (usually found in VLESS inbound)
        existing = xui.get_client_by_tg_id(tg_id)

        if existing:
            # Продлеваем основной (VLESS)
            result = xui.extend_client_expiry(
                existing['inbound_id'],
                existing['client'],
                duration_ms,
            )
            if not result:
                logger.error(f"Failed to extend referral VPN for {tg_id}")
                return None

            # Продлеваем дополнительный (Hysteria), если есть
            hysteria_inbound_id = xui.get_hysteria_inbound_id()
            hysteria_client = xui.get_client_by_email(f"{existing['client']['email']}_h")
            # Мы ищем по email с суффиксом _h
            if hysteria_client and hysteria_client['inbound_id'] == hysteria_inbound_id:
                 xui.extend_client_expiry(
                    hysteria_inbound_id,
                    hysteria_client['client'],
                    duration_ms,
                )

            # Sync new expiry (result is new_expiry_ms) to MySQL
            new_expiry_ms = result
            new_expiry_dt = datetime.fromtimestamp(new_expiry_ms / 1000, tz=timezone.utc)
            sync_expiry(tg_id, new_expiry_dt)

            logger.info(f"Referral: extended VPN for {tg_id} by {days} days")
            return {"action": "extended", "days": days}

        # No existing config — create new Multi-protocol
        data = await create_xui_multi_config(tg_id, xui, days=days)
        if not data:
            return None

        sub_url = xui.get_client_subscription_url(tg_id)
        expires_at = data["expires_at"]

        upsert_vpn_key(
            tg_id=tg_id, payment_id=None,
            client_id=data["client_uuid"], client_name=data["client_email"],
            client_ip=None, client_public_key=None,
            vless_link=data["vless_link"], hysteria_link=data.get("hysteria_link"), expires_at=expires_at, vpn_type="vless",
            subscription_link=sub_url,
        )

        sync_expiry(tg_id, expires_at)

        logger.info(f"Referral: created new Multi-VPN for {tg_id}, {days} days")
        return {"action": "created", "days": days, "vless_link": data["vless_link"], "sub_url": sub_url}

    except Exception as e:
        logger.error(f"Referral VPN grant error for {tg_id}: {e}", exc_info=True)
        return None







# ──────────────────────────────────────────────────────────────────────────────
# Handlers — вызываются из button_handler
# ──────────────────────────────────────────────────────────────────────────────

async def handle_test_awg(query, xui: XUIClient):
    """Создаёт тестовый AWG конфиг и отправляет пользователю."""
    tg_id = query.from_user.id
    if is_awg_test_activated(tg_id):
        from bot_xui.views import show_configs
        await show_configs(query, xui)
        return
    try:
        await query.message.delete()
    except Exception:
        pass
    await query.message.chat.send_message("⏳ Создаю тестовый AmneziaWG конфиг...")

    try:
        data = await create_awg_config(tg_id)
        expiry_at = datetime.now(timezone.utc) + timedelta(hours=TARIFFS["test_24h"]["hours"])

        upsert_vpn_key(
            tg_id=tg_id, payment_id=None,
            client_id=data["client_id"], client_name=data["client_name"],
            client_ip=data["client_ip"], client_public_key=None,
            vless_link=data["config"], expires_at=expiry_at, vpn_type="awg",
        )

        config_file = BytesIO(data["config"].encode("utf-8"))
        config_file.name = f"amneziawg_test_{tg_id}.conf"

        caption = (
            f"🔵 <b>Тестовый AmneziaWG конфиг</b>\n\n"
            f"👤 Клиент: <code>{data['client_name']}</code>\n"
            f"🌐 IP: <code>{data['client_ip']}</code>\n"
            f"⏱ Действителен: {TARIFFS['test_24h']['period']}\n\n"
            f"📱 <b>Инструкция:</b>\n"
            f"1. Установите <a href='https://amnezia.org'>AmneziaVPN</a>\n"
            f"2. Импортируйте файл конфигурации\n"
            f"3. Подключитесь\n\n"
            f"💬 Поддержка: кнопка «Написать нам» в меню"
        )

        await query.message.reply_document(
            document=config_file,
            caption=caption,
            parse_mode="HTML",
        )

        set_awg_test_activated(tg_id)

        await query.message.reply_text(
            "✅ Конфиг создан!\n\nПроверьте сообщение выше ☝️",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("◀️ В главное меню", callback_data="back_to_menu")]
            ]),
        )

        await _log_message(tg_id, "bot_menu", "test_awg_config", f"sent document: {config_file.name}")

    except Exception as e:
        logger.error(f"AWG config error: {e}")
        await query.message.reply_text(
            "❌ Ошибка создания конфига\n\nПопробуйте позже или выберите VLESS.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("◀️ В меню", callback_data="back_to_menu")]
            ]),
        )


async def handle_get_awg_config(query):
    """Выдаёт AWG конфиг пользователю с активной VLESS подпиской (winback сценарий)."""
    tg_id = query.from_user.id

    # Проверяем, нет ли уже AWG ключа
    existing_keys = get_keys_by_tg_id(tg_id)
    has_awg = any(k['vpn_type'] == 'awg' for k in existing_keys)
    if has_awg:
        await safe_edit_text(query,
            "✅ У вас уже есть AmneziaWG конфиг.\n\n"
            "Нажмите «Мои конфиги» чтобы посмотреть.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📱 Мои конфиги", callback_data="my_configs")],
            ]),
        )
        return

    # Проверяем активную подписку
    sub_until = get_subscription_until(tg_id)
    if not sub_until or sub_until < datetime.utcnow():
        await safe_edit_text(query,
            "❌ У вас нет активной подписки.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("💎 Тарифы", callback_data="tariffs")],
            ]),
        )
        return

    await safe_edit_text(query, "⏳ Создаю AmneziaWG конфиг...")

    try:
        client_name = f"awg_{tg_id}"
        data = await create_awg_config(tg_id, client_name=client_name)

        upsert_vpn_key(
            tg_id=tg_id, payment_id=None,
            client_id=data["client_id"], client_name=data["client_name"],
            client_ip=data["client_ip"], client_public_key=None,
            vless_link=data["config"], expires_at=sub_until, vpn_type="awg",
        )

        config_file = BytesIO(data["config"].encode("utf-8"))
        config_file.name = f"amneziawg_{tg_id}.conf"

        await query.message.reply_document(
            document=config_file,
            caption=(
                f"🔵 <b>AmneziaWG конфиг</b>\n\n"
                f"Этот протокол лучше работает на нестабильных каналах, "
                f"мобильном интернете и в удалённых регионах.\n\n"
                f"📱 <b>Инструкция:</b>\n"
                f"1. Установите <a href='https://amnezia.org'>AmneziaVPN</a>\n"
                f"2. Импортируйте файл конфигурации\n"
                f"3. Подключитесь\n\n"
                f"⏱ Действует до: {sub_until.strftime('%d.%m.%Y')}"
            ),
            parse_mode="HTML",
        )

        await safe_edit_text(query,
            "✅ AmneziaWG конфиг создан!\n\nПроверьте сообщение выше ☝️",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📱 Мои конфиги", callback_data="my_configs")],
                [InlineKeyboardButton("◀️ В главное меню", callback_data="back_to_menu")],
            ]),
        )

        await _log_message(tg_id, "bot_menu", "awg_config", f"sent document: {config_file.name}")

    except Exception as e:
        logger.error(f"AWG config error (winback): {e}")
        await query.message.reply_text(
            "❌ Ошибка создания конфига. Попробуйте позже или напишите в поддержку.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("◀️ В меню", callback_data="back_to_menu")]
            ]),
        )


async def handle_get_awg_config_v2(query):
    """Выдаёт AWG 2.0 конфиг пользователю с истёкшей подпиской (winback)."""
    tg_id = query.from_user.id

    existing_keys = get_keys_by_tg_id(tg_id)
    has_awg = any(k['vpn_type'] == 'awg' for k in existing_keys)
    if has_awg:
        await safe_edit_text(query,
            "✅ У вас уже есть AmneziaWG конфиг.\n\n"
            "Нажмите «Мои конфиги» чтобы посмотреть.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📱 Мои конфиги", callback_data="my_configs")],
            ]),
        )
        return

    await safe_edit_text(query, "⏳ Создаю AmneziaWG 2.0 конфиг...")

    try:
        client_name = f"awg2_{tg_id}"
        data = await create_awg_config(tg_id, client_name=client_name)
        sub_until = get_subscription_until(tg_id)

        upsert_vpn_key(
            tg_id=tg_id, payment_id=None,
            client_id=data["client_id"], client_name=data["client_name"],
            client_ip=data["client_ip"], client_public_key=None,
            vless_link=data["config"], expires_at=sub_until, vpn_type="awg",
        )

        config_file = BytesIO(data["config"].encode("utf-8"))
        config_file.name = f"amneziawg2_{tg_id}.conf"

        await query.message.reply_document(
            document=config_file,
            caption=(
                f"🔵 <b>AmneziaWG 2.0 конфиг</b>\n\n"
                f"Новый протокол — улучшенная совместимость и стабильность.\n"
                f"Лучше работает на нестабильных каналах и мобильном интернете.\n\n"
                f"📱 <b>Инструкция:</b>\n"
                f"1. Установите <a href='https://amnezia.org'>AmneziaVPN</a>\n"
                f"2. Импортируйте файл конфигурации\n"
                f"3. Подключитесь\n\n"
                f"💡 <i>Ваша подписка истекла — для полноценной работы рекомендуем продлить тариф.</i>"
            ),
            parse_mode="HTML",
        )

        await safe_edit_text(query,
            "✅ AmneziaWG 2.0 конфиг создан!\n\nПроверьте сообщение выше ☝️",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("💎 Продлить подписку", callback_data="tariffs")],
                [InlineKeyboardButton("📱 Мои конфиги", callback_data="my_configs")],
                [InlineKeyboardButton("◀️ В главное меню", callback_data="back_to_menu")],
            ]),
        )

        await _log_message(tg_id, "bot_menu", "awg2_config", f"sent document: {config_file.name}")

    except Exception as e:
        logger.error(f"AWG 2.0 config error: {e}")
        await query.message.reply_text(
            "❌ Ошибка создания конфига. Попробуйте позже или напишите в поддержку.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("◀️ В меню", callback_data="back_to_menu")]
            ]),
        )





async def handle_test_vless(query, xui: XUIClient):
    """Создаёт тестовый VLESS+Hysteria конфиг и отправляет пользователю."""
    tg_id = query.from_user.id
    if is_vless_test_activated(tg_id):
        from bot_xui.views import show_configs
        await show_configs(query, xui)
        return
    try:
        await query.message.delete()
    except Exception:
        pass
    await query.message.chat.send_message("⏳ Создаю тестовый конфиг (VLESS + Hysteria)...")

    try:
        data = await create_xui_multi_config(tg_id, xui)
        sub_url = xui.get_client_subscription_url(tg_id)

        upsert_vpn_key(
            tg_id=tg_id, payment_id=None,
            client_id=data["client_uuid"], client_name=data["client_email"],
            client_ip=None, client_public_key=None,
            vless_link=data["vless_link"], xhttp_link=data["xhttp_link"], hysteria_link=data["hysteria_link"], expires_at=data["expires_at"], vpn_type="vless",
            subscription_link=sub_url,
        )
        web_token = get_web_token(tg_id)
        qr_url = f"{WEB_BASE_URL}/my/{web_token}" if web_token else ""
        bio = make_qr_bytes(qr_url) if qr_url else None

        from config import SERVER_LOCATION
        instr_url = f"https://344988.snk.wtf/my/{web_token}" if web_token else ""
        await query.message.reply_photo(
            photo=bio,
            caption=(
                f"🚀 <b>Тестовый период активирован!</b>\n\n"
                f"🌍 Сервер: <b>{SERVER_LOCATION}</b>\n\n"
                f"🟢 <b>VLESS + Reality (TCP)</b>\n"
                f"   Стабильный протокол для всех платформ\n\n"
                f"🟠 <b>VLESS + Reality (XHTTP)</b>\n"
                f"   Оптимизирован для работы через HTTP\n\n"
                f"🚀 <b>Hysteria 2</b>\n"
                f"   Скоростной протокол для обхода блокировок\n\n"
                f"⏱ Действителен: {TARIFFS['test_24h']['period']}\n\n"
                f'📲 <a href="{instr_url}">Инструкция по подключению</a>\n\n'
                f"💬 Поддержка: кнопка «Написать нам» в меню"
            ),
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📖 Инструкция", url=instr_url)],
                [InlineKeyboardButton("◀️ В меню", callback_data="back_to_menu")],
            ]),
        )

        set_vless_test_activated(tg_id)
        sync_expiry(tg_id, data["expires_at"])

        await _log_message(tg_id, "bot_menu", "test_vless_config", "sent photo: test_vless_qr")

    except Exception as e:
        logger.error(f"XUI multi-config error: {e}")
        await query.message.reply_text(
            "❌ Ошибка создания конфига\n\nПопробуйте позже.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("◀️ В меню", callback_data="back_to_menu")]
            ]),
        )


async def ensure_test_subscription(tg_id: int, xui: XUIClient) -> dict | None:
    """
    Создаёт тестовый Multi-XUI-конфиг, если пользователь ещё не активировал тест.
    Возвращает dict с данными конфига или None, если тест уже активирован либо возникла ошибка.
    """
    if is_vless_test_activated(tg_id):
        return None
    try:
        data = await create_xui_multi_config(tg_id, xui)
        sub_url = xui.get_client_subscription_url(tg_id)
        upsert_vpn_key(
            tg_id=tg_id, payment_id=None,
            client_id=data["client_uuid"], client_name=data["client_email"],
            client_ip=None, client_public_key=None,
            vless_link=data["vless_link"], xhttp_link=data["xhttp_link"], hysteria_link=data["hysteria_link"], expires_at=data["expires_at"], vpn_type="vless",
            subscription_link=sub_url,
        )
        set_vless_test_activated(tg_id)
        sync_expiry(tg_id, data["expires_at"])
        logger.info(f"Auto-granted test Multi-XUI for tg_id={tg_id}")
        return {**data, "sub_url": sub_url}
    except Exception as e:
        logger.error(f"Auto-grant test Multi-XUI failed for {tg_id}: {e}")
        return None


async def auto_grant_test_and_notify(tg_id: int, xui: XUIClient, reply_photo_func) -> bool:
    """
    Если у пользователя нет активных ключей и тест ещё не активирован,
    автоматически создаёт тестовый VLESS-конфиг и отправляет сообщение с QR.
    Возвращает True, если тест был выдан, иначе False.
    """
    from datetime import datetime

    if is_vless_test_activated(tg_id):
        return False
    keys = get_keys_by_tg_id(tg_id)
    active_keys = [k for k in keys if k.get("expires_at") and k["expires_at"] > datetime.utcnow()]
    if active_keys:
        return False
    result = await ensure_test_subscription(tg_id, xui)
    if not result:
        return False
    web_token = get_web_token(tg_id)
    qr_url = f"{WEB_BASE_URL}/my/{web_token}" if web_token else ""
    bio = make_qr_bytes(qr_url) if qr_url else None
    from config import SERVER_LOCATION
    try:
        await reply_photo_func(
            photo=bio,
            caption=(
                f"🎁 <b>Тестовый период активирован!</b>\n\n"
                f"🌍 Сервер: <b>{SERVER_LOCATION}</b>\n"
                f"🟢 VLESS TCP · 🟠 VLESS XHTTP · 🚀 Hysteria 2\n"
                f"👤 ID: {result['client_email']}\n"
                f"⏱ Действует: {TARIFFS['test_24h']['period']}\n\n"
                f'📲 <a href="https://344988.snk.wtf/my/{web_token}">Инструкция по подключению</a>'
            ),
            parse_mode="HTML",
        )
        await _log_message(tg_id, "bot_menu", "auto_test_grant", "sent photo: auto_test_qr")
    except Exception as e:
        logger.warning(f"Failed to send auto-test notification: {e}")
    return True


async def activate_test_period(query, xui):
    """Активирует тестовый период для пользователя"""
    from datetime import datetime
    from bot_xui.vpn_factory import ensure_test_subscription

    tg_id = query.from_user.id

    if is_vless_test_activated(tg_id):
        try:
            await query.message.delete()
        except Exception:
            pass
        await query.message.chat.send_message(
            "❌ Тестовый период уже был активирован ранее.\n\n"
            "Ты можешь приобрести тариф, чтобы продолжить пользоваться VPN.",
            reply_markup=make_back_keyboard()
        )
        return

    keys = get_keys_by_tg_id(tg_id)
    now = datetime.utcnow()
    active_vless_keys = [k for k in keys if k.get("vpn_type") == "vless" and k.get("expires_at") and k["expires_at"] > now]

    if active_vless_keys:
        try:
            await query.message.delete()
        except Exception:
            pass
        await query.message.chat.send_message(
            "✅ У тебя уже есть активная подписка!\n\n"
            "Ты можешь посмотреть свои конфиги в разделе «🔑 Мои конфиги».",
            reply_markup=make_back_keyboard()
        )
        return

    try:
        await query.message.delete()
    except Exception:
        pass
    await query.message.chat.send_message(
        "🎁 Активируем тестовый период...\n\n"
        "⏳ Пожалуйста, подожди несколько секунд.",
    )

    result = await ensure_test_subscription(tg_id, xui)

    if not result:
        await query.message.reply_text(
            "❌ Не удалось активировать тестовый период.\n\n"
            "Пожалуйста, попробуй позже или обратись в поддержку.",
            reply_markup=make_back_keyboard()
        )
        return

    web_token = get_web_token(tg_id)
    qr_url = f"{WEB_BASE_URL}/my/{web_token}" if web_token else ""
    bio = make_qr_bytes(qr_url) if qr_url else None

    from config import SERVER_LOCATION
    caption_text = (
        f"🎉 <b>Тестовый период активирован!</b>\n\n"
        f"🌍 Сервер: <b>{SERVER_LOCATION}</b>\n\n"
        f"🟢 <b>VLESS + Reality TCP</b> — стабильный\n"
        f"🟠 <b>VLESS + Reality XHTTP</b> — оптимизированный\n"
        f"🚀 <b>Hysteria 2</b> — скоростной\n\n"
        f"👤 ID: <code>{result['client_email']}</code>\n"
        f"⏱ Действует: {TARIFFS['test_24h']['period']}\n\n"
        f"📲 <b>Как подключиться:</b>\n"
        f"Нажмите кнопку <b>«📖 Инструкция»</b> ниже — там пошагово показано, как настроить подключение для вашего устройства.\n\n"
        f"💎 После окончания теста выберите тариф для продолжения."
    )
    fallback_text = (
        f"🎉 <b>Тестовый период активирован!</b>\n\n"
        f"🌍 Сервер: <b>{SERVER_LOCATION}</b>\n"
        f"👤 ID: <code>{result['client_email']}</code>\n"
        f"⏱ Действует: {TARIFFS['test_24h']['period']}\n\n"
        f'📖 <a href="https://344988.snk.wtf/my/{web_token}">Инструкция</a>'
    )
    try:
        await query.message.reply_photo(
            photo=bio,
            caption=caption_text,
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📖 Инструкция", url=f"https://344988.snk.wtf/my/{web_token}")],
                [InlineKeyboardButton("💎 Выбрать тариф", callback_data="tariffs")],
                [InlineKeyboardButton("◀️ В меню", callback_data="back_to_menu")],
            ])
        )
        sent_text = caption_text
    except Exception as e:
        logger.error(f"Failed to send test config: {e}")
        await query.message.reply_text(
            fallback_text,
            parse_mode="HTML",
            reply_markup=make_back_keyboard("💎 Выбрать тариф", "tariffs")
        )
        sent_text = fallback_text

    await _log_message(tg_id, "bot_menu", "test_activation", sent_text)






