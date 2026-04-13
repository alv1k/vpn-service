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
    SERVER_LOCATION, VLESS_INBOUND_ID,
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

# ─────────────────────────────────────────────
#  IP rate limiter (in-memory, per-endpoint)
# ─────────────────────────────────────────────
from collections import defaultdict
import threading

_rate_lock = threading.Lock()
_rate_buckets: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))

RATE_LIMITS = {
    "/api/auth/send-code": (5, 300),      # 5 requests per 5 min
    "/api/auth/verify": (10, 300),        # 10 per 5 min — prevent brute-force on 6-digit codes
    "/api/web/order/send-code": (5, 300),
    "/api/web/activate-test": (3, 3600),   # 3 per hour
    "/api/web/support/send-code": (5, 300),
    "/api/web/order": (10, 300),           # 10 per 5 min
    "/api/web/event": (30, 60),           # 30 per minute — prevent DB flooding
}


_rate_last_purge = 0.0

def _check_rate_limit(ip: str, path: str) -> bool:
    """Returns True if request is allowed, False if rate-limited."""
    limit_cfg = None
    for prefix, cfg in RATE_LIMITS.items():
        if path == prefix or path.startswith(prefix):
            limit_cfg = cfg
            break
    if not limit_cfg:
        return True

    max_req, window = limit_cfg
    now = time.time()
    with _rate_lock:
        # Periodically purge stale IPs (every 10 minutes)
        global _rate_last_purge
        if now - _rate_last_purge > 600:
            _rate_last_purge = now
            max_window = max(w for _, w in RATE_LIMITS.values())
            for p in list(_rate_buckets.keys()):
                for k in list(_rate_buckets[p].keys()):
                    _rate_buckets[p][k] = [t for t in _rate_buckets[p][k] if now - t < max_window]
                    if not _rate_buckets[p][k]:
                        del _rate_buckets[p][k]
                if not _rate_buckets[p]:
                    del _rate_buckets[p]

        bucket = _rate_buckets[path][ip]
        # Purge old entries
        _rate_buckets[path][ip] = bucket = [t for t in bucket if now - t < window]
        if len(bucket) >= max_req:
            return False
        bucket.append(now)
    return True


app = FastAPI()


@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    ip = request.headers.get("x-real-ip") or (request.client.host if request.client else "unknown")
    if ip == "testclient":
        return await call_next(request)
    if not _check_rate_limit(ip, request.url.path):
        return Response(
            content='{"detail":"Слишком много запросов. Попробуйте позже."}',
            status_code=429,
            media_type="application/json",
        )
    return await call_next(request)

from fastapi.middleware.cors import CORSMiddleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://alekscko.beget.tech"],
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "Authorization"],
)

from api.web_portal import web_router
from api.web_api import web_api_router
from api.web_auth import auth_router
from api.sub_proxy import sub_router
app.include_router(web_router)
app.include_router(web_api_router)
app.include_router(auth_router)
app.include_router(sub_router)

TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"

