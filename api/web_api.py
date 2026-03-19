"""
REST API для веб-сайта (Beget).
Эндпоинты для регистрации по email, оплаты, статуса.
"""
import logging
import secrets
import uuid

from fastapi import APIRouter, HTTPException
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


class OrderRequest(BaseModel):
    email: EmailStr
    tariff_id: str
    promo_code: str | None = None


class OrderResponse(BaseModel):
    payment_url: str
    payment_id: str


class StatusResponse(BaseModel):
    status: str
    portal_url: str | None = None


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
    if promo["expires_at"] and promo["expires_at"] < __import__("datetime").datetime.now():
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


@web_api_router.post("/order", response_model=OrderResponse)
async def create_order(req: OrderRequest):
    """Создать заказ: регистрирует пользователя по email и создаёт платёж в YooKassa."""
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

    payment = Payment.create(
        {
            "amount": {"value": str(price), "currency": "RUB"},
            "confirmation": {
                "type": "redirect",
                "return_url": f"https://344988.snk.wtf/my/{user['web_token']}",
            },
            "capture": True,
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
    )


@web_api_router.get("/status/{payment_id}", response_model=StatusResponse)
async def check_status(payment_id: str):
    """Проверить статус платежа (polling с фронтенда после оплаты)."""
    from api.db import get_payment_by_id
    payment = get_payment_by_id(payment_id)
    if not payment:
        raise HTTPException(404, "Платёж не найден")

    status = payment.get("status", "pending")

    portal_url = None
    if status == "paid":
        # Найти web_token по tg_id или email из metadata
        tg_id = payment.get("tg_id")
        if tg_id:
            from api.db import get_web_token
            token = get_web_token(tg_id)
            if token:
                portal_url = f"https://344988.snk.wtf/my/{token}"

    return StatusResponse(status=status, portal_url=portal_url)
