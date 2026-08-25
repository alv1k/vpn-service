"""
Прокси-эндпоинт для VLESS-подписок: фетчит оригинал с XUI и отдаёт как есть.
Никаких переписываний remark'ов и портов.
"""
import asyncio
import base64
import logging
import time
import warnings

warnings.filterwarnings("ignore", message="Unverified HTTPS request.*", category=Warning)
from datetime import datetime

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from api.db import (
    get_user_by_web_token,
    get_keys_by_tg_id,
    get_keys_by_user_id,
    log_user_platform,
)
from api.platform_detector import detect_platform

logger = logging.getLogger(__name__)
sub_router = APIRouter(); print("DEBUG: sub_router initialized")

SUB_CACHE_TTL = 60  # секунд
SUB_PROFILE_TITLE = "🐿 TIIN VPN"
SUB_UPDATE_INTERVAL_HOURS = 12

_CACHE: dict[str, tuple[float, bytes, dict[str, str]]] = {}
_CACHE_LOCK = asyncio.Lock()


import json
import urllib.parse


def _vless_to_singbox_outbound(vless_link: str, remark: str) -> dict | None:
    """Конвертирует vless:// URI в outbound объект sing-box."""
    try:
        if '#' in vless_link:
            vless_link = vless_link.split('#')[0]
        parsed = urllib.parse.urlparse(vless_link)
        if parsed.scheme != "vless":
            return None
        user_host_port = parsed.netloc
        if '@' not in user_host_port:
            return None
        uuid, host_port = user_host_port.split('@', 1)
        if ':' in host_port:
            server, port_str = host_port.rsplit(':', 1)
            port = int(port_str)
        else:
            server = host_port
            port = 443

        params = dict(urllib.parse.parse_qsl(parsed.query))
        sec = params.get('security', 'none')
        transport_type = params.get('type', 'tcp')

        outbound = {
            "type": "vless",
            "tag": remark or "VPN",
            "server": server,
            "server_port": port,
            "uuid": uuid,
            "flow": params.get("flow", ""),
        }

        if sec in ("reality", "tls"):
            tls_cfg = {
                "enabled": True,
                "server_name": params.get("sni", ""),
                "utls": {
                    "enabled": True,
                    "fingerprint": params.get("fp", "chrome"),
                },
            }
            if sec == "reality":
                tls_cfg["reality"] = {
                    "enabled": True,
                    "public_key": params.get("pbk", ""),
                    "short_id": params.get("sid", ""),
                }
            outbound["tls"] = tls_cfg

        if transport_type == "xhttp":
            outbound["transport"] = {
                "type": "xhttp",
                "host": params.get("host", ""),
                "path": params.get("path", "/"),
            }
        elif transport_type == "ws":
            outbound["transport"] = {
                "type": "ws",
                "path": params.get("path", "/"),
                "headers": {"Host": params.get("host", "")} if params.get("host") else {},
            }

        return outbound
    except Exception as ex:
        logger.warning(f"Failed to parse vless link for singbox: {ex}")
        return None


def _build_singbox_config(vless_lines: list[tuple[str, str]]) -> dict:
    """Генерирует полновластный sing-box JSON с зашитой маршрутизацией обхода РФ."""
    outbounds = []
    outbound_tags = []

    for link, remark in vless_lines:
        out = _vless_to_singbox_outbound(link, remark)
        if out:
            outbounds.append(out)
            outbound_tags.append(out["tag"])

    if not outbounds:
        outbounds.append({"type": "direct", "tag": "direct"})
        outbound_tags.append("direct")

    default_proxy = outbound_tags[0]

    # Системные аутбаунды
    outbounds.append({"type": "direct", "tag": "direct"})
    outbounds.append({"type": "block", "tag": "block"})
    outbounds.append({"type": "dns", "tag": "dns-out"})

    return {
        "dns": {
            "servers": [
                {"tag": "dns-remote", "address": "8.8.8.8", "detour": default_proxy},
                {"tag": "dns-direct", "address": "77.88.8.1", "detour": "direct"},
            ],
            "rules": [
                {"geosite": ["ru", "category-gov-ru"], "server": "dns-direct"},
                {"geoip": ["ru"], "server": "dns-direct"},
            ],
            "final": "dns-remote",
        },
        "inbounds": [
            {
                "type": "tun",
                "tag": "tun-in",
                "inet4_address": "172.19.0.1/30",
                "auto_route": True,
                "strict_route": True,
                "sniff": True,
            }
        ],
        "outbounds": outbounds,
        "route": {
            "rules": [
                {"protocol": "dns", "outbound": "dns-out"},
                {"ip_is_private": True, "outbound": "direct"},
                {"geosite": ["ru", "category-gov-ru", "yandex"], "outbound": "direct"},
                {"geoip": ["ru"], "outbound": "direct"},
                {"domain_suffix": [".ru", ".xn--p1ai", ".su"], "outbound": "direct"},
            ],
            "final": default_proxy,
            "auto_detect_interface": True,
        },
    }


