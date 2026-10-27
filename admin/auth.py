"""Admin panel authentication and persistent session management."""
import logging
import os
import secrets
import sqlite3
from datetime import datetime, timezone

import bcrypt
from fastapi import HTTPException, Request, Response, WebSocket

logger = logging.getLogger("admin.auth")

SESSION_MAX_AGE = 604800  # 7 days
DB_PATH = os.path.join(os.path.dirname(__file__), "sessions.db")
HTPASSWD_PATH = "/etc/nginx/.htpasswd_admin"


def _get_db():
    conn = sqlite3.connect(DB_PATH, timeout=10.0)
    conn.row_factory = sqlite3.Row
    return conn


def init_auth_db():
    """Ensure sessions table exists."""
    with _get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS admin_sessions (
                token TEXT PRIMARY KEY,
                username TEXT NOT NULL,
                created_at REAL NOT NULL,
                last_seen REAL NOT NULL
            )
        """)
        conn.commit()


# Initialize table on import
init_auth_db()


def _purge_expired_sessions():
    """Remove expired sessions from SQLite DB."""
    now = datetime.now(timezone.utc).timestamp()
    try:
        with _get_db() as conn:
            conn.execute("DELETE FROM admin_sessions WHERE ? - last_seen > ?", (now, SESSION_MAX_AGE))
            conn.commit()
    except Exception as e:
        logger.warning(f"Error purging expired sessions: {e}")


def verify_credentials(username: str, password: str) -> bool:
    """Verify credentials against .htpasswd_admin and environment variables."""
    # 1. Check .htpasswd_admin file (bcrypt or plain)
    if os.path.exists(HTPASSWD_PATH):
        try:
            with open(HTPASSWD_PATH, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or ":" not in line:
                        continue
                    u, h = line.split(":", 1)
                    if username and u != username:
                        continue
                    # Handle bcrypt hash ($2y$ or $2b$ or $2a$)
                    if h.startswith("$2"):
                        h_std = h.replace("$2y$", "$2b$").encode("utf-8")
                        if bcrypt.checkpw(password.encode("utf-8"), h_std):
                            return True
                    elif h == password:
                        return True
        except Exception as e:
            logger.warning(f"Error checking htpasswd: {e}")

    # 2. Check ADMIN_PASSWORD or AMNEZIA_WG_API_PASSWORD
    env_pass = os.getenv("ADMIN_PASSWORD") or os.getenv("AMNEZIA_WG_API_PASSWORD", "")
    if env_pass and password == env_pass:
        return True

    return False


def _require_admin_session(request: Request = None, websocket: WebSocket = None):
    """Verify connect.sid session cookie for admin routes and websockets."""
    conn = request or websocket
    if conn is None:
        raise HTTPException(status_code=401, detail="Unauthorized")
    token = conn.cookies.get("connect.sid")
    if not token:
        raise HTTPException(status_code=401, detail="Unauthorized")

    now = datetime.now(timezone.utc).timestamp()
    try:
        with _get_db() as db:
            cur = db.execute("SELECT username, last_seen FROM admin_sessions WHERE token = ?", (token,))
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=401, detail="Unauthorized")
            if now - row["last_seen"] > SESSION_MAX_AGE:
                db.execute("DELETE FROM admin_sessions WHERE token = ?", (token,))
                db.commit()
                raise HTTPException(status_code=401, detail="Session expired")
            # Update last_seen
            db.execute("UPDATE admin_sessions SET last_seen = ? WHERE token = ?", (now, token))
            db.commit()
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Session validation DB error: {e}")
        raise HTTPException(status_code=401, detail="Unauthorized")


async def _ws_authenticate(websocket: WebSocket) -> bool:
    """Verify session cookie for WebSocket handshake."""
    cookie = websocket.cookies.get("connect.sid")
    if not cookie:
        return False

    now = datetime.now(timezone.utc).timestamp()
    try:
        with _get_db() as db:
            cur = db.execute("SELECT last_seen FROM admin_sessions WHERE token = ?", (cookie,))
            row = cur.fetchone()
            if not row:
                return False
            if now - row["last_seen"] > SESSION_MAX_AGE:
                db.execute("DELETE FROM admin_sessions WHERE token = ?", (cookie,))
                db.commit()
                return False
            db.execute("UPDATE admin_sessions SET last_seen = ? WHERE token = ?", (now, cookie))
            db.commit()
            return True
    except Exception:
        return False


async def handle_login(request: Request, response: Response):
    """Handle admin login and set persistent connect.sid session cookie."""
    body = await request.json()
    username = (body.get("username") or "alvik").strip()
    password = (body.get("password") or "").strip()

    if not password or not verify_credentials(username, password):
        raise HTTPException(status_code=401, detail="Неверный логин или пароль")

    _purge_expired_sessions()
    token = secrets.token_hex(24)
    now = datetime.now(timezone.utc).timestamp()

    with _get_db() as conn:
        conn.execute(
            "INSERT INTO admin_sessions (token, username, created_at, last_seen) VALUES (?, ?, ?, ?)",
            (token, username, now, now),
        )
        conn.commit()

    response.set_cookie(
        key="connect.sid",
        value=token,
        httponly=True,
        secure=False,
        samesite="lax",
        max_age=SESSION_MAX_AGE,
        path="/",
    )
    return {"success": True, "username": username}


async def handle_logout(request: Request, response: Response):
    """Handle admin logout and delete session."""
    token = request.cookies.get("connect.sid")
    if token:
        try:
            with _get_db() as conn:
                conn.execute("DELETE FROM admin_sessions WHERE token = ?", (token,))
                conn.commit()
        except Exception:
            pass

    response.delete_cookie(key="connect.sid", path="/")
    return {"success": True}


async def get_session_status(request: Request):
    """Check if current session is authenticated."""
    token = request.cookies.get("connect.sid")
    if not token:
        return {"authenticated": False}

    now = datetime.now(timezone.utc).timestamp()
    try:
        with _get_db() as db:
            cur = db.execute("SELECT username, last_seen FROM admin_sessions WHERE token = ?", (token,))
            row = cur.fetchone()
            if not row or (now - row["last_seen"] > SESSION_MAX_AGE):
                return {"authenticated": False}
            return {"authenticated": True, "username": row["username"]}
    except Exception:
        return {"authenticated": False}