# ── Proxy file download ──
@app.get("/proxy.html")
async def proxy_file_download():
    from bot_xui.helpers import make_proxy_file
    buf = make_proxy_file()
    return Response(
        content=buf.getvalue(),
        media_type="text/html",
        headers={"Content-Disposition": f"attachment; filename={buf.name}"},
    )

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
        client_name = get_user_email(tg_id, payment_id=payment_id)

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

        tg_id: int = payment_data.get("tg_id") or 0
        tariff_key: str = payment_data.get("tariff", "default")

        # ===== 1. Получение / создание пользователя =====
        web_user_id = payment_data.get("_web_user_id")
        if tg_id and tg_id != 0:
            user_id = get_or_create_user(tg_id)
        elif web_user_id:
            user_id = web_user_id
        else:
            user_id = None

        # ===== 4. Формирование имени VPN клиента =====
        if tg_id and tg_id != 0:
            client_name = f"tiin_{tg_id}"
        elif web_user_id:
            client_name = f"tiin_web_{web_user_id}"
        else:
            client_name = f"tiin_{payment_id[:8]}"

        client_id = None
        client_ip = None
        client_public_key = None
        bio = None
        sub_url = None
        user_sub_url = None
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
            
            inbound_id = xui.get_vless_reality_inbound_id(fallback_id=int(VLESS_INBOUND_ID))
            
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
                # Web user without tg_id — check for existing client by email
                existing_web = xui.get_client_by_email(client_name)
                if existing_web:
                    client_id = existing_web['client']['id']
                    logger.info(f"Existing web client found, reusing uuid: {client_id}")
                    # Extend expiry
                    import time as _time
                    now_ms = int(_time.time() * 1000)
                    duration_ms = expiry_time - now_ms
                    success = xui.extend_client_expiry(
                        existing_web['inbound_id'],
                        existing_web['client'],
                        duration_ms,
                    )
                else:
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
                pbk=VLESS_PBK,
                sid=VLESS_SID,
                sni=VLESS_SNI,
                fp="chrome",
                spx="/",
                remark=f"🇩🇪 {SERVER_LOCATION} | VLESS",
            )
            
            # Получаем subscription URL (XUI) — храним в БД как источник для прокси
            if tg_id and tg_id != 0:
                sub_url = xui.get_client_subscription_url(tg_id)
            else:
                sub_url = xui.get_subscription_url_by_uuid(client_id)

            # Пользователю показываем прокси-URL, который переписывает remark
            from api.db import get_web_token
            if tg_id and tg_id != 0:
                _wt = get_web_token(tg_id)
                user_sub_url = f"https://344988.snk.wtf/sub/{_wt}" if _wt else sub_url
            else:
                # для веб-заказов web_token добавим позже при отдаче email-писем
                user_sub_url = sub_url

            # Создаем QR код из прокси-URL
            qr = qrcode.QRCode(version=1, box_size=10, border=5)
            qr.add_data(user_sub_url)
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
            user_sub_url = None
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

                # 5.2 Delete existing client with same name (dedup on retry)
                r = await client.get(f"{AMNEZIA_WG_API_URL}/api/wireguard/client")
                r.raise_for_status()
                for c in r.json():
                    if c.get("name") == client_name:
                        old_id = c.get("id")
                        logger.info(f"Deleting old AWG client {old_id} ({client_name})")
                        await client.delete(
                            f"{AMNEZIA_WG_API_URL}/api/wireguard/client/{old_id}",
                        )
                        break

                # 5.3 Create client
                r = await client.post(
                    f"{AMNEZIA_WG_API_URL}/api/wireguard/client",
                    json={"name": client_name},
                )
                r.raise_for_status()

                # 5.4 Получение client_id
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

        # ===== 6.1. Mark test period as activated =====
        tariff_info = TARIFFS.get(tariff_key, {})
        if tariff_info.get("is_test"):
            from api.db import set_vless_test_activated, set_vless_test_activated_by_id
            if tg_id and tg_id != 0:
                set_vless_test_activated(tg_id)
                logger.info(f"Marked test_vless_activated for tg_id={tg_id}")
            elif web_user_id:
                set_vless_test_activated_by_id(web_user_id)
                logger.info(f"Marked test_vless_activated for user_id={web_user_id}")

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
        sub_utc = subscription_until if subscription_until.tzinfo else subscription_until.replace(tzinfo=timezone.utc)
        if subscription_until and tg_id and tg_id != 0:
            sync_expiry(tg_id, sub_utc)
        elif subscription_until and web_user_id:
            from api.db import sync_expiry_by_user_id
            sync_expiry_by_user_id(web_user_id, sub_utc)

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
            # Build portal URL for wizard link
            from api.db import get_web_token
            _wt = get_web_token(tg_id)
            portal_url = f"https://344988.snk.wtf/my/{_wt}" if _wt else None

            if vpn_type == "vless":
                # Отправляем VLESS как QR + краткая информация
                await send_telegram_photo_from_bytes(
                    tg_id=tg_id,
                    image_bytes=bio,
                    caption=f"✅ <b>Оплата прошла успешно!</b>\n\n"
                            f"⏱ <b>{TARIFFS[tariff_key].get('name', tariff_key)}</b> · "
                            f"{TARIFFS[tariff_key].get('period', '30 дней')} · "
                            f"{TARIFFS[tariff_key].get('device_limit', 10)} устр.",
                )

                # Ссылка на подписку + кнопки
                message = (
                    f"🔗 <b>Ваша ссылка:</b>\n"
                    f"<code>{user_sub_url or sub_url}</code>\n\n"
                    f"📱 Скопируйте и вставьте в приложение"
                )
                buttons = []
                if portal_url:
                    buttons.append([{"text": "🪄 Гид по подключению", "url": portal_url}])
                buttons.append([{"text": "📲 Happ: настроить маршрутизацию", "url": "https://344988.snk.wtf/happ-routing"}])
                buttons.append([{"text": "◀️ В меню", "callback_data": "back_to_menu"}])
                await send_telegram_notification(tg_id, message, buttons)
                
                
            elif vpn_type == "softether":
                # Отправляем SoftEther как текст с данными подключения
                from bot_xui.vpn_factory import _softether_credentials_text
                message = (
                    f"✅ <b>Оплата прошла успешно!</b>\n\n"
                    f"⏱ <b>{tariff_name}</b> · {TARIFFS[tariff_key].get('period', '30 дней')}\n\n"
                    + _softether_credentials_text(se_data["username"], se_data["password"])
                )
                se_buttons = []
                if portal_url:
                    se_buttons.append([{"text": "🪄 Гид по подключению", "url": portal_url}])
                se_buttons.append([{"text": "◀️ В меню", "callback_data": "back_to_menu"}])
                await send_telegram_notification(tg_id, message, se_buttons)

            else:
                # Отправляем AmneziaWG как файл
                filename = f"amneziawg_{tg_id}_{payment_id[:8]}.conf"

                caption = (
                    f"✅ <b>Оплата прошла успешно!</b>\n\n"
                    f"⏱ <b>{tariff_name}</b> · до {subscription_until:%d.%m.%Y}\n"
                    f"🌐 IP: {client_ip}\n\n"
                    f"📱 Импортируйте файл в AmneziaVPN"
                )
                if portal_url:
                    caption += f'\n\n<a href="{portal_url}">🪄 Гид по подключению</a>'

                await send_telegram_document(tg_id, client_config.encode(), filename, caption)

            logger.info("📤 Config sent to Telegram")

            # Send portal link via email as fallback (in case TG is blocked)
            try:
                from api.db import get_user_by_tg_id
                from api.notifications import send_payment_success_email
                _tg_user = get_user_by_tg_id(tg_id)
                if _tg_user and _tg_user.get('email') and portal_url:
                    send_payment_success_email(
                        to=_tg_user['email'],
                        tariff_name=tariff_name,
                        period=tariff_info.get('period', ''),
                        portal_url=portal_url,
                    )
                    logger.info(f"📧 Portal link emailed to {_tg_user['email']} (TG fallback)")
                elif _tg_user and not _tg_user.get('email'):
                    # Ask user for email for fallback access
                    await send_telegram_notification(
                        tg_id,
                        "📧 <b>Укажите email для резервного доступа</b>\n\n"
                        "Если Telegram будет недоступен, мы отправим "
                        "ссылку на личный кабинет на вашу почту.\n\n"
                        "Отправьте email ответным сообщением:",
                    )
                    from bot_xui.bot import WAITING_EMAIL
                    import time as _time
                    WAITING_EMAIL[tg_id] = _time.time()
                    logger.info(f"📧 Asked tg_id={tg_id} for email (no email on file)")
            except Exception:
                logger.warning("⚠️ Failed to send portal email fallback", exc_info=True)

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

    # ===== 1. Проверка IP (ALWAYS enforced — before parsing body) =====
    verify_yookassa_ip(request)

    # ===== 2. Парсинг данных =====
    try:
        body = await request.body()
        payload = json.loads(body)
    except json.JSONDecodeError:
        logger.error("Invalid JSON in webhook body")
        return Response(status_code=400)

    # ===== 3. Извлечение данных =====
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

        # Verify paid amount from YooKassa webhook matches what we stored
        webhook_amount = float(obj.get("amount", {}).get("value", 0))
        stored_amount = float(payment_data.get("amount") or 0)
        if stored_amount > 0 and abs(webhook_amount - stored_amount) > 0.01:
            logger.error(f"❌ Amount mismatch: webhook={webhook_amount}, stored={stored_amount}, payment={payment_id}")
            update_payment_status(payment_id, "pending")  # revert claim
            return Response(status_code=400)

        # Для веб-заказов: привязать tg_id и user_id из users если есть
        if is_web_order and web_token:
            web_user = get_user_by_web_token(web_token) if web_token else None
            if web_user:
                tg_id = int(web_user.get('tg_id') or 0)
                payment_data = {**payment_data, 'tg_id': tg_id, '_web_user_id': web_user['id']}

                # Process web referral if present
                ref_token = metadata.get("ref")
                if ref_token:
                    from api.db import process_web_referral
                    if process_web_referral(web_user['id'], ref_token):
                        logger.info(f"Web referral applied on payment: user_id={web_user['id']}, ref={ref_token}")

        # Save payment method for autopay if available
        pm = obj.get("payment_method", {})
        if pm.get("saved") and pm.get("id"):
            from api.db import save_user_payment_method, save_user_payment_method_by_id
            pm_id = pm["id"]
            if is_web_order and payment_data.get('_web_user_id'):
                save_user_payment_method_by_id(
                    payment_data['_web_user_id'], pm_id, tariff,
                    vpn_type or "vless",
                )
                logger.info(f"Saved payment method {pm_id} for web user_id={payment_data['_web_user_id']}")
            elif tg_id and int(tg_id) > 0:
                save_user_payment_method(
                    int(tg_id), pm_id, tariff,
                    vpn_type or "vless",
                )
                logger.info(f"Saved payment method {pm_id} for tg_id={tg_id}")

        # Consume promo code if used (both web and bot flows)
        promo_code_str = metadata.get("promo_code")
        if promo_code_str:
            from api.db import get_promocode, use_promocode
            promo = get_promocode(promo_code_str)
            if promo:
                use_promocode(promo["id"], int(tg_id or 0))
                logger.info(f"Promo '{promo_code_str}' consumed for payment {payment_id}")

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
            # Instant admin alert for payment_no_config
            from config import ADMIN_TG_ID
            await send_telegram_notification(
                ADMIN_TG_ID,
                f"🚨 <b>payment_no_config</b>\n\n"
                f"Платёж: {payment_id}\n"
                f"tg_id: {tg_id}\n"
                f"Тариф: {tariff}\n"
                f"VPN тип: {vpn_type}\n"
                f"Web: {is_web_order}\n\n"
                f"VPN конфиг не создан. Вебхук вернёт 500 для повтора.",
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


@app.get("/favicon.png")
async def favicon():
    from fastapi.responses import FileResponse
    return FileResponse("/home/alvik/vpn-service/website/favicon.png", media_type="image/png")


@app.get("/", response_class=HTMLResponse)
async def root():
    """Лендинг: always serve lightweight static HTML (no JS framework needed)"""
    from pathlib import Path
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