from fastapi import FastAPI, Request, HTTPException, Response
from fastapi.responses import HTMLResponse, PlainTextResponse
import json
import sys
import httpx
import logging
import time
from ipaddress import ip_address, ip_network
from datetime import datetime, timezone, timedelta
from io import BytesIO

import qrcode

from config import (
    XUI_HOST, XUI_USERNAME, XUI_PASSWORD,
    VLESS_DOMAIN, VLESS_PORT, VLESS_PATH,
    TELEGRAM_BOT_TOKEN, VLESS_SID, VLESS_PBK, VLESS_SNI,
    AMNEZIA_WG_API_URL, AMNEZIA_WG_API_PASSWORD,
)
from api.subscriptions import activate_subscription
from api.db import (
    update_payment_status,
    is_payment_processed,
    claim_payment_for_processing,
    get_payment_status,
    get_payment_by_id,
    get_or_create_user,
    create_vpn_key,
    get_subscription_until,
    get_user_email,
    deactivate_key_by_payment,
    get_user_by_web_token,
    sync_expiry,
)
from api.wireguard import AmneziaWGClient
from bot_xui.tariffs import TARIFFS
from bot_xui.utils import XUIClient

logger = logging.getLogger(__name__)

app = FastAPI()

from fastapi.middleware.cors import CORSMiddleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://alekscko.beget.tech", "http://alekscko.beget.tech"],
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

from api.web_portal import web_router
from api.web_api import web_api_router
from api.web_auth import auth_router
app.include_router(web_router)
app.include_router(web_api_router)
app.include_router(auth_router)

TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"

# ── Email open tracking pixel ──
import base64 as _b64
_PIXEL = _b64.b64decode("R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7")

@app.get("/t/{track_id}.gif")
async def email_open_track(track_id: str):
    from api.db import execute_query as _eq
    _eq("UPDATE email_opens SET opened_at = NOW() WHERE track_id = %s AND opened_at IS NULL", (track_id,))
    return Response(content=_PIXEL, media_type="image/gif", headers={"Cache-Control": "no-store"})

# ===== Белые IP ЮKassa =====
YOO_IPS = [
    ip_network("185.71.76.0/27"),
    ip_network("185.71.77.0/27"),
    ip_network("77.75.153.0/25"),
    ip_network("77.75.154.128/25"),
]

logger.info("WEBHOOK APP STARTED")


def verify_yookassa_ip(request: Request):
    """Проверка IP адреса YooKassa. IP check is ALWAYS enforced."""
    if not request.client:
        raise HTTPException(status_code=403, detail="No client IP")

    try:
        ip = ip_address(request.client.host)
    except ValueError:
        logger.warning(f"Invalid client IP: {request.client.host}")
        raise HTTPException(status_code=403, detail="Invalid IP")

    if not any(ip in net for net in YOO_IPS):
        logger.warning(f"Forbidden IP attempt: {request.client.host}")
        raise HTTPException(status_code=403, detail="Forbidden IP")


async def amnezia_login(client: httpx.AsyncClient):
    r = await client.post(
        f"{AMNEZIA_WG_API_URL}/api/session",
        json={"password": AMNEZIA_WG_API_PASSWORD},
        timeout=10
    )
    r.raise_for_status()

async def amnezia_create_client(client: httpx.AsyncClient, name: str):
    r = await client.post(
        f"{AMNEZIA_WG_API_URL}/api/wireguard/client",
        json={"name": name},
        timeout=10
    )
    r.raise_for_status()

async def amnezia_get_client_id(client: httpx.AsyncClient, name: str) -> str:
    r = await client.get(f"{AMNEZIA_WG_API_URL}/api/wireguard/client", timeout=10)
    r.raise_for_status()

    for c in r.json():
        if c.get("name") == name:
            return c["id"]

    raise RuntimeError("Client not found after creation")

