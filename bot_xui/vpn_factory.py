"""
Фабрика VPN-конфигов: создание AWG и VLESS, сохранение в БД.
"""
import json
import logging
import secrets
import time
import uuid
import httpx
from datetime import datetime, timedelta, timezone
from io import BytesIO

import qrcode
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from config import (
    AMNEZIA_WG_API_URL, AMNEZIA_WG_API_PASSWORD,
    VLESS_DOMAIN, VLESS_PORT, VLESS_PATH,
    VLESS_PBK, VLESS_SID, VLESS_SNI, VLESS_INBOUND_ID,
    SOFTETHER_CONNECT_HOST, SOFTETHER_CONNECT_PORT, SOFTETHER_HUB,
)
from bot_xui.utils import XUIClient, generate_vless_link
from bot_xui import softether
from bot_xui.tariffs import TARIFFS
from api.db import (
    create_vpn_key, set_awg_test_activated, set_vless_test_activated,
    is_awg_test_activated, is_vless_test_activated,
    set_softether_test_activated, is_softether_test_activated,
    get_keys_by_tg_id,
)

logger = logging.getLogger(__name__)


def make_qr_bytes(data: str, box_size: int = 10, border: int = 5) -> BytesIO:
    """Генерирует PNG QR-код и возвращает BytesIO."""
    qr = qrcode.QRCode(version=1, box_size=box_size, border=border)
    qr.add_data(data)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    bio = BytesIO()
    bio.name = "qr.png"
    img.save(bio, "PNG")
    bio.seek(0)
    return bio


async def create_awg_config(tg_id: int) -> dict:
    """
    Создаёт клиента в AmneziaWG и возвращает dict с полями:
        client_name, client_id, client_ip, config
    Бросает RuntimeError при любой ошибке.
    """
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


async def create_vless_config(tg_id: int, xui: XUIClient) -> dict:
    """
    Создаёт VLESS-клиента через XUI и возвращает dict с полями:
        client_email, client_uuid, vless_link, expires_at
    Бросает RuntimeError при любой ошибке.
    """
    client_email = f"test-{tg_id}-{uuid.uuid4().hex[:8]}"
    client_uuid = str(uuid.uuid4())
    tz_tokyo = timezone(timedelta(hours=9))
    raw_end = datetime.now(timezone.utc) + timedelta(hours=TARIFFS["test_24h"]["hours"])
    end_tokyo = raw_end.astimezone(tz_tokyo).replace(hour=23, minute=59, second=59, microsecond=0)
    expiry_ms = int(end_tokyo.timestamp() * 1000)
    inbound_id = int(VLESS_INBOUND_ID)

    success = xui.add_client(
        inbound_id=inbound_id,
        email=client_email,
        tg_id=tg_id,
        uuid=client_uuid,
        expiry_time=expiry_ms,
        total_gb=0,
        limit_ip=1,
    )
    if not success:
        raise RuntimeError("Не удалось создать VLESS клиента")

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
    )

    expires_at = end_tokyo.astimezone(timezone.utc)

    return {"client_email": client_email, "client_uuid": client_uuid,
            "vless_link": vless_link, "expires_at": expires_at}


# ──────────────────────────────────────────────────────────────────────────────
# Referral VPN reward
# ──────────────────────────────────────────────────────────────────────────────

