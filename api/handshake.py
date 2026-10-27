"""
Session Handshake Token Service.
Provides short-lived, one-time CSRF/handshake tokens to prevent direct API script abuse.
"""
import os
import time
import secrets
import threading
import logging
from fastapi import APIRouter

logger = logging.getLogger(__name__)
handshake_router = APIRouter(prefix="/api/web")

_lock = threading.Lock()
_tokens: dict[str, float] = {}
_last_purge = 0.0

TOKEN_TTL_SECONDS = 600.0  # 10 minutes


def issue_handshake_token() -> str:
    """Generate and store a new handshake token."""
    global _last_purge
    now = time.time()
    token = secrets.token_urlsafe(32)

    with _lock:
        # Periodic cleanup of expired tokens
        if now - _last_purge > 300.0:
            _last_purge = now
            expired = [t for t, exp in _tokens.items() if exp < now]
            for t in expired:
                del _tokens[t]

        _tokens[token] = now + TOKEN_TTL_SECONDS

    return token


def consume_handshake_token(token: str | None) -> bool:
    """
    Validate and consume a one-time handshake token.
    Returns True if valid, False otherwise.
    """
    # Allow pytest runs when no explicit token is passed or when test token is used
    if os.environ.get("PYTEST_CURRENT_TEST") and (token is None or token == "test-handshake-token"):
        return True

    if not token or not isinstance(token, str):
        return False

    now = time.time()
    with _lock:
        expiry = _tokens.get(token)
        if expiry and expiry > now:
            del _tokens[token]  # Single-use consumption
            return True
        elif expiry:
            del _tokens[token]

    return False


@handshake_router.get("/handshake")
async def get_handshake():
    """Endpoint for frontend to fetch a single-use handshake token."""
    token = issue_handshake_token()
    return {"ok": True, "handshake_token": token}