async def amnezia_get_config(client: httpx.AsyncClient, client_id: str) -> str:
    r = await client.get(
        f"{AMNEZIA_WG_API_URL}/api/wireguard/client/{client_id}/configuration",
        timeout=10
    )
    r.raise_for_status()
    return r.text

def deactivate_xui_client(client_name: str) -> bool:
    """Деактивирует клиента в 3x-ui по email (client_name)."""
    try:
        xui = XUIClient(XUI_HOST, XUI_USERNAME, XUI_PASSWORD)
        info = xui.get_client_by_email(client_name)
        if not info:
            logger.warning(f"XUI client not found: {client_name}")
            return False
        return xui.deactivate_client(info['inbound_id'], info['client'])
    except Exception as e:
        logger.error(f"Error deactivating XUI client {client_name}: {e}")
        return False


async def process_refund(payment_id: str) -> bool:
    """Деактивирует VPN конфиг при возврате платежа"""
    try:
        payment_data = get_payment_by_id(payment_id)
        if not payment_data:
            logger.error(f"Payment not found for refund: {payment_id}")
            return False

        tg_id = payment_data.get("tg_id")
        client_name = get_user_email(tg_id)

        if not client_name:
            logger.error(f"No client_name for refund: {payment_id}")
            return False

        # Деактивируем в XUI
        xui_success = deactivate_xui_client(client_name)
        if not xui_success:
            logger.error(f"Failed to deactivate XUI client: {client_name}")
            return False

        # Деактивируем в БД
        deactivate_key_by_payment(payment_id)

        logger.info(f"Refund processed: {payment_id}, client: {client_name}")
        return True

    except Exception as e:
        logger.error(f"Error processing refund {payment_id}: {e}")
        return False

