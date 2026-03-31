"""
REST API для веб-сайта (Beget).
Эндпоинты для регистрации по email, оплаты, статуса.
"""
import logging
import secrets
import uuid

from datetime import datetime
from fastapi import APIRouter, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, EmailStr
from yookassa import Configuration, Payment

from config import YOO_KASSA_SHOP_ID, YOO_KASSA_SECRET_KEY
from bot_xui.tariffs import TARIFFS
from api.db import execute_query, get_user_by_web_token, get_keys_by_tg_id

logger = logging.getLogger(__name__)
web_api_router = APIRouter(prefix="/api/web")


# ─────────────────────────────────────────────
#  Models
# ─────────────────────────────────────────────

class PromoRequest(BaseModel):
    code: str
    tariff_id: str | None = None


class PromoResponse(BaseModel):
    valid: bool
    type: str | None = None
    value: int | None = None
    discount_price: float | None = None
    message: str | None = None


class OrderCodeRequest(BaseModel):
    email: EmailStr


class OrderRequest(BaseModel):
    email: EmailStr
    tariff_id: str
    code: str
    promo_code: str | None = None
    ref: str | None = None


class OrderResponse(BaseModel):
    payment_url: str
    payment_id: str
    web_token: str


class StatusResponse(BaseModel):
    status: str


# ─────────────────────────────────────────────
#  Helpers
# ─────────────────────────────────────────────

def _get_or_create_web_user(email: str) -> dict:
    """Найти или создать пользователя по email. Возвращает dict с id, web_token."""
    row = execute_query(
        "SELECT id, web_token, tg_id FROM users WHERE email = %s",
        (email,), fetch='one',
    )
    if row:
        return row

    token = secrets.token_urlsafe(16)
    user_id = execute_query(
        "INSERT INTO users (email, web_token) VALUES (%s, %s)",
        (email, token),
    )
    return {'id': user_id, 'web_token': token, 'tg_id': None}


# ─────────────────────────────────────────────
#  Endpoints
# ─────────────────────────────────────────────

@web_api_router.get("/tariffs")
async def get_tariffs():
    """Список тарифов для сайта (без тестовых и служебных)."""
    result = []
    for tid, t in TARIFFS.items():
        if t.get("is_test") or tid == "admin_test":
            continue
        result.append({
            "id": tid,
            "name": t["name"],
            "price": t["price"],
            "period": t["period"],
            "days": t.get("days", 0),
            "device_limit": t.get("device_limit", 1),
        })
    result.sort(key=lambda x: x["days"])
    return result


@web_api_router.post("/promo/validate", response_model=PromoResponse)
async def validate_promo(req: PromoRequest):
    """Проверить промокод и вернуть скидку."""
    from api.db import get_promocode
    promo = get_promocode(req.code)

    if not promo:
        return PromoResponse(valid=False, message="Промокод не найден")
    if not promo["is_active"]:
        return PromoResponse(valid=False, message="Промокод неактивен")
    if promo["expires_at"] and promo["expires_at"] < datetime.now():
        return PromoResponse(valid=False, message="Промокод истёк")
    if promo["max_uses"] and promo["used_count"] >= promo["max_uses"]:
        return PromoResponse(valid=False, message="Промокод исчерпан")

    result = PromoResponse(
        valid=True,
        type=promo["type"],
        value=promo["value"],
    )

    # Calculate discount price if tariff provided
    if req.tariff_id and promo["type"] in ("discount", "permanent_discount"):
        tariff = TARIFFS.get(req.tariff_id)
        if tariff:
            discounted = max(1, round(tariff["price"] * (100 - promo["value"]) / 100))
            result.discount_price = discounted
            result.message = f"-{promo['value']}%"
    elif promo["type"] == "days":
        result.message = f"+{promo['value']} дней бесплатно"

    return result


@web_api_router.post("/order/send-code")
async def order_send_code(req: OrderCodeRequest):
    """Send verification code to email before placing an order."""
    from api.notifications import create_auth_code
    code = create_auth_code(req.email.lower().strip(), "email")
    if not code:
        raise HTTPException(429, "Слишком много запросов. Попробуйте позже.")
    return {"ok": True, "message": "Код отправлен на почту"}