async def grant_referral_vpn(tg_id: int, days: int, xui: XUIClient) -> dict | None:
    """
    Выдаёт или продлевает VPN за реферальную награду.
    Если у пользователя есть активный VLESS конфиг — продлевает его.
    Если нет — создаёт новый.
    Возвращает dict с информацией о конфиге или None при ошибке.
    """
    try:
        duration_ms = days * 86400 * 1000
        inbound_id = int(VLESS_INBOUND_ID)

        # Check for existing active config
        existing = xui.get_client_by_tg_id(tg_id)

        if existing:
            # Extend existing client
            success = xui.extend_client_expiry(
                existing['inbound_id'],
                existing['client'],
                duration_ms,
            )
            if not success:
                logger.error(f"Failed to extend referral VPN for {tg_id}")
                return None

            logger.info(f"Referral: extended VPN for {tg_id} by {days} days")
            return {"action": "extended", "days": days}

        # No existing config — create new VLESS
        client_email = f"ref_{tg_id}_{uuid.uuid4().hex[:8]}"
        client_uuid = str(uuid.uuid4())
        tz_tokyo = timezone(timedelta(hours=9))
        raw_end = datetime.now(timezone.utc) + timedelta(days=days)
        end_tokyo = raw_end.astimezone(tz_tokyo).replace(hour=23, minute=59, second=59, microsecond=0)
        expiry_ms = int(end_tokyo.timestamp() * 1000)

        success = xui.add_client(
            inbound_id=inbound_id,
            email=client_email,
            tg_id=tg_id,
            uuid=client_uuid,
            expiry_time=expiry_ms,
            total_gb=0,
            limit_ip=10,
        )
        if not success:
            logger.error(f"Failed to create referral VPN for {tg_id}")
            return None

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
        )

        sub_url = xui.get_client_subscription_url(tg_id)
        expires_at = end_tokyo.astimezone(timezone.utc)

        create_vpn_key(
            tg_id=tg_id, payment_id=None,
            client_id=client_uuid, client_name=client_email,
            client_ip=None, client_public_key=None,
            vless_link=vless_link, expires_at=expires_at, vpn_type="vless",
            subscription_link=sub_url,
        )

        logger.info(f"Referral: created new VPN for {tg_id}, {days} days")
        return {"action": "created", "days": days, "vless_link": vless_link, "sub_url": sub_url}

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
        await query.edit_message_text("❌ Тестовый AWG конфиг уже был создан.")
        return
    await query.edit_message_text("⏳ Создаю тестовый AmneziaWG конфиг...")

    try:
        data = await create_awg_config(tg_id)
        expiry_at = datetime.now(timezone.utc) + timedelta(hours=TARIFFS["test_24h"]["hours"])

        create_vpn_key(
            tg_id=tg_id, payment_id=None,
            client_id=data["client_id"], client_name=data["client_name"],
            client_ip=data["client_ip"], client_public_key=None,
            vless_link=data["config"], expires_at=expiry_at, vpn_type="awg",
        )

        config_file = BytesIO(data["config"].encode("utf-8"))
        config_file.name = f"amneziawg_test_{tg_id}.conf"

        await query.message.reply_document(
            document=config_file,
            caption=(
                f"🔵 <b>Тестовый AmneziaWG конфиг</b>\n\n"
                f"👤 Клиент: <code>{data['client_name']}</code>\n"
                f"🌐 IP: <code>{data['client_ip']}</code>\n"
                f"⏱ Действителен: {TARIFFS['test_24h']['period']}\n\n"
                f"📱 <b>Инструкция:</b>\n"
                f"1. Установите <a href='https://amnezia.org'>AmneziaVPN</a>\n"
                f"2. Импортируйте файл конфигурации\n"
                f"3. Подключитесь\n\n"
                f"💬 Поддержка: кнопка «Написать нам» в меню"
            ),
            parse_mode="HTML",
        )

        set_awg_test_activated(tg_id)

        await query.edit_message_text(
            "✅ Конфиг создан!\n\nПроверьте сообщение выше ☝️",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("◀️ В главное меню", callback_data="back_to_menu")]
            ]),
        )

    except Exception as e:
        logger.error(f"AWG config error: {e}")
        await query.message.reply_text(
            "❌ Ошибка создания конфига\n\nПопробуйте позже или выберите VLESS.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("◀️ В меню", callback_data="back_to_menu")]
            ]),
        )