async def process_successful_payment(payment_id: str, payment_data: dict, vpn_type: str) -> bool:
    """
    ⭐ ОБРАБОТКА УСПЕШНОГО ПЛАТЕЖА ⭐
    
    Выполняет все необходимые действия при успешной оплате:
    1. Активирует подписку в БД
    2. Создает VPN клиента через AmneziaWG API
    3. Получает конфигурацию
    4. Сохраняет данные в БД
    5. Отправляет конфиг пользователю в Telegram
    
    Args:
        payment_id: ID платежа в YooKassa
        payment_data: Данные платежа из БД
        
    Returns:
        bool: True если обработка прошла успешно, False при ошибке
    """
    try:
        logger.info(f"💰 Processing successful payment: {payment_id}")

        tg_id: int = payment_data["tg_id"]
        tariff_key: str = payment_data["tariff"]

        # ===== 1. Получение / создание пользователя =====
        web_user_id = payment_data.get("_web_user_id")
        if tg_id and tg_id != 0:
            user_id = get_or_create_user(tg_id)
        elif web_user_id:
            user_id = web_user_id
        else:
            user_id = None

        # ===== 4. Формирование имени VPN клиента =====
        client_name = f"{tariff_key}_{tg_id or user_id}_{payment_id[:8]}"

        client_id = None
        client_ip = None
        client_public_key = None
        bio = None
        sub_url = None
        se_data = {}

        # ===== 5. Создание конфига в зависимости от типа VPN =====
        if vpn_type == "vless":
            # ========== VLESS (3x-ui) ==========
            logger.info("🟢 Creating VLESS config via 3x-ui")
            
            import uuid
            from bot_xui.utils import XUIClient, generate_vless_link
            
            # Инициализируем 3x-ui клиент
            xui = XUIClient(
                XUI_HOST,
                XUI_USERNAME,
                XUI_PASSWORD
            )
            
            # Генерируем UUID для клиента
            client_id = str(uuid.uuid4())
            
            # Получаем inbound
            inbounds = xui.get_inbounds()
            if not inbounds:
                raise RuntimeError("3x-ui inbound not found")
            
            if len(inbounds) < 3:
                raise RuntimeError(f"Expected at least 3 inbounds, got {len(inbounds)}")
            inbound_id = inbounds[2]['id']
            
            # Время истечения — 23:59:59 Tokyo последнего дня
            duration_days = TARIFFS[tariff_key].get('days', 30)
            tz_tokyo = timezone(timedelta(hours=9))
            raw_end = datetime.now(timezone.utc) + timedelta(days=duration_days)
            end_tokyo = raw_end.astimezone(tz_tokyo).replace(hour=23, minute=59, second=59, microsecond=0)
            expiry_time = int(end_tokyo.timestamp() * 1000)
            
            # ===== Создаем/продлеваем клиента в 3x-ui =====
            if tg_id and tg_id != 0:
                existing = xui.get_client_by_tg_id(tg_id)
                if existing:
                    client_id = existing['client']['id']
                    logger.info(f"Existing client found, reusing uuid: {client_id}")

                success = xui.add_or_extend_client(
                    inbound_id=inbound_id,
                    email=client_name,
                    tg_id=tg_id,
                    uuid=client_id,
                    expiry_time=expiry_time,
                    total_gb=0,
                    limit_ip=TARIFFS[tariff_key].get('device_limit', 10)
                )
            else:
                # Web user without tg_id — always create new client
                logger.info(f"Web user (no tg_id), creating new VLESS client")
                success = xui.add_client(
                    inbound_id=inbound_id,
                    email=client_name,
                    tg_id=0,
                    uuid=client_id,
                    expiry_time=expiry_time,
                    total_gb=0,
                    limit_ip=TARIFFS[tariff_key].get('device_limit', 10)
                )
            
            if not success:
                raise RuntimeError("Failed to create VLESS client")
            
            # Генерируем VLESS ссылку
            client_config = generate_vless_link(
                client_id=client_id,
                domain=VLESS_DOMAIN,
                port=VLESS_PORT,
                path=VLESS_PATH,
                client_name=client_name,
                pbk=VLESS_PBK,       # из .env
                sid=VLESS_SID,       # из .env
                sni=VLESS_SNI,       # из .env
                fp="chrome",
                spx="/"
            )
            
            # Получаем subscription URL
            if tg_id and tg_id != 0:
                sub_url = xui.get_client_subscription_url(tg_id)
            else:
                sub_url = xui.get_subscription_url_by_uuid(client_id)

            # Создаем QR код
            qr = qrcode.QRCode(version=1, box_size=10, border=5)
            qr.add_data(sub_url)
            qr.make(fit=True)
            img = qr.make_image(fill_color="black", back_color="white")
            
            bio = BytesIO()
            img.save(bio, 'PNG')
            bio.seek(0)            
            
        elif vpn_type == "softether":
            # ========== SoftEther ==========
            logger.info("🖥 Creating SoftEther config")
            from bot_xui.vpn_factory import create_softether_config
            se_data = create_softether_config(tg_id, days=TARIFFS[tariff_key].get('days', 30))
            client_id = se_data["username"]
            client_name = se_data["username"]
            client_config = se_data["config"]
            sub_url = None
            bio = None

        else:
            # ========== AmneziaWG ==========
            logger.info("🔵 Creating AmneziaWG config")
            
            async with httpx.AsyncClient(timeout=15) as client:
                # 5.1 Login
                r = await client.post(
                    f"{AMNEZIA_WG_API_URL}/api/session",
                    json={"password": AMNEZIA_WG_API_PASSWORD},
                )
                r.raise_for_status()

                # 5.2 Create client
                r = await client.post(
                    f"{AMNEZIA_WG_API_URL}/api/wireguard/client",
                    json={"name": client_name},
                )
                r.raise_for_status()

                # 5.3 Получение client_id
                r = await client.get(f"{AMNEZIA_WG_API_URL}/api/wireguard/client")
                r.raise_for_status()

                for c in r.json():
                    if c.get("name") == client_name:
                        client_id = c.get("id")
                        client_ip = c.get("address")
                        client_public_key = c.get("publicKey")
                        break

                if not client_id:
                    raise RuntimeError("Client ID not found after creation")

                logger.info(f"✅ VPN client created: client_id={client_id}, ip={client_ip}")

                # 5.4 Получение конфигурации
                r = await client.get(
                    f"{AMNEZIA_WG_API_URL}/api/wireguard/client/{client_id}/configuration"
                )
                r.raise_for_status()

                client_config = r.text
                if not client_config:
                    raise RuntimeError("Empty client configuration")


        # ===== 6. Активация подписки (после успешного создания VPN) =====
        activate_subscription(payment_id, user_id=web_user_id)
        subscription_until = get_subscription_until(tg_id) if tg_id else None
        if not subscription_until and web_user_id:
            from api.db import get_user_by_id
            _u = get_user_by_id(web_user_id)
            subscription_until = _u.get('subscription_until') if _u else None
        logger.info("✅ Subscription activated")

        # ===== 6.5. Sync expiry across all stores =====
        # For VLESS: 3x-ui expiry was calculated from now(), but activate_subscription
        # calculates from paid_at — they can diverge. Use subscription_until as the
        # single source of truth and re-sync 3x-ui.
        if vpn_type == "vless" and subscription_until and tg_id and tg_id != 0:
            sub_until_ms = int(subscription_until.replace(tzinfo=timezone.utc).timestamp() * 1000) if subscription_until.tzinfo is None else int(subscription_until.timestamp() * 1000)
            existing_xui = xui.get_client_by_tg_id(tg_id)
            if existing_xui:
                updated_client = {**existing_xui['client'], 'expiryTime': sub_until_ms}
                if not updated_client.get('flow'):
                    updated_client['flow'] = 'xtls-rprx-vision'
                import json as _json
                payload = {
                    "id": existing_xui['inbound_id'],
                    "settings": _json.dumps({"clients": [updated_client]})
                }
                xui._request(
                    "POST",
                    f"{xui.host}/panel/api/inbounds/updateClient/{existing_xui['client']['id']}",
                    json=payload,
                    headers={"Content-Type": "application/json"}
                )
                logger.info(f"Re-synced 3x-ui expiry to {subscription_until}")

        # ===== 7. Сохранение в БД =====
        create_vpn_key(
            tg_id=tg_id,
            payment_id=payment_id,
            client_id=client_id,
            client_name=client_name,
            client_ip=client_ip,
            client_public_key=client_public_key,
            vless_link=client_config,
            expires_at=subscription_until,
            vpn_type=vpn_type,
            subscription_link=sub_url,
            vpn_file=se_data.get("vpn_file") if vpn_type == "softether" else None,
            user_id=web_user_id,
        )

        # Sync expiry to vpn_keys for existing keys too
        if subscription_until and tg_id:
            sync_expiry(tg_id, subscription_until if subscription_until.tzinfo else subscription_until.replace(tzinfo=timezone.utc))

        logger.info("💾 VPN config saved to DB")

        # ===== 7. Отправка в Telegram (пропускаем для веб-заказов без tg_id) =====
        tariff_info = TARIFFS.get(tariff_key, {})
        tariff_name = tariff_info.get("name", tariff_key)

        if not tg_id or tg_id == 0:
            logger.info("📤 Web order — Telegram notification skipped (no tg_id)")
            # Send payment confirmation email for web-only users
            if web_user_id:
                from api.db import get_user_by_id
                from api.notifications import send_payment_success_email
                _web_u = get_user_by_id(web_user_id)
                if _web_u and _web_u.get('email'):
                    portal_url = f"https://344988.snk.wtf/my/{_web_u.get('web_token', '')}"
                    send_payment_success_email(
                        to=_web_u['email'],
                        tariff_name=tariff_name,
                        period=tariff_info.get('period', ''),
                        portal_url=portal_url,
                    )
            return True

        try:
            if vpn_type == "vless":
                # Отправляем VLESS как текст с QR кодом  
                
                await send_telegram_photo_from_bytes(
                    tg_id=tg_id,
                    image_bytes=bio,
                    caption=f"✅ <b>Оплата прошла успешно!</b>\n\n"
                            f"⏱ Тариф: <b>{TARIFFS[tariff_key].get('name', tariff_key)}</b>\n"
                            f"📅 Период: {TARIFFS[tariff_key].get('period', '30 дней')}\n"
                            f"👥 Устройств: {TARIFFS[tariff_key].get('device_limit', 10)}\n\n"
                            f"<b>📱 Инструкция:</b>\n"
                            f"1. Установите приложение из раздела «Инструкция»\n"
                            f"2. Отсканируйте QR или скопируйте ссылку ниже\n"
                            f"3. Подключитесь\n\n"
                            f"💬 Поддержка: кнопка «Написать нам» в меню",
                )

                # Отправка subscription ссылки
                message = (
                    f"🔗 Ваша ссылка на подписку:\n"
                    f"👇 <i>Нажмите чтобы скопировать:</i>\n"
                    f"┌────────────────────\n"
                    f"  <code>{sub_url}</code>\n"
                    f"└────────────────────\n\n"
                    f"📱 Скопируйте ссылку и вставьте в приложение"
                )
                await send_telegram_notification(tg_id, message)

                routing_message = (
                    "⚙️ <b>Важно для пользователей Happ:</b>\n\n"
                    "Чтобы YouTube, Instagram и другие заблокированные сайты "
                    "открывались через VPN, настройте маршрутизацию:\n\n"
                    "1. Откройте ссылку ниже на телефоне\n"
                    "2. В Happ: <b>Настройки</b> → <b>Настройки туннеля</b> → "
                    "<b>Маршрутизация</b> → выберите <b>Tiin Split Rules</b>\n"
                    "3. Переподключите VPN"
                )
                routing_buttons = [
                    [{"text": "📲 Настроить маршрутизацию", "url": "https://344988.snk.wtf/happ-routing"}],
                    [{"text": "📑 Инструкция и ссылки", "callback_data": "instructions"}],
                    [{"text": "◀️ В меню", "callback_data": "back_to_menu"}]
                ]
                await send_telegram_notification(tg_id, routing_message, routing_buttons)
                
                
            elif vpn_type == "softether":
                # Отправляем SoftEther как текст с данными подключения
                from bot_xui.vpn_factory import _softether_credentials_text
                message = (
                    f"✅ <b>Оплата прошла успешно!</b>\n\n"
                    f"⏱ Тариф: <b>{tariff_name}</b>\n"
                    f"📅 Период: {TARIFFS[tariff_key].get('period', '30 дней')}\n\n"
                    + _softether_credentials_text(se_data["username"], se_data["password"])
                    + f"\n<b>📱 Инструкция:</b>\n"
                    f"1. Установите <b>SoftEther VPN Client</b>\n"
                    f"2. Создайте новое подключение\n"
                    f"3. Введите данные выше\n"
                    f"4. Подключитесь\n\n"
                    f"💬 Поддержка: кнопка «Написать нам» в меню"
                )
                await send_telegram_notification(tg_id, message)

                buttons = [
                    [{"text": "◀️ В меню", "callback_data": "back_to_menu"}]
                ]
                await send_telegram_notification(tg_id, "Выберите действие:", buttons)

            else:
                # Отправляем AmneziaWG как файл
                filename = f"amneziawg_{tg_id}_{payment_id[:8]}.conf"

                caption = (
                    f"✅ Ваш AmneziaWG конфиг готов!\n\n"
                    f"🔑 Тариф: {tariff_name}\n"
                    f"🌐 IP: {client_ip}\n"
                    f"📅 Активен до: {subscription_until:%d.%m.%Y}\n\n"
                    f"📱 Инструкция:\n"
                    f"1. Установите AmneziaVPN\n"
                    f"2. Импортируйте файл конфигурации\n"
                    f"3. Подключитесь\n\n"
                    f"💬 Поддержка: кнопка «Написать нам» в меню"
                )

                await send_telegram_document(tg_id, client_config.encode(), filename, caption)

            logger.info("📤 Config sent to Telegram")

        except Exception:
            logger.exception("⚠️ Failed to send config to Telegram")

        return True

    except Exception:
        logger.exception(f"❌ Critical error processing payment {payment_id}")
        return False