@web_api_router.post("/order", response_model=OrderResponse)
async def create_order(req: OrderRequest):
    """Создать заказ: проверяет код, регистрирует пользователя и создаёт платёж."""
    from api.notifications import verify_code
    email = req.email.lower().strip()
    if not verify_code(email, req.code.strip()):
        raise HTTPException(400, "Неверный или истёкший код")

    tariff = TARIFFS.get(req.tariff_id)
    if not tariff or tariff.get("is_test"):
        raise HTTPException(400, "Тариф не найден")

    user = _get_or_create_web_user(req.email)

    # Apply promo discount
    price = tariff["price"]
    promo_meta = None
    if req.promo_code:
        from api.db import get_promocode
        promo = get_promocode(req.promo_code)
        if promo and promo["is_active"] and promo["type"] in ("discount", "permanent_discount"):
            price = max(1, round(price * (100 - promo["value"]) / 100))
            promo_meta = req.promo_code
            logger.info(f"Promo {req.promo_code}: {tariff['price']} -> {price} RUB")

    Configuration.account_id = YOO_KASSA_SHOP_ID
    Configuration.secret_key = YOO_KASSA_SECRET_KEY

    metadata = {
        "tariff": req.tariff_id,
        "vpn_type": "vless",
        "email": req.email,
        "web_token": user["web_token"],
        "source": "web",
    }
    if promo_meta:
        metadata["promo_code"] = promo_meta
    if req.ref:
        metadata["ref"] = req.ref

    payment = Payment.create(
        {
            "amount": {"value": str(price), "currency": "RUB"},
            "confirmation": {
                "type": "redirect",
                "return_url": f"https://344988.snk.wtf/my/{user['web_token']}",
            },
            "capture": True,
            "save_payment_method": True,
            "description": f"Оплата тарифа {tariff['name']}",
            "metadata": metadata,
        },
        str(uuid.uuid4()),
    )

    # Сохранить платёж в БД
    from api.db import create_payment
    create_payment(
        payment_id=payment.id,
        tg_id=user.get("tg_id") or 0,
        tariff=req.tariff_id,
        amount=price,
        status="pending",
    )

    logger.info(f"Web order: {payment.id} for {req.email}, tariff={req.tariff_id}, price={price}")

    return OrderResponse(
        payment_url=payment.confirmation.confirmation_url,
        payment_id=payment.id,
        web_token=user["web_token"],
    )


@web_api_router.get("/status/{payment_id}", response_model=StatusResponse)
async def check_status(payment_id: str):
    """Проверить статус платежа (polling с фронтенда после оплаты)."""
    from api.db import get_payment_by_id
    payment = get_payment_by_id(payment_id)
    if not payment:
        raise HTTPException(404, "Платёж не найден")

    return StatusResponse(status=payment.get("status", "pending"))


# ─────────────────────────────────────────────
#  Free test activation (web users)
# ─────────────────────────────────────────────

class TestActivateRequest(BaseModel):
    web_token: str
    ref: str | None = None


@web_api_router.post("/activate-test")
async def activate_test(req: TestActivateRequest):
    """Activate free 3-day VLESS test for web user."""
    from datetime import datetime, timedelta, timezone
    from api.db import (
        create_vpn_key, update_user_subscription_by_id,
    )
    from bot_xui.utils import XUIClient, generate_vless_link
    from config import (
        VLESS_DOMAIN, VLESS_PORT, VLESS_PATH,
        VLESS_PBK, VLESS_SID, VLESS_SNI, VLESS_INBOUND_ID,
    )

    user = get_user_by_web_token(req.web_token)
    if not user:
        raise HTTPException(404, "Пользователь не найден")

    # Atomic claim: UPDATE ... WHERE test_vless_activated=0 prevents TOCTOU race
    from api.db import get_db
    _db = get_db()
    _cur = _db.cursor()
    try:
        _cur.execute(
            "UPDATE users SET test_vless_activated = 1 "
            "WHERE id = %s AND test_vless_activated = 0",
            (user['id'],),
        )
        _db.commit()
        if _cur.rowcount == 0:
            raise HTTPException(400, "Тестовый период уже был активирован")
    finally:
        _cur.close()
        _db.close()

    # Calculate expiry: 3 days, rounded to 23:59:59 UTC+9
    tz_tokyo = timezone(timedelta(hours=9))
    test_hours = TARIFFS["test_24h"]["hours"]
    raw_end = datetime.now(timezone.utc) + timedelta(hours=test_hours)
    end_tokyo = raw_end.astimezone(tz_tokyo).replace(hour=23, minute=59, second=59, microsecond=0)
    expiry_ms = int(end_tokyo.timestamp() * 1000)
    expires_at = end_tokyo.astimezone(timezone.utc)

    # Create VLESS client in XUI
    import uuid as _uuid
    client_email = f"web-test-{user['id']}-{_uuid.uuid4().hex[:8]}"
    client_uuid = str(_uuid.uuid4())
    inbound_id = int(VLESS_INBOUND_ID)

    xui = XUIClient()
    success = xui.add_client(
        inbound_id=inbound_id,
        email=client_email,
        tg_id=0,
        uuid=client_uuid,
        expiry_time=expiry_ms,
        total_gb=0,
        limit_ip=1,
    )
    if not success:
        raise HTTPException(500, "Ошибка создания VPN конфига")

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

    sub_url = xui.get_subscription_url_by_uuid(client_uuid)

    create_vpn_key(
        tg_id=0, payment_id=None,
        client_id=client_uuid, client_name=client_email,
        client_ip=None, client_public_key=None,
        vless_link=vless_link, expires_at=expires_at, vpn_type="vless",
        subscription_link=sub_url,
        user_id=user['id'],
    )

    # Set subscription_until
    sub_until = expires_at.replace(tzinfo=None)
    update_user_subscription_by_id(user['id'], sub_until)

    # test_vless_activated already set atomically above

    # Process referral if present
    if req.ref:
        from api.db import process_web_referral
        if process_web_referral(user['id'], req.ref):
            logger.info(f"Web referral applied on test activation: user_id={user['id']}, ref={req.ref}")

    logger.info(f"Web test activated: user_id={user['id']}, client={client_email}")
    return {"ok": True, "message": "Тест активирован на 3 дня"}


