"""
AWG 2.0 API — drop-in replacement for wg-easy REST API.

Implements the same endpoints so existing bot code works without changes.
"""
import logging
import random
import secrets
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from io import BytesIO

import os

import qrcode
from fastapi import FastAPI, Request, Response, HTTPException
from fastapi.responses import PlainTextResponse, JSONResponse

from . import db, awg_manager
from admin.routes import router as admin_router, get_admin_page_route

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger("awg_api")

# In-memory session store
_sessions: dict[str, float] = {}  # token -> created_at timestamp

SESSION_MAX_AGE = 86400  # 24h
_MAX_SESSIONS = 100


def _purge_expired_sessions():
    """Remove expired sessions to prevent unbounded growth."""
    now = datetime.now(timezone.utc).timestamp()
    expired = [t for t, ts in _sessions.items() if now - ts > SESSION_MAX_AGE]
    for t in expired:
        del _sessions[t]
    # If still over limit, remove oldest
    if len(_sessions) > _MAX_SESSIONS:
        sorted_sessions = sorted(_sessions.items(), key=lambda x: x[1])
        for t, _ in sorted_sessions[:len(_sessions) - _MAX_SESSIONS]:
            del _sessions[t]


def _check_session_from_request(request: Request):
    """Extract connect.sid cookie manually (dots in cookie names break FastAPI Cookie())."""
    token = request.cookies.get("connect.sid")
    if not token or token not in _sessions:
        raise HTTPException(status_code=401, detail="Unauthorized")
    created = _sessions[token]
    if datetime.now(timezone.utc).timestamp() - created > SESSION_MAX_AGE:
        del _sessions[token]
        raise HTTPException(status_code=401, detail="Session expired")


def _client_to_json(c: dict) -> dict:
    """Convert DB client row to wg-easy compatible JSON format."""
    created = c.get("created_at")
    updated = c.get("updated_at")
    if isinstance(created, datetime):
        created = created.strftime("%Y-%m-%dT%H:%M:%S.000Z")
    if isinstance(updated, datetime):
        updated = updated.strftime("%Y-%m-%dT%H:%M:%S.000Z")

    return {
        "id": c["id"],
        "name": c["name"],
        "address": c["address"],
        "publicKey": c["public_key"],
        "enabled": bool(c.get("enabled", True)),
        "createdAt": created or "",
        "updatedAt": updated or "",
        "persistentKeepalive": str(awg_manager.KEEPALIVE),
    }


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("Starting AWG 2.0 API")
    db.init_db()

    srv = db.get_server_config()
    if not srv:
        logger.info("No server config found — generating new AWG 2.0 keys")
        priv, pub = awg_manager.generate_keypair()
        from .config import AWG_PARAMS_DEFAULTS, LISTEN_PORT
        # Resolve H1-H4 range tuples to random integers
        resolved = {}
        for k, v in AWG_PARAMS_DEFAULTS.items():
            resolved[k] = random.randint(v[0], v[1]) if isinstance(v, tuple) else v
        cfg = {
            "private_key": priv, "public_key": pub,
            "listen_port": LISTEN_PORT,
            **resolved,
            "i1": None, "i2": None, "i3": None, "i4": None, "i5": None,
        }
        db.save_server_config(cfg)
        logger.info(f"Server config created: pub={pub}")

    yield
    # Shutdown
    logger.info("AWG 2.0 API shutting down")


app = FastAPI(lifespan=lifespan)
app.include_router(admin_router)


app.get("/")(get_admin_page_route())


# ── Auth ─────────────────────────────────────────────────────────────────────

@app.post("/api/session")
async def login(request: Request, response: Response):
    body = await request.json()
    password = body.get("password", "")

    from .config import API_PASSWORD
    if password != API_PASSWORD:
        raise HTTPException(status_code=401, detail="Incorrect password")

    _purge_expired_sessions()
    token = secrets.token_hex(24)
    _sessions[token] = datetime.now(timezone.utc).timestamp()

    response.set_cookie(
        key="connect.sid", value=token,
        httponly=True, secure=True, samesite="lax", max_age=SESSION_MAX_AGE,
    )
    return {"success": True}


# ── Clients CRUD ─────────────────────────────────────────────────────────────

@app.post("/api/wireguard/client")
async def create_client(request: Request):
    _check_session_from_request(request)

    body = await request.json()
    name = body.get("name", "unnamed")

    priv, pub = awg_manager.generate_keypair()
    psk = awg_manager.generate_preshared_key()
    address = db.next_free_address()

    client = db.create_client(name, address, priv, pub, psk)

    # Regenerate server conf and reload
    awg_manager.write_server_conf()
    if awg_manager.is_interface_up():
        awg_manager.reload_interface()

    return _client_to_json(client)


@app.get("/api/wireguard/client")
async def list_clients(request: Request):
    _check_session_from_request(request)
    clients = db.list_clients()
    return [_client_to_json(c) for c in clients]


@app.get("/api/wireguard/client/{client_id}/configuration")
async def get_client_config(client_id: str, request: Request):
    _check_session_from_request(request)
    conf = awg_manager.generate_client_conf(client_id)
    if not conf:
        raise HTTPException(status_code=404, detail="Client not found")
    return PlainTextResponse(conf)


@app.get("/api/wireguard/client/{client_id}/qrcode.svg")
async def get_client_qr(client_id: str, request: Request):
    _check_session_from_request(request)
    conf = awg_manager.generate_client_conf(client_id)
    if not conf:
        raise HTTPException(status_code=404, detail="Client not found")

    qr = qrcode.QRCode(version=1, box_size=10, border=4)
    qr.add_data(conf)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    bio = BytesIO()
    img.save(bio, "PNG")
    bio.seek(0)
    return Response(content=bio.getvalue(), media_type="image/png")


@app.delete("/api/wireguard/client/{client_id}")
async def delete_client(client_id: str, request: Request):
    _check_session_from_request(request)
    if not db.delete_client(client_id):
        raise HTTPException(status_code=404, detail="Client not found")

    awg_manager.write_server_conf()
    if awg_manager.is_interface_up():
        awg_manager.reload_interface()

    return Response(status_code=204)


@app.post("/api/wireguard/client/{client_id}/enable")
async def enable_client(client_id: str, request: Request):
    _check_session_from_request(request)
    if not db.update_client_enabled(client_id, True):
        raise HTTPException(status_code=404)

    awg_manager.write_server_conf()
    if awg_manager.is_interface_up():
        awg_manager.reload_interface()

    return Response(status_code=204)


@app.post("/api/wireguard/client/{client_id}/disable")
async def disable_client(client_id: str, request: Request):
    _check_session_from_request(request)
    if not db.update_client_enabled(client_id, False):
        raise HTTPException(status_code=404)

    awg_manager.write_server_conf()
    if awg_manager.is_interface_up():
        awg_manager.reload_interface()

    return Response(status_code=204)


@app.put("/api/wireguard/client/{client_id}/name")
async def update_name(client_id: str, request: Request):
    _check_session_from_request(request)
    body = await request.json()
    if not db.update_client_name(client_id, body.get("name", "")):
        raise HTTPException(status_code=404)
    return Response(status_code=204)


@app.put("/api/wireguard/client/{client_id}/address")
async def update_address(client_id: str, request: Request):
    _check_session_from_request(request)
    body = await request.json()
    new_addr = body.get("address", "")
    if not db.update_client_address(client_id, new_addr):
        raise HTTPException(status_code=404)

    awg_manager.write_server_conf()
    if awg_manager.is_interface_up():
        awg_manager.reload_interface()

    return Response(status_code=204)