async def handle_test_vless(query, xui: XUIClient):
    """Создаёт тестовый VLESS конфиг и отправляет пользователю."""
    tg_id = query.from_user.id
    if is_vless_test_activated(tg_id):
        await query.edit_message_text("❌ Тестовый VLESS конфиг уже был создан.")
        return
    await query.edit_message_text("⏳ Создаю тестовый VLESS конфиг...")

    try:
        data = await create_vless_config(tg_id, xui)
        sub_url = xui.get_client_subscription_url(tg_id)

        create_vpn_key(
            tg_id=tg_id, payment_id=None,
            client_id=data["client_uuid"], client_name=data["client_email"],
            client_ip=None, client_public_key=None,
            vless_link=data["vless_link"], expires_at=data["expires_at"], vpn_type="vless",
            subscription_link=sub_url,
        )

        bio = make_qr_bytes(sub_url)

        await query.message.reply_photo(
            photo=bio,
            caption=(
                f"🟢 <b>Тестовый VLESS конфиг</b>\n\n"
                f"👤 ID: {data['client_email']}\n"
                f"⏱ Действителен: {TARIFFS['test_24h']['period']}\n\n"
                f"<b>Инструкция:</b>\n"
                f"1. Установите приложение из раздела «Инструкция»\n"
                f"2. Отсканируйте QR или скопируйте ссылку\n"
                f"3. Подключитесь\n\n"
                f"💬 Поддержка: кнопка «Написать нам» в меню"
            ),
            parse_mode="HTML",
        )

        await query.message.reply_text(
            f"🔗 Ваша ссылка на подписку:\n"
            f"👇 <i>Нажмите чтобы скопировать:</i>\n"
            f"┌────────────────────\n"
            f"  <code>{sub_url}</code>\n"
            f"└────────────────────\n\n"
            f"📱 Скопируйте ссылку и вставьте в приложение",
            parse_mode="HTML",
        )

        set_vless_test_activated(tg_id)

        await query.message.reply_text(
            "Выберите действие:",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📑 Инструкция и ссылки", callback_data="instructions")],
                [InlineKeyboardButton("◀️ В меню",              callback_data="back_to_menu")],
            ]),
        )

    except Exception as e:
        logger.error(f"VLESS config error: {e}")
        await query.message.reply_text(
            "❌ Ошибка создания конфига\n\nПопробуйте позже.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("◀️ В меню", callback_data="back_to_menu")]
            ]),
        )


# ──────────────────────────────────────────────────────────────────────────────
# SoftEther
# ──────────────────────────────────────────────────────────────────────────────

def _softether_credentials_text(username: str, password: str) -> str:
    """Форматирует данные подключения SoftEther."""
    return (
        f"🖥 <b>Данные для подключения SoftEther VPN</b>\n\n"
        f"┌─────────────────────\n"
        f"│ 🌐 Сервер: <code>{SOFTETHER_CONNECT_HOST}</code>\n"
        f"│ 🔌 Порт: <code>{SOFTETHER_CONNECT_PORT}</code>\n"
        f"│ 🏠 Hub: <code>{SOFTETHER_HUB}</code>\n"
        f"│ 👤 Логин: <code>{username}</code>\n"
        f"│ 🔑 Пароль: <code>{password}</code>\n"
        f"└─────────────────────\n"
    )


def _make_softether_vpn_file(username: str, password: str) -> BytesIO:
    """Генерирует .vpn файл для импорта в SoftEther VPN Client."""
    import hashlib
    # SoftEther хранит пароль как SHA-0 хеш, но для import connection
    # используется открытый текст с HashedPassword + Plain password
    content = f"""# VPN Client VPN Connection Setting File
#
# This file is exported by SoftEther VPN Client.
# The contents of this file can be edited by a text editor.
#

declare root
{{
	bool CheckServerCert false
	uint64 CreateDateTime 0
	uint64 LastConnectDateTime 0
	bool StartupAccount false
	uint64 UpdateDateTime 0

	declare ClientAuth
	{{
		uint AuthType 1
		string Username {username}
		byte HashedPassword {hashlib.new('sha1', password.encode()).hexdigest()}
		string PlainPassword {password}
	}}

	declare ClientOption
	{{
		string AccountName TIIN_VPN
		uint AdditionalConnectionInterval 1
		uint ConnectionDisconnectSpan 0
		string DeviceName VPN
		bool DisableQoS false
		bool HalfConnection false
		bool HideNicInfoWindow false
		bool HideStatusWindow false
		string Hostname {SOFTETHER_CONNECT_HOST}
		string HubName {SOFTETHER_HUB}
		uint MaxConnection 1
		bool NoRoutingTracking false
		uint NumRetry 4294967295
		uint Port {SOFTETHER_CONNECT_PORT}
		uint RetryInterval 15
		bool UseCompress false
		bool UseEncrypt true
	}}
}}
"""
    bio = BytesIO(content.encode("utf-8"))
    bio.name = f"tiin_vpn_{username}.vpn"
    return bio