# ─────────────────────────────────────────────
#  Site analytics
# ─────────────────────────────────────────────

ALLOWED_EVENTS = {"visit", "click_proxy", "click_connect"}


class SiteEvent(BaseModel):
    event: str
    visitor_id: str


@web_api_router.post("/event")
async def track_event(body: SiteEvent, request: Request):
    if body.event not in ALLOWED_EVENTS:
        raise HTTPException(400, "Unknown event")
    if not body.visitor_id or len(body.visitor_id) > 64:
        raise HTTPException(400, "Bad visitor_id")

    ip = request.headers.get("x-real-ip", request.client.host)

    # For 'visit' — only record once per visitor_id
    if body.event == "visit":
        existing = execute_query(
            "SELECT id FROM site_events WHERE visitor_id=%s AND event_type='visit' LIMIT 1",
            (body.visitor_id,), fetch='one',
        )
        if existing:
            return {"ok": True, "dup": True}

    execute_query(
        "INSERT INTO site_events (event_type, visitor_id, ip) VALUES (%s, %s, %s)",
        (body.event, body.visitor_id, ip),
    )
    return {"ok": True}


# ─────────────────────────────────────────────
#  Support contact form
# ─────────────────────────────────────────────

class SupportCodeRequest(BaseModel):
    email: EmailStr


class SupportRequest(BaseModel):
    email: EmailStr
    message: str
    code: str


@web_api_router.post("/support/send-code")
async def support_send_code(req: SupportCodeRequest):
    """Send verification code to email before accepting support message."""
    from api.notifications import create_auth_code
    code = create_auth_code(req.email.lower().strip(), "email")
    if not code:
        raise HTTPException(429, "Слишком много запросов. Попробуйте позже.")
    return {"ok": True, "message": "Код отправлен на почту"}


@web_api_router.post("/support/contact")
async def support_contact(req: SupportRequest, request: Request):
    """Accept support message from web portal, verify code first."""
    from api.notifications import (
        verify_code, send_support_autoreply, send_support_message_to_team,
    )

    email = req.email.lower().strip()

    # Verify the code
    if not verify_code(email, req.code.strip()):
        raise HTTPException(400, "Неверный или истёкший код")

    msg = req.message.strip()
    if not msg or len(msg) > 5000:
        raise HTTPException(400, "Сообщение должно быть от 1 до 5000 символов")

    # Forward to team inbox
    send_support_message_to_team(email, msg)

    # Auto-reply to user
    send_support_autoreply(email)

    # Notify admin in Telegram
    try:
        import httpx as _httpx
        from config import TELEGRAM_BOT_TOKEN, ADMIN_TG_ID
        tg_text = (
            f"✉️ <b>Обращение с сайта</b>\n\n"
            f"📧 {email}\n\n"
            f"💬 {msg[:500]}"
        )
        async with _httpx.AsyncClient() as client:
            await client.post(
                f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                json={"chat_id": ADMIN_TG_ID, "text": tg_text, "parse_mode": "HTML"},
            )
    except Exception as e:
        logger.warning(f"Failed to notify admin in TG: {e}")

    logger.info(f"Support contact from {email}")
    return {"ok": True, "message": "Сообщение отправлено"}
