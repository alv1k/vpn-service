"""
Web authentication via email/phone codes (passwordless).
Endpoints for sending codes, verifying, and session management.
"""
import logging
import secrets

from fastapi import APIRouter, HTTPException, Response, Request, Depends
from pydantic import BaseModel, EmailStr

from api.db import (
    execute_query,
    get_user_by_web_token,
)
from api.notifications import create_auth_code, verify_code

logger = logging.getLogger(__name__)
auth_router = APIRouter(prefix="/api/auth")


# ─────────────────────────────────────────────
#  Models
# ─────────────────────────────────────────────

class SendCodeRequest(BaseModel):
    email: EmailStr | None = None
    phone: str | None = None


class VerifyRequest(BaseModel):
    email: EmailStr | None = None
    phone: str | None = None
    code: str


class AuthResponse(BaseModel):
    ok: bool
    message: str | None = None
    token: str | None = None


class MeResponse(BaseModel):
    id: int
    email: str | None = None
    phone: str | None = None
    first_name: str | None = None
    subscription_until: str | None = None
    is_active: bool


# ─────────────────────────────────────────────
#  Helpers
# ─────────────────────────────────────────────

def _get_destination(email: str | None, phone: str | None) -> tuple[str, str]:
    """Returns (destination, channel) or raises 400."""
    if email:
        return email.lower().strip(), "email"
    if phone:
        cleaned = phone.strip().replace(" ", "").replace("-", "")
        if not cleaned.startswith("+") or len(cleaned) < 10:
            raise HTTPException(400, "Неверный формат телефона")
        return cleaned, "sms"
    raise HTTPException(400, "Укажите email или телефон")


def _get_or_create_user_by_contact(destination: str, channel: str) -> dict:
    """Find or create user by email/phone. Returns user dict."""
    if channel == "email":
        row = execute_query(
            "SELECT * FROM users WHERE email = %s",
            (destination,), fetch='one',
        )
        if row:
            return row
        token = secrets.token_urlsafe(16)
        user_id = execute_query(
            "INSERT INTO users (email, web_token) VALUES (%s, %s)",
            (destination, token),
        )
        return {'id': user_id, 'web_token': token, 'email': destination}
    else:
        row = execute_query(
            "SELECT * FROM users WHERE phone = %s",
            (destination,), fetch='one',
        )
        if row:
            return row
        token = secrets.token_urlsafe(16)
        user_id = execute_query(
            "INSERT INTO users (phone, web_token) VALUES (%s, %s)",
            (destination, token),
        )
        return {'id': user_id, 'web_token': token, 'phone': destination}


def _get_current_user(request: Request) -> dict:
    """Extract user from session cookie or Authorization header."""
    token = request.cookies.get("session_token")
    if not token:
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            token = auth[7:]
    if not token:
        raise HTTPException(401, "Необходима авторизация")

    session = execute_query(
        "SELECT user_id FROM auth_sessions WHERE token = %s AND expires_at > NOW()",
        (token,), fetch='one',
    )
    if not session:
        raise HTTPException(401, "Сессия истекла")

    user = execute_query(
        "SELECT * FROM users WHERE id = %s",
        (session['user_id'],), fetch='one',
    )
    if not user:
        raise HTTPException(401, "Пользователь не найден")
    return user


# ─────────────────────────────────────────────
#  Endpoints
# ─────────────────────────────────────────────

@auth_router.post("/send-code", response_model=AuthResponse)
async def send_code(req: SendCodeRequest):
    """Send auth code to email or phone."""
    destination, channel = _get_destination(req.email, req.phone)

    result = create_auth_code(destination, channel)
    if result is None:
        raise HTTPException(
            429,
            "Слишком много запросов. Подождите несколько минут.",
        )

    return AuthResponse(ok=True, message="Код отправлен")


@auth_router.post("/verify", response_model=AuthResponse)
async def verify(req: VerifyRequest, response: Response):
    """Verify code and create session."""
    destination, channel = _get_destination(req.email, req.phone)

    if not req.code or len(req.code) != 6:
        raise HTTPException(400, "Неверный формат кода")

    if not verify_code(destination, req.code):
        raise HTTPException(400, "Неверный или просроченный код")

    user = _get_or_create_user_by_contact(destination, channel)

    # Cleanup: remove expired sessions and keep max 10 per user
    execute_query(
        "DELETE FROM auth_sessions WHERE user_id = %s AND expires_at <= NOW()",
        (user['id'],),
    )
    execute_query(
        "DELETE FROM auth_sessions WHERE user_id = %s AND id NOT IN "
        "(SELECT id FROM (SELECT id FROM auth_sessions WHERE user_id = %s "
        "ORDER BY created_at DESC LIMIT 9) t)",
        (user['id'], user['id']),
    )

    # Create session token (30 days)
    session_token = secrets.token_urlsafe(32)
    execute_query(
        "INSERT INTO auth_sessions (user_id, token, expires_at) "
        "VALUES (%s, %s, NOW() + INTERVAL 30 DAY)",
        (user['id'], session_token),
    )

    # Set httponly cookie
    response.set_cookie(
        key="session_token",
        value=session_token,
        httponly=True,
        secure=True,
        samesite="lax",
        max_age=30 * 24 * 3600,
    )

    return AuthResponse(
        ok=True,
        message="Авторизация успешна",
        token=session_token,
    )


@auth_router.get("/me", response_model=MeResponse)
async def me(request: Request):
    """Get current authenticated user info."""
    user = _get_current_user(request)
    from datetime import datetime
    sub_until = user.get('subscription_until')
    is_active = bool(sub_until and sub_until > datetime.now())

    return MeResponse(
        id=user['id'],
        email=user.get('email'),
        phone=user.get('phone'),
        first_name=user.get('first_name'),
        subscription_until=sub_until.isoformat() if sub_until else None,
        is_active=is_active,
    )


@auth_router.post("/logout", response_model=AuthResponse)
async def logout(request: Request, response: Response):
    """Invalidate current session."""
    token = request.cookies.get("session_token")
    if token:
        execute_query(
            "DELETE FROM auth_sessions WHERE token = %s",
            (token,),
        )
        response.delete_cookie("session_token")
    return AuthResponse(ok=True, message="Вы вышли из системы")
