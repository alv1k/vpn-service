"""Admin panel authentication and session management."""
import logging
import os
import secrets
from datetime import datetime, timezone

from fastapi import HTTPException, Request, Response, WebSocket

logger = logging.getLogger("admin.auth")

SESSION_MAX_AGE = 604800  # 7 days
_MAX_SESSIONS = 100

# In-memory session store: token -> created_at timestamp
_sessions: dict[str, float] = {}


def _purge_expired_sessions():
    """Remove expired sessions to prevent unbounded growth."""
    now = datetime.now(timezone.utc).timestamp()
    expired = [t for t, ts in _sessions.items() if now - ts > SESSION_MAX_AGE]
    for t in expired:
        del _sessions[t]
    if len(_sessions) > _MAX_SESSIONS:
        sorted_sessions = sorted(_sessions.items(), key=lambda x: x[1])
        for t, _ in sorted_sessions[:len(_sessions) - _MAX_SESSIONS]:
            del _sessions[t]


def _require_admin_session(request: Request = None, websocket: WebSocket = None):
    """Verify connect.sid session cookie for admin routes and websockets."""
    conn = request or websocket
    if conn is None:
        raise HTTPException(status_code=401, detail="Unauthorized")
    token = conn.cookies.get("connect.sid")
    if not token or token not in _sessions:
        raise HTTPException(status_code=401, detail="Unauthorized")
    created = _sessions[token]
    now = datetime.now(timezone.utc).timestamp()
    if now - created > SESSION_MAX_AGE:
        del _sessions[token]
        raise HTTPException(status_code=401, detail="Session expired")
    # Sliding window: refresh session on each request
    _sessions[token] = now


async def _ws_authenticate(websocket: WebSocket) -> bool:
    """Verify session cookie for WebSocket handshake."""
    cookie = websocket.cookies.get("connect.sid")
    if not cookie or cookie not in _sessions:
        return False
    created = _sessions[cookie]
    now = datetime.now(timezone.utc).timestamp()
    if now - created > SESSION_MAX_AGE:
        del _sessions[cookie]
        return False
    _sessions[cookie] = now
    return True


async def handle_login(request: Request, response: Response):
    """Handle admin login and set connect.sid session cookie."""
    body = await request.json()
    password = body.get("password", "")

    pwd_check = os.getenv("ADMIN_PASSWORD") or os.getenv("AMNEZIA_WG_API_PASSWORD", "")
    if not pwd_check or password != pwd_check:
        raise HTTPException(status_code=401, detail="Incorrect password")

    _purge_expired_sessions()
    token = secrets.token_hex(24)
    _sessions[token] = datetime.now(timezone.utc).timestamp()

    response.set_cookie(
        key="connect.sid",
        value=token,
        httponly=True,
        secure=False,
        samesite="lax",
        max_age=SESSION_MAX_AGE,
    )
    return {"success": True}
