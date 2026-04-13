"""
Прокси-эндпоинт для VLESS-подписок: фетчит оригинал с XUI, переписывает remark
у каждой vless:// ссылки в человекочитаемый формат (`🐿 TIIN — осталось N дней`),
строит собственный subscription-userinfo заголовок из БД и кэширует ответ.

Приложение импортирует sub URL → обращается к нам → мы обращаемся к XUI → отдаём
приложению обработанный base64. XUI-шные заголовки игнорируем, свои — собираем.
"""
import asyncio
import base64
import logging
import time
from datetime import datetime
from urllib.parse import quote

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


def _days_left_text(expires_at: datetime | None) -> str:
    """Человекочитаемая строка 'осталось N дней' для remark."""
    if not expires_at:
        return "безлимит"
    delta = expires_at - datetime.utcnow()
    days = delta.days
    if days < 0:
        return "истекла"
    if days == 0:
        return "истекает сегодня"
    if days == 1:
        return "остался 1 день"
    mod10 = days % 10
    mod100 = days % 100
    if 2 <= mod10 <= 4 and not (12 <= mod100 <= 14):
        return f"осталось {days} дня"
    return f"осталось {days} дней"


def _build_remark(expires_at: datetime | None) -> str:
    return f"🐿 TIIN — {_days_left_text(expires_at)}"


def _rewrite_vless_remarks(raw_body: bytes, remark: str) -> bytes:
    """
    Декодирует base64-тело XUI, заменяет часть после `#` у каждой vless:// строки
    на переданный `remark`, возвращает снова base64-encoded bytes.
    """
    try:
        decoded = base64.b64decode(raw_body).decode("utf-8", errors="replace")
    except Exception as e:
        logger.warning(f"sub_proxy: base64 decode failed: {e}")
        return raw_body

    out_lines = []
    encoded_remark = quote(remark)
    for line in decoded.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("vless://") and "#" in line:
            base, _, _ = line.partition("#")
            out_lines.append(f"{base}#{encoded_remark}")
        elif line.startswith("vless://"):
            out_lines.append(f"{line}#{encoded_remark}")
        else:
            out_lines.append(line)

    joined = "\n".join(out_lines)
    return base64.b64encode(joined.encode("utf-8"))


def _build_headers(expires_at: datetime | None) -> dict[str, str]:
    expire_ts = int(expires_at.timestamp()) if expires_at else 0
    title_b64 = base64.b64encode(SUB_PROFILE_TITLE.encode("utf-8")).decode("ascii")
    return {
        "subscription-userinfo": f"upload=0; download=0; total=0; expire={expire_ts}",
        "profile-update-interval": str(SUB_UPDATE_INTERVAL_HOURS),
        "profile-title": f"base64:{title_b64}",
        "content-type": "text/plain; charset=utf-8",
    }


def _pick_vless_key(user: dict) -> dict | None:
    """
    Выбирает самый свежий VLESS-ключ с непустым `subscription_link`.
    Предпочитает активные (expires_at > now), иначе самый свежий истёкший.
    """
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
    async with httpx.AsyncClient(timeout=10, verify=False) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.content.strip()


@sub_router.get("/sub/{token}")
async def proxy_subscription(token: str):
    user = get_user_by_web_token(token)
    if not user:
        raise HTTPException(status_code=404, detail="Not found")

    key = _pick_vless_key(user)
    if not key:
        raise HTTPException(status_code=404, detail="No active subscription")

    xui_url = key["subscription_link"]
    expires_at = key.get("expires_at")
    remark = _build_remark(expires_at)

    now = time.time()
    async with _CACHE_LOCK:
        cached = _CACHE.get(token)
        if cached and (now - cached[0]) < SUB_CACHE_TTL:
            return Response(content=cached[1], headers=cached[2])

    try:
        raw = await _fetch_xui(xui_url)
    except Exception as e:
        logger.error(f"sub_proxy: XUI fetch failed for {token[:8]}…: {e}")
        # Fallback на устаревший кэш, если он есть
        cached = _CACHE.get(token)
        if cached:
            logger.info(f"sub_proxy: serving stale cache for {token[:8]}…")
            return Response(content=cached[1], headers=cached[2])
        raise HTTPException(status_code=503, detail="Upstream unavailable")

    body = _rewrite_vless_remarks(raw, remark)
    headers = _build_headers(expires_at)

    async with _CACHE_LOCK:
        _CACHE[token] = (now, body, headers)

    return Response(content=body, headers=headers)
