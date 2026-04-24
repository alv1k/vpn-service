"""
Прокси-эндпоинт для VLESS-подписок: фетчит оригинал с XUI и отдаёт как есть.
Никаких переписываний remark'ов и портов.
"""
import asyncio
import base64
import logging
import time
from datetime import datetime

import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from api.db import (
    get_user_by_web_token,
    get_keys_by_tg_id,
    get_keys_by_user_id,
)

logger = logging.getLogger(__name__)
sub_router = APIRouter()

SUB_CACHE_TTL = 60  # секунд
SUB_PROFILE_TITLE = "🐿 TIIN VPN"
SUB_UPDATE_INTERVAL_HOURS = 12

_CACHE: dict[str, tuple[float, bytes, dict[str, str]]] = {}
_CACHE_LOCK = asyncio.Lock()


def _build_headers(expires_at: datetime | None) -> dict[str, str]:
    expire_ts = int(expires_at.timestamp()) if expires_at else 0
    title_b64 = base64.b64encode(SUB_PROFILE_TITLE.encode("utf-8")).decode("ascii")
    return {
        "subscription-userinfo": f"upload=0; download=0; total=0; expire={expire_ts}",
        "profile-update-interval": str(SUB_UPDATE_INTERVAL_HOURS),
        "profile-title": f"base64:{title_b64}",
        "content-type": "text/plain; charset=utf-8",
        "Cache-Control": "private, max-age=60",
    }


def _pick_vless_key(user: dict) -> dict | None:
    """Выбирает VLESS-ключ с непустым subscription_link."""
    tg_id = user.get("tg_id")
    keys = get_keys_by_tg_id(tg_id) if tg_id else []
    if not keys:
        keys = get_keys_by_user_id(user["id"])

    vless_keys = [
        k for k in keys
        if k.get("vpn_type") == "vless" and k.get("subscription_link")
    ]
    if not vless_keys:
        return None

    now = datetime.utcnow()
    active = [k for k in vless_keys if k.get("expires_at") and k["expires_at"] > now]
    pool = active or vless_keys
    return max(pool, key=lambda k: k.get("expires_at") or datetime.min)


async def _fetch_xui(url: str) -> bytes:
    """Получает подписку из XUI."""
    async with httpx.AsyncClient(timeout=10, verify=False) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.content


@sub_router.get("/sub/{token}")
async def proxy_subscription(token: str):
    """Эндпоинт подписки – просто проксируем ответ от XUI."""
    user = get_user_by_web_token(token)
    if not user:
        raise HTTPException(status_code=404, detail="Not found")

    key = _pick_vless_key(user)
    if not key:
        raise HTTPException(status_code=404, detail="No active subscription")

    xui_url = key["subscription_link"]
    expires_at = key.get("expires_at")

    now = time.time()
    async with _CACHE_LOCK:
        cached = _CACHE.get(token)
        if cached and (now - cached[0]) < SUB_CACHE_TTL:
            logger.debug(f"sub_proxy: serving cached for {token[:8]}…")
            return Response(content=cached[1], headers=cached[2])

    try:
        raw_body = await _fetch_xui(xui_url)
    except Exception as e:
        logger.error(f"sub_proxy: XUI fetch failed for {token[:8]}…: {e}")
        cached = _CACHE.get(token)
        if cached:
            logger.info(f"sub_proxy: serving stale cache for {token[:8]}…")
            return Response(content=cached[1], headers=cached[2])
        raise HTTPException(status_code=503, detail="Upstream unavailable")

    headers = _build_headers(expires_at)

    async with _CACHE_LOCK:
        _CACHE[token] = (now, raw_body, headers)

    return Response(content=raw_body, headers=headers)