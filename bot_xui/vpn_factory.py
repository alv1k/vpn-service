"""
Фабрика VPN-конфигов: создание AWG и VLESS, сохранение в БД.
"""
import logging
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
)
from bot_xui.utils import XUIClient, generate_vless_link
from bot_xui.tariffs import TARIFFS
from api.db import create_vpn_key, set_awg_test_activated, set_vless_test_activated

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
    expiry_ms = int((time.time() + 86400) * 1000)
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

    expires_at = datetime.now(timezone.utc) + timedelta(hours=TARIFFS["test_24h"]["hours"])

    return {"client_email": client_email, "client_uuid": client_uuid,
            "vless_link": vless_link, "expires_at": expires_at}


# ──────────────────────────────────────────────────────────────────────────────
# Handlers — вызываются из button_handler
# ──────────────────────────────────────────────────────────────────────────────

async def handle_test_awg(query, xui: XUIClient):
    """Создаёт тестовый AWG конфиг и отправляет пользователю."""
    tg_id = query.from_user.id
    await query.edit_message_text("⏳ Создаю тестовый AmneziaWG конфиг...")

    try:
        data = await create_awg_config(tg_id)
        expiry_at = datetime.now(timezone.utc) + timedelta(hours=TARIFFS["test_24h"]["hours"])

        create_vpn_key(
            tg_id=tg_id, payment_id=None,
            client_id=data["client_id"], client_name=data["client_name"],
            client_ip=data["client_ip"], client_public_key=None,
            config=data["config"], expires_at=expiry_at, vpn_type="awg",
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
                f"💬 Поддержка: @al_v1k"
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
            f"❌ Ошибка создания конфига\n\n{e}\n\nПопробуйте позже или выберите VLESS.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("◀️ В меню", callback_data="back_to_menu")]
            ]),
        )


async def handle_test_vless(query, xui: XUIClient):
    """Создаёт тестовый VLESS конфиг и отправляет пользователю."""
    tg_id = query.from_user.id
    await query.edit_message_text("⏳ Создаю тестовый VLESS конфиг...")

    try:
        data = await create_vless_config(tg_id, xui)

        create_vpn_key(
            tg_id=tg_id, payment_id=None,
            client_id=data["client_uuid"], client_name=data["client_email"],
            client_ip=None, client_public_key=None,
            config=data["vless_link"], expires_at=data["expires_at"], vpn_type="vless",
        )

        bio = make_qr_bytes(data["vless_link"])

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
                f"💬 Поддержка: @al_v1k"
            ),
            parse_mode="HTML",
        )

        await query.message.reply_text(
            f"🔑 Ключ-конфиг\n\n"
            f"<pre>{data['vless_link']}</pre>\n\n"
            f"Скопируйте эту ссылку и вставьте в приложение",
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
            f"❌ Ошибка создания конфига\n\n{e}\n\nПопробуйте позже или выберите AmneziaWG.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("◀️ В меню", callback_data="back_to_menu")]
            ]),
        )
