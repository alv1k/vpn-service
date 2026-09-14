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
from pathlib import Path


def _get_active_sni(inbound_key: str = "1") -> str:
    """Возвращает текущий активный и проверенный SNI для конкретного инбаунда."""
    defaults = {
        "1": "tile.openstreetmap.org",
        "2": "www.speedtest.net",
        "14": "www.speedtest.net",
    }
    try:
        state_file = Path(__file__).resolve().parent.parent / "data" / "active_sni.json"
        if state_file.exists():
            with open(state_file, "r", encoding="utf-8") as sf:
                data = json.load(sf)
                return data.get(f"active_sni_{inbound_key}") or defaults.get(inbound_key, "tile.openstreetmap.org")
    except Exception:
        pass
    return defaults.get(inbound_key, "tile.openstreetmap.org")


def _get_active_reality_sni() -> str:
    return _get_active_sni("1")


def _vless_to_singbox_outbound(vless_link: str, remark: str) -> dict | None:
    """Конвертирует vless:// или hysteria2:// URI в outbound объект sing-box."""
    try:
        if '#' in vless_link:
            vless_link = vless_link.split('#')[0]
        parsed = urllib.parse.urlparse(vless_link)
        
        if parsed.scheme in ("hysteria2", "hy2"):
            user_host_port = parsed.netloc
            auth, host_port = user_host_port.split('@', 1) if '@' in user_host_port else ('', user_host_port)
            server, port_str = host_port.rsplit(':', 1) if ':' in host_port else (host_port, '443')
            params = dict(urllib.parse.parse_qsl(parsed.query))
            sni = params.get('sni', server)
            insecure = params.get('insecure', '0') == '1'
            return {
                "type": "hysteria2",
                "tag": remark or "Hysteria2",
                "server": server,
                "server_port": int(port_str),
                "password": auth,
                "tls": {
                    "enabled": True,
                    "server_name": sni,
                    "insecure": insecure,
                    "alpn": ["h3"]
                }
            }

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

    # Создаем группы отказоустойчивости и выбора
    proxy_groups = []
    if len(outbound_tags) > 1:
        auto_group_tag = "⚡ Автовыбор узла"
        manual_group_tag = "🎯 Выбор сервера вручную"
        auto_outbound = {
            "type": "urltest",
            "tag": auto_group_tag,
            "outbounds": list(outbound_tags),
            "url": "https://www.gstatic.com/generate_204",
            "interval": "2m",
            "tolerance": 100,
            "idle_timeout": "30m",
        }
        select_outbound = {
            "type": "selector",
            "tag": manual_group_tag,
            "outbounds": [auto_group_tag] + list(outbound_tags),
            "default": auto_group_tag,
        }
        proxy_groups.extend([select_outbound, auto_outbound])
        default_proxy = manual_group_tag
    else:
        default_proxy = outbound_tags[0]

    # Системные аутбаунды
    all_outbounds = proxy_groups + outbounds + [
        {"type": "direct", "tag": "direct"},
        {"type": "block", "tag": "block"},
        {"type": "dns", "tag": "dns-out"},
    ]

    return {
        "dns": {
            "servers": [
                {"tag": "dns-remote", "address": "8.8.8.8", "detour": default_proxy},
                {"tag": "dns-direct", "address": "77.88.8.8", "detour": "direct"},
            ],
            "rules": [
                {"geosite": ["category-ru", "category-gov-ru", "mailru", "vk"], "server": "dns-direct"},
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
        "outbounds": all_outbounds,
        "route": {
            "rules": [
                {"protocol": "dns", "outbound": "dns-out"},
                {"ip_is_private": True, "outbound": "direct"},
                {"geosite": ["category-ru", "category-gov-ru", "mailru", "vk"], "outbound": "direct"},
                {"geoip": ["ru"], "outbound": "direct"},
            ],
            "final": default_proxy,
            "auto_detect_interface": True,
        },
    }


def _build_headers(expires_at: datetime | None, is_json: bool = False) -> dict[str, str]:
    expire_ts = int(expires_at.timestamp()) if expires_at else 0
    now_dt = datetime.utcnow()
    
    if not expires_at:
        title = "🐿 TIIN"
    elif expires_at <= now_dt:
        title = "🐿 TIIN  ❌ Истекла"
    else:
        seconds_left = (expires_at - now_dt).total_seconds()
        days_left = int(seconds_left // 86400)
        hours_left = int(seconds_left // 3600)
        
        if days_left > 7:
            status_tag = f"✅ {days_left}д"
        elif days_left >= 1:
            status_tag = f"⚠️ {days_left}д"
        else:
            status_tag = f"⏳ Сегодня ({hours_left}ч)" if hours_left > 0 else "⏳ Заканчивается"
        
        title = f"🐿 TIIN  {status_tag}"

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

    sub_url = f"https://344988.snk.wtf/sub/{token}"
    happ_link = f"happ://add/{sub_url}"

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
  <code style="word-break:break-all;font-size:12px">{sub_url}</code></p>
</div>
<script>window.location.href="{happ_link}"</script>
</body></html>"""


@sub_router.get("/go-connect/{token}", response_class=HTMLResponse)
async def go_connect_smart_redirect(token: str, request: Request = None):
    """Универсальная страница быстрого подключения и импорта в 1 клик для всех ОС."""
    user = get_user_by_web_token(token)
    if not user:
        raise HTTPException(status_code=404, detail="Not found")

    key = _pick_vless_key(user)
    if not key:
        raise HTTPException(status_code=404, detail="No active subscription")

    ua = request.headers.get("user-agent", "") if request else ""
    platform, _, ver = detect_platform(ua)
    log_user_platform(
        tg_id=user.get("tg_id"), user_id=user.get("id"),
        platform=platform, client_app="SmartConnect", client_version=ver,
        user_agent=ua,
    )

    # Используем ссылку подписки из Личного кабинета (через наш прокси со всеми шлюзами и SNI)
    sub_url = f"https://344988.snk.wtf/sub/{token}"
    import base64
    b64_url = base64.urlsafe_b64encode(sub_url.encode()).decode().rstrip("=")
    
    happ_link = f"happ://add/{sub_url}"
    streisand_link = f"streisand://import/{sub_url}"
    v2rayng_link = f"v2rayng://install-config?url={urllib.parse.quote(sub_url, safe='')}"
    shadowrocket_link = f"shadowrocket://add/sub://{b64_url}"
    singbox_link = f"sing-box://import-remote-profile?url={urllib.parse.quote(sub_url, safe='')}#%F0%9F%90%BF%20TIIN%20VPN"
    cabinet_link = f"https://344988.snk.wtf/my/{token}"

    return f"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
<title>Подключение • тииҥ VPN 🐿</title>
<style>
  :root {{
    --bg: #0d0f17;
    --card-bg: rgba(26, 31, 46, 0.85);
    --border: rgba(255, 255, 255, 0.08);
    --accent: #6366f1;
    --accent-hover: #4f46e5;
    --text-main: #f8fafc;
    --text-muted: #94a3b8;
    --radius: 16px;
  }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }}
  body {{
    background: radial-gradient(circle at 50% 0%, #1e1e38 0%, var(--bg) 70%);
    color: var(--text-main);
    min-height: 100vh;
    display: flex;
    justify-content: center;
    align-items: center;
    padding: 20px 16px;
  }}
  .card {{
    background: var(--card-bg);
    backdrop-filter: blur(20px);
    -webkit-backdrop-filter: blur(20px);
    border: 1px solid var(--border);
    border-radius: 24px;
    padding: 32px 24px;
    width: 100%;
    max-width: 440px;
    box-shadow: 0 20px 40px rgba(0,0,0,0.5);
    text-align: center;
  }}
  .badge {{
    display: inline-block;
    padding: 4px 12px;
    background: rgba(99, 102, 241, 0.15);
    border: 1px solid rgba(99, 102, 241, 0.3);
    color: #a5b4fc;
    font-size: 12px;
    font-weight: 600;
    border-radius: 99px;
    margin-bottom: 16px;
    text-transform: uppercase;
    letter-spacing: 0.5px;
  }}
  h1 {{ font-size: 22px; font-weight: 700; margin-bottom: 8px; color: #fff; }}
  p.subtitle {{ font-size: 14px; color: var(--text-muted); margin-bottom: 24px; line-height: 1.5; }}
  .btn-group {{ display: flex; flex-direction: column; gap: 12px; }}
  .btn {{
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 10px;
    width: 100%;
    padding: 14px 18px;
    border-radius: var(--radius);
    text-decoration: none;
    font-size: 15px;
    font-weight: 600;
    transition: all 0.2s ease;
    cursor: pointer;
    border: none;
  }}
  .btn-primary {{
    background: linear-gradient(135deg, #6366f1 0%, #4f46e5 100%);
    color: #fff;
    box-shadow: 0 4px 14px rgba(99, 102, 241, 0.4);
  }}
  .btn-primary:active {{ transform: scale(0.98); opacity: 0.9; }}
  .btn-secondary {{
    background: rgba(255, 255, 255, 0.05);
    border: 1px solid var(--border);
    color: var(--text-main);
  }}
  .btn-secondary:active {{ background: rgba(255, 255, 255, 0.1); }}
  .divider {{
    display: flex;
    align-items: center;
    margin: 20px 0;
    color: var(--text-muted);
    font-size: 12px;
    text-transform: uppercase;
    letter-spacing: 0.5px;
  }}
  .divider::before, .divider::after {{ content: ''; flex: 1; height: 1px; background: var(--border); }}
  .divider span {{ padding: 0 10px; }}
  .sub-box {{
    background: rgba(0, 0, 0, 0.3);
    border: 1px dashed var(--border);
    border-radius: 12px;
    padding: 12px;
    margin-top: 14px;
    text-align: left;
  }}
  .sub-box-title {{ font-size: 11px; color: var(--text-muted); margin-bottom: 6px; }}
  .sub-url-row {{ display: flex; gap: 8px; align-items: center; }}
  .sub-url {{
    font-family: monospace;
    font-size: 11px;
    color: #cbd5e1;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    flex: 1;
    user-select: all;
  }}
  .btn-copy {{
    padding: 6px 12px;
    font-size: 12px;
    font-weight: 600;
    background: rgba(255, 255, 255, 0.1);
    color: #fff;
    border: 1px solid var(--border);
    border-radius: 8px;
    cursor: pointer;
  }}
  .footer-link {{
    margin-top: 20px;
    display: inline-block;
    color: #818cf8;
    text-decoration: none;
    font-size: 13px;
    font-weight: 500;
  }}
  .footer-link:hover {{ text-decoration: underline; }}
  .ios-only, .android-only, .pc-only {{ display: none; }}
</style>
</head>
<body>
<div class="card">
  <div class="badge">🚀 Быстрый старт в 1 клик</div>
  <h1>Подключение к тииҥ VPN</h1>
  <p class="subtitle">Нажмите кнопку установленного у вас приложения — настройки подтянутся автоматически:</p>

  <div class="btn-group">
    <!-- Кнопки приложений -->
    <a class="btn btn-primary" href="{happ_link}">
      <span>⚡</span> Открыть в Happ Proxy
    </a>
    
    <a class="btn btn-secondary" id="btn-streisand" href="{streisand_link}">
      <span>🛡️</span> Открыть в Streisand
    </a>
    
    <a class="btn btn-secondary" id="btn-v2rayng" href="{v2rayng_link}">
      <span>🤖</span> Открыть в v2rayNG
    </a>

    <a class="btn btn-secondary" id="btn-shadowrocket" href="{shadowrocket_link}">
      <span>🚀</span> Открыть в Shadowrocket
    </a>
  </div>

  <div class="divider"><span>или скопируйте ссылку</span></div>

  <div class="sub-box">
    <div class="sub-box-title">Прямая ссылка подписки:</div>
    <div class="sub-url-row">
      <div class="sub-url" id="subUrlText">{sub_url}</div>
      <button class="btn-copy" onclick="copySub()">Копировать</button>
    </div>
  </div>

  <div>
    <a class="footer-link" href="{cabinet_link}">📖 Открыть подробную инструкцию и QR-код</a>
  </div>
</div>

<script>
function copySub() {{
  const text = document.getElementById('subUrlText').innerText;
  navigator.clipboard.writeText(text).then(() => {{
    const btn = event.target;
    const old = btn.innerText;
    btn.innerText = 'Скопировано!';
    btn.style.background = '#10b981';
    btn.style.borderColor = '#10b981';
    setTimeout(() => {{
      btn.innerText = old;
      btn.style.background = '';
      btn.style.borderColor = '';
    }}, 2000);
  }}).catch(() => {{
    alert('Ссылка: ' + text);
  }});
}}

// Попытка авто-редиректа на Happ (если поддерживается)
window.addEventListener('load', () => {{
  const ua = navigator.userAgent.toLowerCase();
  const isIOS = /iphone|ipad|ipod/.test(ua);
  const isAndroid = /android/.test(ua);
}});
</script>
</body>
</html>"""



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

            # Исключаем нерабочие/отключенные узлы по запросу
            if "type=ws" in line or ":14715" in line:
                continue  # WebSocket • MUX
            elif ":48745" in line or ("type=xhttp" in line and "update.microsoft.com" in line):
                continue  # xHTTP • Armor-M1
            elif ":53151" in line or ("security=reality" in line and "amazon.com" in line):
                continue  # Reality • Standard

            if ":7443" in line:
                active_sni = _get_active_reality_sni()
                if "sni=" in base_link:
                    import re
                    base_link = re.sub(r'sni=[^&]+', f'sni={active_sni}', base_link)
                else:
                    base_link = f"{base_link}&sni={active_sni}"
                remark = "🇩🇪 👁️ Reality • Vision (TCP)"
            elif line.startswith("hysteria2://") or line.startswith("hysteria://"):
                if ":35443" in line:
                    remark = "🇩🇪 ⚔️ Hysteria2 • Armor-M1"
                elif ":4443" in line:
                    remark = "🇩🇪 🚀 Hysteria2 • Turbo"
                else:
                    remark = "🇩🇪 🚀 Hysteria2"
            elif line.startswith("tuic://"):
                remark = "🇩🇪 🌐 TUIC"
            elif line.startswith("ss://"):
                remark = "🇩🇪 🛡️ Shadowsocks"
            elif "type=grpc" in line:
                remark = "🇩🇪 🟣 VLESS • gRPC"
            elif ":8081" in line or "x_padding_bytes=100-1000" in line or "amd.com" in line:
                remark = "🇩🇪 🛡️ Anti-DPI • Enhanced"
            elif ":47447" in line or ("type=xhttp" in line and ("nvidia.com" in line or "samsung.com" in line)):
                active_sni_2 = _get_active_sni("2")
                import re
                if "sni=" in base_link:
                    base_link = re.sub(r'sni=[^&]+', f'sni={active_sni_2}', base_link)
                else:
                    base_link = f"{base_link}&sni={active_sni_2}"
                if "host=" in base_link:
                    base_link = re.sub(r'host=[^&]+', f'host={active_sni_2}', base_link)
                else:
                    base_link = f"{base_link}&host={active_sni_2}"
                # Сброс несовместимого flow и post-quantum encryption
                base_link = re.sub(r'flow=[^&]+&?', '', base_link)
                base_link = re.sub(r'encryption=[^&]+', 'encryption=none', base_link)
                base_link = base_link.rstrip('?&')
                remark = "🇩🇪 🌊 xHTTP • Stream"
            elif ":38745" in line or ("type=xhttp" in line and "microsoft.com" in line):
                active_sni_14 = _get_active_sni("14")
                import re
                if "sni=" in base_link:
                    base_link = re.sub(r'sni=[^&]+', f'sni={active_sni_14}', base_link)
                else:
                    base_link = f"{base_link}&sni={active_sni_14}"
                if "host=" in base_link:
                    base_link = re.sub(r'host=[^&]+', f'host={active_sni_14}', base_link)
                else:
                    base_link = f"{base_link}&host={active_sni_14}"
                base_link = re.sub(r'flow=[^&]+&?', '', base_link)
                base_link = re.sub(r'encryption=[^&]+', 'encryption=none', base_link)
                base_link = base_link.rstrip('?&')
                remark = "🇩🇪 💎 xHTTP • Ultra"
            elif ":28745" in line or ("type=xhttp" in line and "google.com" in line):
                remark = "🇩🇪 🟠 xHTTP • Standard"
            elif "type=xhttp" in line:
                remark = "🇩🇪 🟠 xHTTP"
            elif "security=reality" in line:
                remark = "🇩🇪 👁️ Reality"
            else:
                remark = "🇩🇪 🟢 TCP"

            new_lines.append(f"{base_link}#{remark}")
            vless_entries.append((base_link, remark))

        # Сортируем немецкие серверы по надежности: xHTTP Ultra -> Hysteria2 -> Reality
        def _server_sort_key(entry):
            _, remark = entry
            if "xHTTP • Ultra" in remark: return 1
            if "Hysteria2 • Armor" in remark: return 2
            if "Reality • Vision" in remark: return 3
            return 10
        vless_entries.sort(key=_server_sort_key)
        new_lines = [f"{link}#{remark}" for link, remark in vless_entries]

        # Добавляем Российский шлюз (СПб ➔ Германия) для всех пользователей
        client_uuid = key.get("client_id")
        if client_uuid:
            # Извлекаем точный auth/password для Hysteria из оригинальной подписки
            real_hy2_auth = None
            for v_link, _ in vless_entries:
                if v_link.startswith("hysteria2://") or v_link.startswith("hy2://"):
                    try:
                        import urllib.parse
                        p = urllib.parse.urlparse(v_link)
                        real_hy2_auth = p.netloc.split('@')[0]
                        if real_hy2_auth:
                            break
                    except Exception:
                        pass
            
            client_auth = real_hy2_auth or client_uuid.replace('-', '')
            ru_hy2_link = f"hysteria2://{client_auth}@ru-server.tiinservice.online:35443?alpn=h3&fp=chrome&security=tls&sni=ru-server.tiinservice.online"
            ru_hy2_remark = "🇷🇺 ⚡ Hysteria2 • Шлюз (РФ ➔ DE)"
            new_lines.insert(0, f"{ru_hy2_link}#{ru_hy2_remark}")
            vless_entries.insert(0, (ru_hy2_link, ru_hy2_remark))

            ru_vless_link = f"vless://{client_uuid}@ru-server.tiinservice.online:8443?type=ws&security=tls&sni=ru-server.tiinservice.online&path=%2Fapi%2Fv1%2Fws"
            ru_vless_remark = "🇷🇺 🛡️ XHTTP • Шлюз (РФ ➔ DE)"
            new_lines.insert(1, f"{ru_vless_link}#{ru_vless_remark}")
            vless_entries.insert(1, (ru_vless_link, ru_vless_remark))

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