async def send_telegram_notification(tg_id: int, message: str, buttons: list = None):
    """
    Отправка уведомления в Telegram через HTTP API
    
    Args:
        tg_id: Telegram ID пользователя
        message: Текст сообщения
        buttons: Список кнопок (опционально)
    """
    if not tg_id:
        return

    data = {
        "chat_id": tg_id,
        "text": message,
        "parse_mode": "HTML"
    }
    
    # Добавляем кнопки если они есть
    if buttons:
        keyboard = {
            "inline_keyboard": buttons
        }
        data["reply_markup"] = json.dumps(keyboard)

    async with httpx.AsyncClient(timeout=5) as client:
        try:
            response = await client.post(TELEGRAM_API, data=data)
            
            if response.status_code == 200:
                logger.info(f"📨 Notification sent to user: {tg_id}")
            else:
                logger.warning(f"⚠️ Telegram API returned {response.status_code}")
                
        except Exception as e:
            logger.error(f"❌ Failed to send Telegram notification: {e}")

async def send_telegram_document(tg_id: int, file_bytes: bytes, filename: str, caption: str = ""):
    """Отправка документа в Telegram через HTTP API"""
    if not tg_id:
        return False

    telegram_doc_api = TELEGRAM_API.replace('sendMessage', 'sendDocument')

    async with httpx.AsyncClient(timeout=10) as client:
        try:
            files = {'document': (filename, file_bytes, 'application/octet-stream')}
            data = {'chat_id': tg_id}
            if caption:
                data['caption'] = caption
                data['parse_mode'] = 'HTML'

            response = await client.post(telegram_doc_api, data=data, files=files)

            if response.status_code == 200:
                logger.info(f"Document sent to user: {tg_id}")
                return True
            else:
                logger.warning(f"Telegram API returned {response.status_code}: {response.text}")
                return False
        except Exception as e:
            logger.error(f"Failed to send Telegram document: {e}")
            return False