def _build_headers(expires_at: datetime | None, is_json: bool = False) -> dict[str, str]:
    expire_ts = int(expires_at.timestamp()) if expires_at else 0
    now_dt = datetime.utcnow()
    days_left = (expires_at - now_dt).days if expires_at and expires_at > now_dt else 0
    
    if days_left > 7:
        status_emoji = "✅"
    elif days_left >= 1:
        status_emoji = "⚠️"
    else:
        status_emoji = "❌"

    if expires_at:
        title = f"🐿 TIIN  {status_emoji} {days_left}д"
    else:
        title = "🐿 TIIN"

    title_b64 = base64.b64encode(title.encode("utf-8")).decode("ascii")
    content_type = "application/json; charset=utf-8" if is_json else "text/plain; charset=utf-8"
    return {
        "subscription-userinfo": f"upload=0; download=0; total=0; expire={expire_ts}",
        "profile-update-interval": str(SUB_UPDATE_INTERVAL_HOURS),
        "profile-title": f"base64:{title_b64}",
        "content-type": content_type,
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


class XUIError(Exception):
    def __init__(self, status_code: int):
        self.status_code = status_code
        super().__init__(f"XUI returned {status_code}")


async def _fetch_xui(url: str) -> bytes:
    """Получает подписку из XUI."""
    async with httpx.AsyncClient(timeout=10, verify=False) as client:
        resp = await client.get(url)
        if resp.status_code in (400, 403, 404):
            raise XUIError(resp.status_code)
        resp.raise_for_status()
        return resp.content


@sub_router.get("/go-happ/{token}", response_class=HTMLResponse)
async def go_happ_redirect(token: str, request: Request = None):
    """Редирект на happ:// deep link для Telegram кнопок."""
    user = get_user_by_web_token(token)
    if not user:
        raise HTTPException(status_code=404, detail="Not found")

    key = _pick_vless_key(user)
    if not key:
        raise HTTPException(status_code=404, detail="No active subscription")

    ua = request.headers.get("user-agent") if request else None
    platform, _, ver = detect_platform(ua)
    log_user_platform(
        tg_id=user.get("tg_id"), user_id=user.get("id"),
        platform=platform, client_app='Happ', client_version=ver,
        user_agent=ua,
    )

    xui_url = key["subscription_link"]
    happ_link = f"happ://add/{xui_url}"

    return f"""<!DOCTYPE html>
<html><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Открыть в Happ</title>
<style>
  body{{font-family:-apple-system,system-ui,sans-serif;display:flex;justify-content:center;
        align-items:center;min-height:100vh;margin:0;background:#0a0a0a;color:#fff;text-align:center}}
  .c{{padding:2rem}}
  .btn{{display:inline-block;padding:16px 32px;background:#007aff;color:white;
        border-radius:12px;text-decoration:none;font-size:18px;margin-top:1rem}}
  .note{{color:#888;font-size:14px;margin-top:1rem}}
</style>
</head><body>
<div class="c">
  <h2>Открыть в Happ</h2>
  <p>Нажмите кнопку, чтобы открыть подписку в приложении:</p>
  <a class="btn" href="{happ_link}">⚡ Открыть в Happ</a>
  <p class="note">Если кнопка не сработала — скопируйте ссылку подписки в Happ вручную:<br>
  <code style="word-break:break-all;font-size:12px">{xui_url}</code></p>
</div>
<script>window.location.href="{happ_link}"</script>
</body></html>"""


@sub_router.get("/go-shadowrocket/{token}", response_class=HTMLResponse)
async def go_shadowrocket_redirect(token: str, request: Request = None):
    """Redirect to shadowrocket:// deep link for Telegram buttons."""
    user = get_user_by_web_token(token)
    if not user:
        raise HTTPException(status_code=404, detail="Not found")

    key = _pick_vless_key(user)
    if not key:
        raise HTTPException(status_code=404, detail="No active subscription")

    ua = request.headers.get("user-agent") if request else None
    platform, _, ver = detect_platform(ua)
    if platform == 'unknown':
        platform = 'ios'
    log_user_platform(
        tg_id=user.get("tg_id"), user_id=user.get("id"),
        platform=platform, client_app='Shadowrocket', client_version=ver,
        user_agent=ua,
    )

    xui_url = key["subscription_link"]
    import base64
    b64_url = base64.urlsafe_b64encode(xui_url.encode()).decode().rstrip("=")
    shadowrocket_link = f"shadowrocket://add/sub://{b64_url}"

    return f"""<!DOCTYPE html>
<html><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Открыть в Shadowrocket</title>
<style>
  body{{font-family:-apple-system,system-ui,sans-serif;display:flex;justify-content:center;
        align-items:center;min-height:100vh;margin:0;background:#0a0a0a;color:#fff;text-align:center}}
  .c{{padding:2rem}}
  .btn{{display:inline-block;padding:16px 32px;background:#007aff;color:white;
        border-radius:12px;text-decoration:none;font-size:18px;margin-top:1rem}}
  .note{{color:#888;font-size:14px;margin-top:1rem}}
</style>
</head><body>
<div class="c">
  <h2>Открыть в Shadowrocket</h2>
  <p>Нажмите кнопку, чтобы открыть подписку в приложении:</p>
  <a class="btn" href="{shadowrocket_link}">🚀 Открыть в Shadowrocket</a>
  <p class="note">Если кнопка не сработала — скопируйте ссылку подписки в Shadowrocket вручную:<br>
  <code style="word-break:break-all;font-size:12px">{xui_url}</code></p>
</div>
<script>window.location.href="{shadowrocket_link}"</script>
</body></html>"""


@sub_router.get("/sub/{token}")
async def proxy_subscription(token: str, request: Request = None):
    """Эндпоинт подписки – проксирует ответ от XUI и склеивает с Hysteria."""
    user = get_user_by_web_token(token)
    if not user:
        raise HTTPException(status_code=404, detail="Not found")

    key = _pick_vless_key(user)
    if not key:
        raise HTTPException(status_code=404, detail="No active subscription")

    ua = request.headers.get("user-agent", "") if request else ""
    platform, app, ver = detect_platform(ua)
    if platform != 'unknown' or app:
        log_user_platform(
            tg_id=user.get("tg_id"), user_id=user.get("id"),
            platform=platform, client_app=app, client_version=ver,
            user_agent=ua,
            ip=request.client.host if request and request.client else None,
        )

    xui_url = key["subscription_link"]
    expires_at = key.get("expires_at")

    # Check if subscription is expired
    now_dt = datetime.utcnow()
    is_expired = expires_at and expires_at <= now_dt

    # Detect VPN client (no text/html in Accept header)
    accept = request.headers.get("accept", "") if request else ""
    is_browser = "text/html" in accept

    # Detect if client requests sing-box / clash format
    wants_singbox = any(k in ua.lower() for k in ["sing-box", "hiddify", "karing", "clash"])

    # If expired and VPN client → return expired message
    if is_expired and not is_browser:
        renewal_url = f"https://344988.snk.wtf/my/{token}"
        expired_vless = f"vless://dummy@expired:443?type=tcp#❌ Подписка истекла — продлите: {renewal_url}"
        raw_body = base64.b64encode(expired_vless.encode())
        
        headers = _build_headers(expires_at)
        async with _CACHE_LOCK:
            _CACHE[token] = (time.time(), raw_body, headers)
        return Response(content=raw_body, headers=headers)

    headers = _build_headers(expires_at, is_json=wants_singbox)
    now = time.time()
    cache_key = f"{token}_{'json' if wants_singbox else 'b64'}"
    async with _CACHE_LOCK:
        cached = _CACHE.get(cache_key)
        if cached:
            ts = cached[0].timestamp() if isinstance(cached[0], datetime) else cached[0]
            if (now - ts) < SUB_CACHE_TTL:
                return Response(content=cached[1], headers=cached[2])

    try:
        raw_body = await _fetch_xui(xui_url)

        try:
            decoded_sub = base64.b64decode(raw_body).decode('utf-8')
        except:
            decoded_sub = raw_body.decode('utf-8')
        
        # Переписываем remark'и
        now_dt = datetime.utcnow()
        days_left = (expires_at - now_dt).days if expires_at and expires_at > now_dt else 0

        if days_left > 7:
            status_emoji = "✅"
        elif days_left >= 1:
            status_emoji = "⚠️"
        else:
            status_emoji = "❌"

        vless_entries = []
        new_lines = []
        for line in decoded_sub.splitlines():
            if not line.strip():
                continue
            if '#' in line:
                parts = line.split('#')
                base_link = '#'.join(parts[:-1])
            else:
                base_link = line

            if line.startswith("hysteria2://") or line.startswith("hysteria://"):
                if ":35443" in line:
                    remark = "🚀 Hysteria2 • Armor-M1"
                elif ":4443" in line:
                    remark = "🚀 Hysteria2 • Turbo"
                else:
                    remark = "🚀 Hysteria2"
            elif line.startswith("tuic://"):
                remark = "🌐 TUIC"
            elif line.startswith("ss://"):
                remark = "🛡 Shadowsocks"
            elif "type=grpc" in line:
                remark = "🟣 VLESS • gRPC"
            elif ":8081" in line or "x_padding_bytes=100-1000" in line or "amd.com" in line:
                remark = "🛡 Anti-DPI • Enhanced"
            elif ":48745" in line or ("type=xhttp" in line and "update.microsoft.com" in line):
                remark = "🛡️ xHTTP • Armor-M1"
            elif ":38745" in line or ("type=xhttp" in line and "microsoft.com" in line):
                remark = "🛡️ xHTTP • Ultra"
            elif ":47447" in line or ("type=xhttp" in line and ("nvidia.com" in line or "samsung.com" in line)):
                remark = "🟠 xHTTP • Stream"
            elif ":28745" in line or ("type=xhttp" in line and "google.com" in line):
                remark = "🟠 xHTTP • Standard"
            elif "type=xhttp" in line:
                remark = "🟠 xHTTP"
            elif "type=ws" in line or ":14715" in line:
                remark = "⚡ WebSocket • MUX"
                if "mux=" not in base_link:
                    join_char = "&" if "?" in base_link else "?"
                    base_link = f"{base_link}{join_char}mux=8"
            elif ":53151" in line or ("security=reality" in line and "amazon.com" in line):
                remark = "🟢 Reality • Standard"
            elif ":7443" in line or ("security=reality" in line and "apple.com" in line):
                remark = "🟢 Reality • Main"
            elif "security=reality" in line:
                remark = "🟢 Reality"
            else:
                remark = "🟢 TCP"

            new_lines.append(f"{base_link}#{remark}")
            vless_entries.append((base_link, remark))

        if wants_singbox:
            singbox_cfg = _build_singbox_config(vless_entries)
            raw_body = json.dumps(singbox_cfg, ensure_ascii=False, indent=2).encode('utf-8')
        else:
            decoded_sub = "\n".join(new_lines)
            raw_body = base64.b64encode(decoded_sub.encode('utf-8'))

        # Store in cache
        async with _CACHE_LOCK:
            _CACHE[cache_key] = (now, raw_body, headers)

    except XUIError as e:
        logger.warning(f"sub_proxy: XUI client not found for {token[:8]}…: HTTP {e.status_code}")
        async with _CACHE_LOCK:
            cached = _CACHE.get(cache_key)
            if cached:
                return Response(content=cached[1], headers=cached[2])
        raise HTTPException(status_code=410, detail="Subscription expired or removed")
    except Exception as e:
        logger.error(f"sub_proxy: XUI fetch failed for {token[:8]}…: {e}")
        async with _CACHE_LOCK:
            cached = _CACHE.get(cache_key)
            if cached:
                return Response(content=cached[1], headers=cached[2])
        raise HTTPException(status_code=503, detail="Upstream unavailable")

    return Response(content=raw_body, headers=headers)