def create_softether_config(tg_id: int, days: int = None, hours: int = None) -> dict:
    """Создаёт пользователя SoftEther и возвращает данные подключения."""
    username = f"se_{tg_id}_{uuid.uuid4().hex[:8]}"
    password = secrets.token_urlsafe(9)

    success = softether.create_user(username, password)
    if not success:
        raise RuntimeError("Failed to create SoftEther user")

    tz_tokyo = timezone(timedelta(hours=9))
    if days:
        raw_end = datetime.now(timezone.utc) + timedelta(days=days)
    elif hours:
        raw_end = datetime.now(timezone.utc) + timedelta(hours=hours)
    else:
        raw_end = datetime.now(timezone.utc) + timedelta(days=30)

    end_tokyo = raw_end.astimezone(tz_tokyo).replace(hour=23, minute=59, second=59, microsecond=0)
    expires_at = end_tokyo.astimezone(timezone.utc)

    # Set expiry in SoftEther (date-only granularity)
    expiry_date_str = end_tokyo.strftime("%Y/%m/%d")
    softether.set_user_expiry(username, expiry_date_str)

    vpn_file_bio = _make_softether_vpn_file(username, password)
    vpn_file_content = vpn_file_bio.getvalue().decode("utf-8")

    config_json = json.dumps({
        "host": SOFTETHER_CONNECT_HOST,
        "port": SOFTETHER_CONNECT_PORT,
        "hub": SOFTETHER_HUB,
        "username": username,
        "password": password,
    })

    return {
        "username": username,
        "password": password,
        "config": config_json,
        "vpn_file": vpn_file_content,
        "expires_at": expires_at,
    }


async def handle_test_softether(query):
    """Создаёт тестовый SoftEther конфиг и отправляет пользователю."""
    tg_id = query.from_user.id
    if is_softether_test_activated(tg_id):
        await query.edit_message_text("❌ Тестовый SoftEther конфиг уже был создан.")
        return
    await query.edit_message_text("⏳ Создаю тестовый SoftEther конфиг...")

    try:
        data = create_softether_config(tg_id, hours=TARIFFS["test_24h"]["hours"])

        create_vpn_key(
            tg_id=tg_id, payment_id=None,
            client_id=data["username"], client_name=data["username"],
            client_ip=None, client_public_key=None,
            vless_link=data["config"], expires_at=data["expires_at"],
            vpn_type="softether",
            vpn_file=data["vpn_file"],
        )

        # Отправляем .vpn файл
        vpn_file = _make_softether_vpn_file(data["username"], data["password"])
        caption = (
            f"🖥 <b>Тестовый SoftEther VPN конфиг</b>\n\n"
            f"⏱ Действителен: {TARIFFS['test_24h']['period']}\n\n"
            f"<b>📱 Инструкция:</b>\n"
            f"1. Установите <b>SoftEther VPN Client</b>\n"
            f"2. Импортируйте этот файл в клиент\n"
            f"3. Подключитесь\n\n"
            f"💬 Поддержка: кнопка «Написать нам» в меню"
        )
        await query.message.reply_document(
            document=vpn_file,
            caption=caption,
            parse_mode="HTML",
        )

        # Также отправляем текстом на случай ручного ввода
        await query.message.reply_text(
            _softether_credentials_text(data["username"], data["password"]),
            parse_mode="HTML",
        )

        set_softether_test_activated(tg_id)

        await query.message.reply_text(
            "✅ Конфиг создан! Выберите действие:",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📑 Инструкция и ссылки", callback_data="instructions")],
                [InlineKeyboardButton("◀️ В главное меню", callback_data="back_to_menu")],
            ]),
        )

    except Exception as e:
        logger.error(f"SoftEther config error: {e}")
        await query.message.reply_text(
            "❌ Ошибка создания конфига\n\nПопробуйте позже.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("◀️ В меню", callback_data="back_to_menu")]
            ]),
        )
