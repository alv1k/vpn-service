"""
Standalone Admin Panel API Service.
Serves TIIN Admin Panel UI, WebSockets, and metrics endpoints.
"""
import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response, WebSocket, WebSocketDisconnect, Query
from fastapi.responses import FileResponse, JSONResponse

from admin.routes import (
    router as admin_router,
    get_admin_page_route,
    _admin_ws_connections,
    _broadcast_ws,
    _get_online_users,
    _clean,
)
from admin.auth import handle_login, _ws_authenticate

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger("admin_panel")

_broadcast_task: asyncio.Task | None = None


async def _periodic_broadcast():
    """Push online user snapshot to all WS clients every 10 seconds."""
    while True:
        await asyncio.sleep(10)
        if not _admin_ws_connections:
            continue
        try:
            online, _ = _get_online_users()
            await _broadcast_ws({"type": "online", "data": online})
        except Exception as e:
            logger.warning(f"WS broadcast error: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _broadcast_task
    logger.info("Starting Standalone Admin Panel Service")
    _broadcast_task = asyncio.create_task(_periodic_broadcast())
    logger.info("WS periodic broadcast task started")
    yield
    if _broadcast_task:
        _broadcast_task.cancel()
    logger.info("Admin Panel Service shutting down")


app = FastAPI(title="TIIN Admin Panel", lifespan=lifespan)

# Mount main admin router
app.include_router(admin_router)

# Mount UI root and favicon
_admin_page = get_admin_page_route()
app.api_route("/", methods=["GET", "HEAD"])(_admin_page)


@app.get("/favicon.png")
async def favicon():
    path = os.path.join(os.path.dirname(__file__), "static", "favicon.png")
    return FileResponse(path, media_type="image/png")


# Session Login
@app.post("/api/session")
async def session_login(request: Request, response: Response):
    return await handle_login(request, response)


# WebSocket Gateway
@app.websocket("/api/admin/ws")
async def admin_websocket(websocket: WebSocket):
    """WebSocket for real-time admin panel updates."""
    if not await _ws_authenticate(websocket):
        await websocket.close(code=4001, reason="Unauthorized")
        return

    await websocket.accept()
    _admin_ws_connections.add(websocket)

    # Send initial snapshot
    try:
        online, _ = _get_online_users()
        await websocket.send_text(json.dumps({
            "type": "online",
            "data": online,
        }))
    except Exception:
        pass

    try:
        while True:
            data = await websocket.receive_text()
            msg = json.loads(data)
            if msg.get("type") == "ping":
                await websocket.send_text(json.dumps({"type": "pong"}))
    except WebSocketDisconnect:
        _admin_ws_connections.discard(websocket)
    except Exception:
        _admin_ws_connections.discard(websocket)


# ── Webpage Events Endpoints ──────────────────────────────────────────────────

@app.get("/api/admin/webpage-events")
async def webpage_events_list(
    limit: int = Query(100),
    event_type: str = Query(None),
    web_token: str = Query(None),
):
    from admin.db import list_webpage_events
    return _clean(list_webpage_events(limit=limit, event_type=event_type, web_token=web_token))


@app.get("/api/admin/webpage-events/stats")
async def webpage_events_stats(days: int = Query(7)):
    from admin.db import webpage_events_stats
    return _clean(webpage_events_stats(days=days))


@app.get("/api/admin/webpage-events/active-users")
async def webpage_active_users():
    from admin.db import webpage_active_users_last_24h
    return _clean(webpage_active_users_last_24h())


@app.get("/api/admin/webpage-events/journey/{token}")
async def webpage_visitor_journey(token: str):
    from admin.db import webpage_visitor_journey
    return _clean(webpage_visitor_journey(token))