async def send_telegram_photo_from_bytes(tg_id: int, image_bytes: BytesIO, caption: str = ""):
    """
    Отправка фото в Telegram через HTTP API из BytesIO    
    """
    if not tg_id:
        return
    
    # Меняем endpoint на sendPhoto
    telegram_photo_api = TELEGRAM_API.replace('sendMessage', 'sendPhoto')
    
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            # Сбрасываем позицию в начало
            image_bytes.seek(0)
            
            # Правильный формат для httpx
            files = {
                'photo': ('qr.png', image_bytes, 'image/png')
            }
            data = {
                'chat_id': tg_id
            }
            
            if caption:
                data['caption'] = caption
                data['parse_mode'] = 'HTML'
            
            response = await client.post(
                telegram_photo_api,
                data=data,
                files=files
            )
            
            if response.status_code == 200:
                logger.info(f"📸 Photo sent to user: {tg_id}")
                return True
            else:
                logger.warning(f"⚠️ Telegram API returned {response.status_code}: {response.text}")
                return False
                    
        except Exception as e:
            logger.error(f"❌ Failed to send Telegram photo: {e}")
            return False

@app.post("/webhook")
async def yookassa_webhook(request: Request):
    """
    Обработчик webhook от YooKassa
    Вызывается при изменении статуса платежа
    """
    logger.info("YooKassa webhook received")

    # ===== 1. Парсинг данных =====
    try:
        body = await request.body()
        payload = json.loads(body)
    except json.JSONDecodeError:
        logger.error("Invalid JSON in webhook body")
        return Response(status_code=400)

    # ===== 2. Извлечение данных =====
    event = payload.get("event")
    obj = payload.get("object", {})

    payment_id = obj.get("id")
    status_raw = obj.get("status")
    metadata = obj.get("metadata", {})

    tg_id = metadata.get("tg_id")
    tariff = metadata.get("tariff", "default")
    vpn_type = metadata.get("vpn_type")
    is_web_order = metadata.get("source") == "web"
    web_email = metadata.get("email", "")
    web_token = metadata.get("web_token", "")

    # ===== 3. Проверка IP (ALWAYS enforced) =====
    verify_yookassa_ip(request)
    
    if not payment_id:
        logger.warning("⚠️ No payment_id in webhook")
        return Response(status_code=200)
    
    logger.info(f"📋 Payment ID: {payment_id}, Status: {status_raw}, Event: {event}")


    # ===== 3.5 ОБРАБОТКА ВОЗВРАТА =====
    if event == "payment.refunded":
        refund_amount = obj.get("refunded_amount", {})
        amount_value = refund_amount.get("value", "0")
        amount_currency = refund_amount.get("currency", "RUB")
        
        logger.info(f"💸 Refund received: {payment_id}, amount: {amount_value} {amount_currency}")
        
        # Обновляем статус платежа в БД
        update_payment_status(payment_id, "refunded")
        
        # Деактивируем VPN конфиг пользователя
        success = await process_refund(payment_id)
        
        # Уведомляем пользователя
        if tg_id:
            await send_telegram_notification(
                tg_id,
                f"💸 Возврат платежа выполнен\n\n"
                f"💳 ID платежа: {payment_id}\n"
                f"💰 Сумма возврата: {amount_value} {amount_currency}\n\n"
                f"Ваш VPN конфиг был деактивирован.\n"
                f"Если это ошибка — нажмите «Написать нам» в меню бота"
            )
        
        return Response(status_code=200)
    
    # ===== 4. Проверка существования платежа =====
    current_status = get_payment_status(payment_id)
    if not current_status:
        logger.warning(f"⚠️ Unknown payment_id: {payment_id}")
        return {"status": "ignored"}
    
    # ===== 5. Проверка на дубликат =====
    if current_status in ("paid", "canceled"):
        logger.info(f"🔁 Duplicate webhook ignored: {payment_id} ({current_status})")
        return {"status": "duplicate"}
    
    # ===== 6. Нормализация статуса =====
    if status_raw == "succeeded":
        new_status = "paid"
    elif status_raw in ("canceled", "failed"):
        new_status = "canceled"
    else:
        new_status = "pending"
    
    # ===== 7. Проверка изменения статуса =====
    if new_status == current_status:
        logger.info(f"ℹ️ Status unchanged: {payment_id} ({new_status})")
        return {"status": "no_change"}
    
    # ===== 8. Получение данных платежа =====
    payment_data = get_payment_by_id(payment_id)
    if not payment_data:
        logger.error(f"❌ Payment data not found: {payment_id}")
        return Response(status_code=404)
    
    # ===== 9. ⭐ ОБРАБОТКА УСПЕШНОГО ПЛАТЕЖА ⭐ =====
    if current_status == "pending" and new_status == "paid":
        # Atomically claim payment — only one concurrent webhook wins
        if not claim_payment_for_processing(payment_id):
            logger.info(f"🔁 Payment already claimed by another request: {payment_id}")
            return Response(status_code=200)

        # Verify paid amount matches tariff price
        paid_amount = float(payment_data.get("amount") or 0)
        tariff_info = TARIFFS.get(tariff, {})
        expected_price = tariff_info.get("price")
        if expected_price is not None and paid_amount < expected_price:
            logger.error(f"❌ Amount mismatch: paid={paid_amount}, expected={expected_price}, payment={payment_id}")
            update_payment_status(payment_id, "pending")  # revert claim
            return Response(status_code=400)

        # Для веб-заказов: привязать tg_id и user_id из users если есть
        if is_web_order and web_token:
            web_user = get_user_by_web_token(web_token) if web_token else None
            if web_user:
                tg_id = int(web_user.get('tg_id') or 0)
                payment_data = {**payment_data, 'tg_id': tg_id, '_web_user_id': web_user['id']}

        success = await process_successful_payment(payment_id, payment_data, vpn_type)

        if not success:
            logger.error(f"❌ Failed to process payment {payment_id}")
            update_payment_status(payment_id, "pending")  # revert so YooKassa retries
            if tg_id:
                await send_telegram_notification(
                    tg_id,
                    f"⚠️ Возникла ошибка при создании VPN конфига.\n"
                    f"Платёж ID: {payment_id}\n\n"
                    f"Мы уже знаем о проблеме, конфиг будет создан автоматически."
                )
            return Response(status_code=500)  # YooKassa повторит запрос

        logger.info(f"💾 Payment claimed and processed: {payment_id} -> paid")
    elif new_status == "canceled":
        # ===== 10. Обновление статуса отмены =====
        update_payment_status(payment_id, new_status)
        logger.info(f"💾 Payment status updated: {payment_id} -> {new_status}")
    else:
        logger.info(f"ℹ️ Ignoring status transition: {payment_id} {current_status} -> {new_status}")
    
    # ===== 12. Уведомление пользователя о статусе =====
    if tg_id:
        tariff_info = TARIFFS.get(tariff, {})
        tariff_name = tariff_info.get("name", tariff)
        tariff_desc = tariff_info.get("yookassa_description", "")
        
        if new_status == "paid":
            # Основное уведомление уже отправлено в process_successful_payment
            # Дополнительное уведомление не нужно
            pass
        elif new_status == "canceled":
            message = (
                f"❌ Платёж не прошёл\n\n"
                f"💳 ID платежа: {payment_id}\n"
                f"📦 Тариф: {tariff_name}\n\n"
                f"Попробуйте ещё раз или обратитесь в поддержку."
            )
            await send_telegram_notification(tg_id, message)
        else:
            message = f"⏳ Платёж {payment_id} в обработке ({new_status})"
            await send_telegram_notification(tg_id, message)
    
    logger.info(
        f"✅ Webhook processed | Payment: {payment_id} | "
        f"TG: {tg_id} | Status: {new_status}"
    )
    
    # ===== 13. ВАЖНО: всегда возвращаем 200 =====
    return Response(status_code=200)


@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "service": "vpn-webhook",
        "timestamp": datetime.now().isoformat(),
        "wg_api": AMNEZIA_WG_API_URL
    }


REACT_DIST = "/home/alvik/vpn-service/website_dist"


@app.get("/mailru-domainExTNua2shOgGVi17.html", response_class=PlainTextResponse)
async def mailru_verify():
    return "mailru-domain: ExTNua2shOgGVi17"


@app.get("/", response_class=HTMLResponse)
async def root():
    """Лендинг: React SPA (if built) or fallback to static HTML"""
    from pathlib import Path
    react_index = Path(REACT_DIST) / "index.html"
    if react_index.exists():
        return HTMLResponse(react_index.read_text())
    html = Path("/home/alvik/vpn-service/website/index.html").read_text()
    return HTMLResponse(html)


# Serve React static assets (JS, CSS, images)
from pathlib import Path as _Path
if _Path(REACT_DIST).exists():
    from fastapi.staticfiles import StaticFiles
    app.mount("/assets", StaticFiles(directory=f"{REACT_DIST}/assets"), name="react-assets")


# SPA catch-all: serve index.html for client-side routes (/login, /dashboard)
@app.get("/login", response_class=HTMLResponse)
@app.get("/dashboard", response_class=HTMLResponse)
async def spa_fallback():
    """Serve React SPA for client-side routes"""
    from pathlib import Path
    react_index = Path(REACT_DIST) / "index.html"
    if react_index.exists():
        return HTMLResponse(react_index.read_text())
    return HTMLResponse("<script>location.href='/'</script>")


## TEST ENDPOINT REMOVED — was accessible without authentication.
## Use admin panel or direct DB queries for payment debugging.