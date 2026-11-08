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
    get_user_by_id,
    get_user_by_tg_id,
    get_keys_by_tg_id,
    get_keys_by_user_id,
    execute_query,
    log_user_platform,
)
from api.platform_detector import detect_platform
from api.web_portal import WebEventRequest, _log_webpage_event, _happ_routing_deeplink
from config import PORTAL_BASE_URL, SUB_BASE_URL, ADMIN_TG_ID
RU_SUB_BASE_URL = "https://ru-server.tiinservice.online"


def _get_sub_base_for_user(user: dict | None) -> str:
    """Базовый URL подписки (направлен напрямую на Selectel SPB для бесперебойного обновления на МТС)."""
    return PORTAL_BASE_URL

logger = logging.getLogger(__name__)
sub_router = APIRouter(); print("DEBUG: sub_router initialized")

SUB_CACHE_TTL = 60  # секунд
SUB_PROFILE_TITLE = "🐿 TIIN VPN"
SUB_UPDATE_INTERVAL_HOURS = 12

_CACHE: dict[str, tuple[float, bytes, dict[str, str]]] = {}
_CACHE_LOCK = asyncio.Lock()

_RETRY_TRACKER: dict[str, list[float]] = {}
_LAST_BURST_ALERT: dict[str, float] = {}


def _check_retry_burst(key_id: str, client_ip: str, ua: str, tg_id: int | None = None):
    now = time.time()
    times = _RETRY_TRACKER.setdefault(key_id, [])
    times.append(now)
    _RETRY_TRACKER[key_id] = [t for t in times if now - t <= 60]
    count = len(_RETRY_TRACKER[key_id])
    if count >= 6:
        last_alert = _LAST_BURST_ALERT.get(key_id, 0)
        if now - last_alert > 300:
            _LAST_BURST_ALERT[key_id] = now
            platform, app, _ = detect_platform(ua)
            app_str = f"{app} ({platform})" if app else (ua[:40] if ua else "Unknown")
            logger.warning(
                f"[SUB_RETRY_BURST] tg_id={tg_id or 0} ip={client_ip} app={app_str} count={count}/60s"
            )


import json
import json as json_mod
import urllib.parse
from pathlib import Path


def _get_active_sni(inbound_key: str = "1") -> str:
    """Возвращает текущий активный и проверенный SNI для конкретного инбаунда."""
    defaults = {
        "1": "tile.openstreetmap.org",
        "2": "www.speedtest.net",
        "10": "www.amazon.com",
        "14": "www.speedtest.net",
        "15": "swscan.apple.com",
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
        yt_group_tag = "🔴 YouTube & Видео (Безлимит DE)"

        # Отбираем скоростные европейские узлы для видеостриминга (без расхода RU Selectel)
        eu_tags = [t for t in outbound_tags if not any(x in t for x in ('🇷🇺', 'Шлюз', 'RU', 'ru-server', 'Selectel', 'Доступ в РФ'))]
        ru_tags = [t for t in outbound_tags if any(x in t for x in ('🇷🇺', 'Шлюз', 'RU', 'ru-server', 'Selectel', 'Доступ в РФ'))]
        ru_proxy = ru_tags[0] if ru_tags else None
        yt_preferred = [t for t in eu_tags if any(k in t for k in ('Turbo', 'Hysteria', 'Vision'))] or eu_tags or outbound_tags
        yt_fallback_chain = list(yt_preferred) + [t for t in outbound_tags if t not in yt_preferred]

        auto_outbound = {
            "type": "urltest",
            "tag": auto_group_tag,
            "outbounds": list(outbound_tags),
            "url": "https://www.gstatic.com/generate_204",
            "interval": "2m",
            "tolerance": 100,
            "idle_timeout": "30m",
        }
        yt_outbound = {
            "type": "urltest",
            "tag": yt_group_tag,
            "outbounds": yt_fallback_chain,
            "url": "https://www.gstatic.com/generate_204",
            "interval": "3m",
            "tolerance": 80,
            "idle_timeout": "30m",
        }
        select_outbound = {
            "type": "selector",
            "tag": manual_group_tag,
            "outbounds": [auto_group_tag, yt_group_tag] + list(outbound_tags),
            "default": auto_group_tag,
        }
        proxy_groups.extend([select_outbound, auto_outbound, yt_outbound])
        default_proxy = manual_group_tag
    else:
        default_proxy = outbound_tags[0]
        yt_group_tag = default_proxy

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
                {"ip_is_private": True, "server": "dns-direct"},
                {
                    "domain": [
                        "344988.snk.wtf",
                        "ru-server.tiinservice.online",
                    ],
                    "domain_suffix": [
                        ".ru", ".su", ".рф",
                        "snk.wtf", "tiinservice.online",
                        "2gis.com", "vk.com", "tinkoff.com", "sberbank.com",
                        "playerok.com", "aviasales.ru", "lamoda.ru",
                        "megamarket.tech", "x5.tech"
                    ],
                    "server": "dns-direct"
                },
            ],
            "hosts": {
                "344988.snk.wtf": ["91.132.161.112"],
                "ru-server.tiinservice.online": ["139.100.207.18"]
            },
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
                # Мгновенный 0мс фоллбэк на TCP для устранения таймаутов QUIC в браузерах
                {"network": "udp", "port": [443], "outbound": "block"},
                {
                    "domain": [
                        "344988.snk.wtf",
                        "ru-server.tiinservice.online",
                    ],
                    "domain_suffix": [
                        "snk.wtf",
                        "tiinservice.online",
                    ],
                    "outbound": "direct",
                },
                {
                    "domain_suffix": [
                        "ooni.org",
                        "ooni.torproject.org",
                        "ooni.nu",
                        "measurementlab.net",
                        "measurement-lab.org",
                        "cmu.ssop.gov.ru",
                        "cmu.gov.ru",
                        "eais.rkn.gov.ru",
                        "blocklist.rkn.gov.ru",
                        "re-filter.com",
                        "zapret-info.org",
                    ],
                    "outbound": "block",
                },
                {
                    "domain_suffix": [
                        "googlevideo.com",
                        "youtube.com",
                        "ytimg.com",
                        "youtu.be",
                        "youtubei.googleapis.com",
                        "yt3.ggpht.com",
                        "nhacmp3youtube.com",
                        "twitch.tv",
                        "ttvnw.net",
                        "jtvnw.net",
                        "ext-twitch.tv",
                        "netflix.com",
                        "nflxvideo.net",
                        "nflxext.com",
                        "nflximg.net",
                        "tiktok.com",
                        "byteoversea.com",
                        "ibytedtos.com",
                        "musical.ly",
                        "kick.com",
                        "vimeo.com",
                        "vimeocdn.com",
                        "disneyplus.com",
                        "hulu.com",
                        "max.com",
                        "hbomax.com",
                        "primevideo.com",
                        "pv-cdn.net",
                        "tv.apple.com"
                    ],
                    "domain_keyword": [
                        "googlevideo"
                    ],
                    "outbound": yt_group_tag
                },
                {
                    "domain_suffix": [
                        ".ru", ".su", ".рф",
                        "2gis.com", "2gis.ru",
                        "ozon.ru", "wildberries.ru", "avito.ru", "megamarket.ru",
                        "gosuslugi.ru", "esia.gosuslugi.ru", "gu-st.ru", "pos.gosuslugi.ru",
                        "nalog.ru", "nalog.gov.ru", "cbr.ru", "nspk.ru", "mir-pay.ru", "sbp.nspk.ru",
                        "tbank.ru", "t-bank.ru", "tinkoff.ru", "tbank-online.ru", "t-invest.ru", "tinkoff.com",
                        "sberbank.ru", "sber.ru", "sberbank.com", "sbrf.ru", "domclick.ru",
                        "vtb.ru", "alfabank.ru", "alfa-bank.ru", "gazprombank.ru", "sovcombank.ru",
                        "rshb.ru", "raiffeisen.ru", "psbank.ru", "open.ru", "rosbank.ru", "mkb.ru", "pochtabank.ru",
                        "2ip.ru", "yandex.ru", "ya.ru", "yandex.net", "vk.com", "vk.ru",
                        "mail.ru", "dzen.ru", "kinopoisk.ru", "mos.ru", "e-yakutia.ru", "sakha.gov.ru", "sakha.ru",
                        "rutube.ru", "rutubelist.ru", "rtbcdn.ru", "fe.rutube.ru",
                        "okko.tv", "okko.sport", "ivi.ru", "kion.ru", "premier.one",
                        "amediateka.ru", "smotrim.ru", "vgtrk.ru", "wink.ru", "start.ru",
                        "vigo.tech", "tns-counter.ru", "yappy.media",
                        "playerok.com", "aviasales.ru", "lamoda.ru",
                        "megamarket.tech", "x5.tech", "lenta.com", "max.ru"
                    ],
                    "outbound": "direct"
                },
                {
                    "ip_cidr": [
                        "5.45.192.0/18",
                        "5.255.192.0/18",
                        "77.88.0.0/18",
                        "87.250.224.0/19",
                        "93.158.128.0/18",
                        "95.108.128.0/17",
                        "213.180.192.0/19",
                        "178.248.224.0/20",
                        "87.240.128.0/18",
                        "93.186.224.0/20",
                        "95.142.192.0/20",
                        "109.207.0.0/16"
                    ],
                    "outbound": "direct"
                }
            ],
            "final": default_proxy,
        },
    }


def _build_shadowrocket_conf() -> str:
    """Генерирует готовый профиль конфигурации маршрутизации для Shadowrocket."""
    return """# TIIN VPN — Smart Routing Rules for Shadowrocket
# Domain: 344988.snk.wtf
# Updated: 2026-10-28

[General]
bypass-system = true
skip-proxy = 127.0.0.1, 192.168.0.0/16, 10.0.0.0/8, 172.16.0.0/12, 100.64.0.0/10, localhost, *.local
bypass-tun = 10.0.0.0/8, 100.64.0.0/10, 127.0.0.0/8, 169.254.0.0/16, 172.16.0.0/12, 192.0.0.0/24, 192.0.2.0/24, 192.88.99.0/24, 192.168.0.0/16, 198.18.0.0/15, 198.51.100.0/24, 203.0.113.0/24, 224.0.0.0/4, 255.255.255.255/32
dns-server = system, 77.88.8.8, 1.1.1.1
fallback-dns-server = system
ipv6 = false
prefer-ipv6 = false
dns-direct-system = false
icmp-auto-reply = true
always-real-ip = *.apple.com, *.icloud.com, *.live.com, *.msftncsi.com

[Rule]
# Блокировка трекеров РКН и систем ТСПУ
DOMAIN-KEYWORD,cmu.gov.ru,REJECT
DOMAIN-KEYWORD,cmu.ssop.gov.ru,REJECT
DOMAIN-KEYWORD,eais.rkn.gov.ru,REJECT
DOMAIN-KEYWORD,blocklist.rkn.gov.ru,REJECT
DOMAIN-KEYWORD,re-filter.com,REJECT

# Локальные адреса
IP-CIDR,127.0.0.0/8,DIRECT
IP-CIDR,10.0.0.0/8,DIRECT
IP-CIDR,172.16.0.0/12,DIRECT
IP-CIDR,192.168.0.0/16,DIRECT

# Российские сервисы, банки, госуслуги напрямую
DOMAIN-SUFFIX,ru,DIRECT
DOMAIN-SUFFIX,su,DIRECT
DOMAIN-SUFFIX,xn--p1ai,DIRECT
DOMAIN-SUFFIX,2gis.com,DIRECT
DOMAIN-SUFFIX,2gis.ru,DIRECT
DOMAIN-SUFFIX,sakha.gov.ru,DIRECT
DOMAIN-SUFFIX,e-yakutia.ru,DIRECT
DOMAIN-SUFFIX,gosuslugi.ru,DIRECT
DOMAIN-SUFFIX,sberbank.ru,DIRECT
DOMAIN-SUFFIX,tbank.ru,DIRECT
DOMAIN-SUFFIX,tinkoff.ru,DIRECT
DOMAIN-SUFFIX,vtb.ru,DIRECT
DOMAIN-SUFFIX,alfabank.ru,DIRECT
DOMAIN-SUFFIX,ozon.ru,DIRECT
DOMAIN-SUFFIX,wildberries.ru,DIRECT
DOMAIN-SUFFIX,avito.ru,DIRECT
DOMAIN-SUFFIX,kinopoisk.ru,DIRECT
DOMAIN-SUFFIX,rutube.ru,DIRECT
GEOIP,RU,DIRECT

# Зарубежные сервисы через прокси
FINAL,PROXY
"""


@sub_router.get("/rules/shadowrocket.conf")
async def shadowrocket_rules():
    """Отдаёт готовый конфигурационный файл правил для Shadowrocket."""
    conf = _build_shadowrocket_conf()
    return Response(
        content=conf,
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": 'inline; filename="tiin_shadowrocket.conf"'},
    )


def _build_headers(expires_at: datetime | None, is_json: bool = False, routing_link: str | None = None) -> dict[str, str]:
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
    headers = {
        "subscription-userinfo": f"upload=0; download=0; total=0; expire={expire_ts}",
        "profile-update-interval": str(SUB_UPDATE_INTERVAL_HOURS),
        "profile-title": f"base64:{title_b64}",
        "content-type": content_type,
        "Cache-Control": "private, max-age=60",
    }
    if routing_link:
        headers["routing"] = routing_link
        headers["routing-enable"] = "true"
    return headers


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

    # Сортируем:
    # 1. Срок действия (expires_at)
    # 2. Приоритет правильным именам: tiin_{tg_id} или tiin_web_{user_id}
    # 3. ID записи (самый свежий ключ)
    pref_names = set()
    if tg_id:
        pref_names.add(f"tiin_{tg_id}")
    if user.get("id"):
        pref_names.add(f"tiin_web_{user['id']}")

    def _sort_key(k: dict):
        exp = k.get("expires_at") or datetime.min
        name_prio = 1 if k.get("client_name") in pref_names else 0
        kid = k.get("id") or 0
        return (exp, name_prio, kid)

    return max(pool, key=_sort_key)


class XUIError(Exception):
    def __init__(self, status_code: int):
        self.status_code = status_code
        super().__init__(f"XUI returned {status_code}")


async def _fetch_xui(url: str) -> bytes:
    """Получает подписку из XUI."""
    if ":2096/sub/" in url:
        url = url.replace(":2096/sub/", ":2096/updates_ui/")
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

    sub_base = _get_sub_base_for_user(user)
    sub_url = f"{sub_base}/sub/{token}"
    de_sub_url = f"https://344988.snk.wtf/sub/{token}"
    ru_sub_url = f"https://ru-server.tiinservice.online/sub/{token}"
    happ_link = f"happ://add/{sub_url}"
    happ_link_de = f"happ://add/{de_sub_url}"
    happ_link_ru = f"happ://add/{ru_sub_url}"
    routing_link = _happ_routing_deeplink()
    is_admin = bool(user and user.get("tg_id") == ADMIN_TG_ID)

    if is_admin:
        return f"""<!DOCTYPE html>
<html><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Happ • Тест маршрутизации (Admin)</title>
<style>
  body{{font-family:-apple-system,system-ui,sans-serif;display:flex;justify-content:center;
        align-items:center;min-height:100vh;margin:0;background:#0d0f17;color:#fff;text-align:center;padding:16px}}
  .c{{background:rgba(26,31,46,0.9);border:1px solid rgba(139,92,246,0.3);border-radius:18px;padding:24px;max-width:440px;box-shadow:0 10px 30px rgba(0,0,0,0.5)}}
  .badge{{display:inline-block;padding:4px 12px;background:rgba(139,92,246,0.2);color:#c084fc;border-radius:20px;font-size:12px;font-weight:600;margin-bottom:12px}}
  h2{{margin:0 0 8px 0;font-size:20px}}
  p{{color:#94a3b8;font-size:14px;line-height:1.4;margin:8px 0 16px}}
  .btn{{display:block;width:100%;box-sizing:border-box;padding:14px 20px;background:linear-gradient(135deg,#6366f1 0%,#4f46e5 100%);color:white;
        border-radius:12px;text-decoration:none;font-size:15px;font-weight:600;margin-bottom:8px;border:none;cursor:pointer}}
  .btn-alt{{background:rgba(255,255,255,0.08);border:1px solid rgba(255,255,255,0.18);font-size:13px;padding:10px 16px;color:#cbd5e1;margin-bottom:12px}}
  .btn-green{{background:linear-gradient(135deg,#10b981 0%,#059669 100%);margin-top:4px}}
  .note{{color:#cbd5e1;font-size:13px;margin-top:16px;text-align:left;background:rgba(0,0,0,0.35);padding:14px;border-radius:12px;line-height:1.5}}
  .note b{{color:#fff}}
  .note ol{{padding-left:20px;margin:8px 0 0}}
  .note li{{margin-bottom:6px}}
</style>
</head><body>
<div class="c">
  <div class="badge">🧪 CANARY TEST · МАРШРУТИЗАЦИЯ TIIN</div>
  <h2>Настройка Happ</h2>
  <p>Чтобы Госуслуги, банки и российские сайты открывались от интернета телефона (быстро и без блокировок), а зарубежные — через VPN:</p>
  
  <a class="btn" href="{happ_link_de}" onclick="trackHapp('de', this)">⚡ Способ 1: Добавить в Happ (Германия)</a>
  <a class="btn" style="background:linear-gradient(135deg,#0284c7 0%,#0369a1 100%)" href="{happ_link_ru}" onclick="trackHapp('ru', this)">🇷🇺 Способ 2: Добавить в Happ (Резервный РФ)</a>
  <button class="btn btn-alt" onclick="copyUrl('{de_sub_url}', this, 'de')">📋 Скопировать ссылку 1 (Германия)</button>
  <button class="btn btn-alt" onclick="copyUrl('{ru_sub_url}', this, 'ru')">📋 Скопировать ссылку 2 (РФ Selectel)</button>
  
  <a class="btn btn-green" href="{routing_link}">🔀 Шаг 2: Включить обход РФ в Happ</a>
  <button class="btn btn-alt" onclick="copyUrl('{routing_link}', this)" id="copyRBtn">📋 Скопировать код правил обхода</button>

  <div class="note">
    <b>Как применить в приложении Happ:</b>
    <ol>
      <li><b>Автоматически:</b> Откройте Happ и нажмите 🔄 (Обновить подписку) — правила подтянутся прямо с сервера.</li>
      <li><b>Или через меню:</b> Нажмите «📋 Скопировать код правил обхода» выше ➔ в Happ откройте <b>Настройки ➔ Настройки туннеля ➔ Маршрутизация ➔ «+» вверху ➔ Вставить</b>.</li>
      <li>Выберите профиль <b>«TIIN • Smart RU Bypass»</b> (появится галочка).</li>
      <li>Отключите и включите VPN заново (Reconnect).</li>
    </ol>
  </div>
</div>
<script>
var TOKEN = {json_mod.dumps(token)};
function trackHapp(method, btn) {{
  try {{
    var data = JSON.stringify({{
      event_type: "sub_option_connect",
      element_id: method,
      element_text: btn ? btn.innerText : "",
      extra_data: {{ method: method, app: "happ", source: "go_happ" }}
    }});
    if (navigator.sendBeacon) {{
      navigator.sendBeacon("/go-connect/" + TOKEN + "/event", new Blob([data], {{ type: "application/json" }}));
    }} else {{
      fetch("/go-connect/" + TOKEN + "/event", {{ method: "POST", headers: {{ "Content-Type": "application/json" }}, body: data, keepalive: true }});
    }}
  }} catch(e) {{}}
}}
function copyUrl(url, btn, method) {{
  try {{
    var m = method || (url.indexOf("ru-server") !== -1 ? "ru" : "de");
    var data = JSON.stringify({{
      event_type: "sub_option_copy",
      element_id: m,
      element_text: btn ? btn.innerText : "",
      extra_data: {{ method: m, url: url, source: "go_happ" }}
    }});
    if (navigator.sendBeacon) {{
      navigator.sendBeacon("/go-connect/" + TOKEN + "/event", new Blob([data], {{ type: "application/json" }}));
    }} else {{
      fetch("/go-connect/" + TOKEN + "/event", {{ method: "POST", headers: {{ "Content-Type": "application/json" }}, body: data, keepalive: true }});
    }}
  }} catch(e) {{}}
  navigator.clipboard.writeText(url).then(function() {{
    var old = btn.innerText;
    btn.innerText = "✅ Скопировано!";
    btn.style.color = "#4ade80";
    setTimeout(function() {{
      btn.innerText = old;
      btn.style.color = "#cbd5e1";
    }}, 3000);
  }}).catch(function() {{
    prompt("Скопируйте вручную:", url);
  }});
}}
</script>
</body></html>"""

    return f"""<!DOCTYPE html>
<html><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Открыть в Happ</title>
<style>
  body{{font-family:-apple-system,system-ui,sans-serif;display:flex;justify-content:center;
        align-items:center;min-height:100vh;margin:0;background:#0a0a0a;color:#fff;text-align:center;padding:16px}}
  .c{{background:rgba(26,31,46,0.9);border:1px solid rgba(255,255,255,0.1);border-radius:20px;padding:2rem;max-width:400px;width:100%}}
  .btn{{display:block;width:100%;box-sizing:border-box;padding:15px;background:linear-gradient(135deg,#6366f1 0%,#4f46e5 100%);color:white;
        border-radius:12px;text-decoration:none;font-size:16px;font-weight:600;margin-top:1rem}}
  .btn-ru{{background:linear-gradient(135deg,#10b981 0%,#059669 100%);margin-top:.6rem}}
  .note{{color:#888;font-size:13px;margin-top:1.2rem;line-height:1.4}}
  code{{display:block;background:rgba(0,0,0,0.4);padding:8px;border-radius:8px;margin-top:4px;word-break:break-all;font-size:11px;color:#cbd5e1}}
</style>
</head><body>
<div class="c">
  <h2>Открыть в Happ</h2>
  <p style="color:#94a3b8;font-size:14px;margin-top:6px">Выберите подходящий способ подключения:</p>
  <a class="btn" href="{happ_link_de}" onclick="trackHapp('de', this)">⚡ Способ 1: Германия (Основной)</a>
  <a class="btn btn-ru" href="{happ_link_ru}" onclick="trackHapp('ru', this)">🇷🇺 Способ 2: Россия (Selectel)</a>
  <div style="margin-top:12px;display:flex;gap:8px">
    <button class="btn" style="margin:0;padding:10px;font-size:12px;background:rgba(255,255,255,0.08);border:1px solid rgba(255,255,255,0.15)" onclick="copyUrl('{de_sub_url}', this, 'de')">📋 Копия 1 (DE)</button>
    <button class="btn" style="margin:0;padding:10px;font-size:12px;background:rgba(255,255,255,0.08);border:1px solid rgba(255,255,255,0.15)" onclick="copyUrl('{ru_sub_url}', this, 'ru')">📋 Копия 2 (RU)</button>
  </div>
  <div class="note">Если кнопка не открыла приложение — скопируйте ссылку вручную:
  <br><b>Способ 1:</b> <code>{de_sub_url}</code>
  <b>Способ 2:</b> <code>{ru_sub_url}</code></div>
</div>
<script>
var TOKEN = {json_mod.dumps(token)};
function trackHapp(method, btn) {{
  try {{
    var data = JSON.stringify({{
      event_type: "sub_option_connect",
      element_id: method,
      element_text: btn ? btn.innerText : "",
      extra_data: {{ method: method, app: "happ", source: "go_happ" }}
    }});
    if (navigator.sendBeacon) {{
      navigator.sendBeacon("/go-connect/" + TOKEN + "/event", new Blob([data], {{ type: "application/json" }}));
    }} else {{
      fetch("/go-connect/" + TOKEN + "/event", {{ method: "POST", headers: {{ "Content-Type": "application/json" }}, body: data, keepalive: true }});
    }}
  }} catch(e) {{}}
}}
function copyUrl(url, btn, method) {{
  try {{
    var m = method || (url.indexOf("ru-server") !== -1 ? "ru" : "de");
    var data = JSON.stringify({{
      event_type: "sub_option_copy",
      element_id: m,
      element_text: btn ? btn.innerText : "",
      extra_data: {{ method: m, url: url, source: "go_happ" }}
    }});
    if (navigator.sendBeacon) {{
      navigator.sendBeacon("/go-connect/" + TOKEN + "/event", new Blob([data], {{ type: "application/json" }}));
    }} else {{
      fetch("/go-connect/" + TOKEN + "/event", {{ method: "POST", headers: {{ "Content-Type": "application/json" }}, body: data, keepalive: true }});
    }}
  }} catch(e) {{}}
  navigator.clipboard.writeText(url).then(function() {{
    var old = btn.innerText;
    btn.innerText = "✅ Скопировано!";
    setTimeout(function() {{
      btn.innerText = old;
    }}, 2000);
  }}).catch(function() {{
    prompt("Скопируйте вручную:", url);
  }});
}}
</script>
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

    # Используем ссылки подписки через два шлюза (Германия и РФ Selectel)
    de_sub_url = f"https://344988.snk.wtf/sub/{token}"
    ru_sub_url = f"https://ru-server.tiinservice.online/sub/{token}"

    req_host = (request.headers.get("host") or "") if request else ""
    use_ru = "ru-server" in req_host
    default_server = "ru" if use_ru else "de"
    sub_base = "https://ru-server.tiinservice.online" if use_ru else _get_sub_base_for_user(user)
    sub_url = f"{sub_base}/sub/{token}"
    import base64
    b64_url = base64.urlsafe_b64encode(sub_url.encode()).decode().rstrip("=")
    
    happ_link = f"happ://add/{sub_url}#TIIN%20VPN"
    hiddify_link = f"hiddify://import/{sub_url}#%F0%9F%90%BF%20TIIN%20VPN"
    karing_link = f"karing://install-config?url={urllib.parse.quote(sub_url, safe='')}&name=%F0%9F%90%BF%20TIIN%20VPN"
    incy_link = f"incy://import/{sub_url}"
    streisand_link = f"streisand://import/{sub_url}"
    v2rayng_link = f"v2rayng://install-config?url={urllib.parse.quote(sub_url, safe='')}"
    shadowrocket_link = f"shadowrocket://add/sub://{b64_url}"
    singbox_link = f"sing-box://import-remote-profile?url={urllib.parse.quote(sub_url, safe='')}#%F0%9F%90%BF%20TIIN%20VPN"
    cabinet_link = f"{PORTAL_BASE_URL}/my/{token}"
    routing_link = _happ_routing_deeplink()
    is_admin = bool(user and user.get("tg_id") == ADMIN_TG_ID)
    happ_routing_btn = f'''
    <a class="btn" style="background: linear-gradient(135deg, #10b981 0%, #059669 100%); color: white;" id="btn-happ-routing" data-app="Happ Routing" href="{routing_link}">
      <span>🔀</span> Включить обход РФ в Happ (Госуслуги напрямую)
    </a>''' if is_admin else ""

    import json as json_mod

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
  .server-tabs {{
    display: flex;
    background: rgba(0, 0, 0, 0.45);
    border: 1px solid var(--border);
    border-radius: 14px;
    padding: 4px;
    margin-bottom: 14px;
    gap: 6px;
  }}
  .server-tab {{
    flex: 1;
    padding: 10px 8px;
    font-size: 13px;
    font-weight: 600;
    border: none;
    border-radius: 10px;
    background: transparent;
    color: var(--text-muted);
    cursor: pointer;
    transition: all 0.2s ease;
  }}
  .server-tab.active {{
    background: var(--accent);
    color: #fff;
    box-shadow: 0 2px 10px rgba(99, 102, 241, 0.4);
  }}
  .sub-box {{
    background: rgba(0, 0, 0, 0.3);
    border: 1px dashed var(--border);
    border-radius: 12px;
    padding: 14px;
    margin-top: 14px;
    text-align: left;
  }}
  .sub-box-title {{ font-size: 12px; color: var(--text-muted); margin-bottom: 6px; }}
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
    white-space: nowrap;
  }}
  .cabinet-box {{
    margin-top: 24px;
    padding-top: 16px;
    border-top: 1px solid var(--border);
    display: flex;
    flex-direction: column;
    gap: 8px;
  }}
  .cabinet-desc {{
    font-size: 12px;
    color: var(--text-muted);
    line-height: 1.4;
  }}
  .btn-cabinet {{
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 8px;
    padding: 12px 16px;
    border-radius: 12px;
    text-decoration: none;
    font-size: 13px;
    font-weight: 600;
    color: #c7d2fe;
    background: rgba(99, 102, 241, 0.12);
    border: 1px solid rgba(99, 102, 241, 0.25);
    transition: all 0.2s ease;
  }}
  .btn-cabinet:hover {{
    background: rgba(99, 102, 241, 0.2);
    color: #fff;
  }}
</style>
</head>
<body>
<div class="card">
  <div class="badge">🚀 Быстрый старт в 1 клик</div>
  <h1>Подключение к тииҥ VPN</h1>
  <p class="subtitle">Выберите способ подключения и нажмите кнопку установленного приложения:</p>

  <div class="server-tabs">
    <button type="button" class="server-tab {'active' if not use_ru else ''}" id="tab-de" onclick="switchServer('de')">🇩🇪 Способ 1: Основной</button>
    <button type="button" class="server-tab {'active' if use_ru else ''}" id="tab-ru" onclick="switchServer('ru')">🇷🇺 Способ 2: Резервный РФ</button>
  </div>
  <p id="serverNotice" style="font-size: 12px; color: #94a3b8; margin-bottom: 16px; line-height: 1.4;">
    {'Подключение через европейский сервер (Германия).' if not use_ru else 'Подключение через шлюз в РФ (Selectel СПб) — для обхода блокировок операторов.'}
  </p>

  <div class="btn-group">
    <!-- Кнопки приложений -->
    <a class="btn btn-primary" id="btn-happ" data-app="Happ Proxy" href="{happ_link}">
      <span>⚡</span> Открыть в Happ Proxy
    </a>
    {happ_routing_btn}

    <a class="btn btn-secondary" id="btn-hiddify" data-app="Hiddify" href="{hiddify_link}">
      <span>🔷</span> Открыть в Hiddify
    </a>

    <a class="btn btn-secondary" id="btn-karing" data-app="Karing" href="{karing_link}">
      <span>🔵</span> Открыть в Karing
    </a>

    <a class="btn btn-secondary" id="btn-incy" data-app="INCY" href="{incy_link}">
      <span>🔮</span> Открыть в INCY
    </a>

    <a class="btn btn-secondary" id="btn-shadowrocket" data-app="Shadowrocket" href="{shadowrocket_link}">
      <span>🚀</span> Открыть в Shadowrocket
    </a>
    
    <a class="btn btn-secondary" id="btn-v2rayng" data-app="v2rayNG" href="{v2rayng_link}">
      <span>🤖</span> Открыть в v2rayNG
    </a>

    <a class="btn btn-secondary" id="btn-streisand" data-app="Streisand" href="{streisand_link}">
      <span>🛡️</span> Открыть в Streisand
    </a>
  </div>

  <div class="divider"><span>или скопируйте ссылку</span></div>

  <div class="sub-box">
    <div class="sub-box-title">Активная ссылка подписки:</div>
    <div class="sub-url-row" style="margin-bottom:14px">
      <div class="sub-url" id="subUrlText">{sub_url}</div>
      <button class="btn-copy" id="btnCopySub" onclick="copySub()">Копировать</button>
    </div>
    <div class="sub-box-title" style="color:#93c5fd;font-weight:600">🇩🇪 Способ 1: Основной (344988.snk.wtf)</div>
    <div class="sub-url-row" style="margin-bottom:12px">
      <div class="sub-url" id="deSubUrlText">{de_sub_url}</div>
      <button class="btn-copy" id="btnCopyDe" onclick="copyCustomUrl('{de_sub_url}', this)">Копировать</button>
    </div>
    <div class="sub-box-title" style="color:#86efac;font-weight:600">🇷🇺 Способ 2: Резервный РФ (ru-server.tiinservice.online)</div>
    <div class="sub-url-row">
      <div class="sub-url" id="ruSubUrlText">{ru_sub_url}</div>
      <button class="btn-copy" id="btnCopyRu" onclick="copyCustomUrl('{ru_sub_url}', this)">Копировать</button>
    </div>
  </div>
  <p style="font-size: 11px; color: #94a3b8; margin-top: 10px; text-align: left; line-height: 1.4;">
    💡 <i>Не удается импортировать первым способом? Нажмите <b>«Способ 2: Резервный РФ»</b> — он подключается напрямую через Selectel СПб и обходит блокировки операторов РФ.</i>
  </p>

  <div class="cabinet-box">
    <div class="cabinet-desc">Нужен статус подписки, QR-код или подробная пошаговая инструкция?</div>
    <a class="btn-cabinet" id="btnToCabinet" href="{cabinet_link}">
      <span>👤</span> Открыть Личный кабинет
    </a>
  </div>
</div>

<script>
// ── Трекинг аналитики на странице 1 клик ──
(function() {{
  var TOKEN = {json_mod.dumps(token)};
  var API = '/go-connect/' + TOKEN + '/event';

  function track(eventType, el, extra) {{
    var payload = {{
      event_type: eventType,
      element_id: el ? el.id || null : null,
      element_text: el ? (el.textContent || '').trim().substring(0, 200) : null,
      extra_data: Object.assign({{ page: 'go_connect', flow: 'one_click' }}, extra || {{}})
    }};
    if (navigator.sendBeacon) {{
      navigator.sendBeacon(API, new Blob([JSON.stringify(payload)], {{ type: 'application/json' }}));
    }} else {{
      fetch(API, {{
        method: 'POST',
        headers: {{ 'Content-Type': 'application/json' }},
        body: JSON.stringify(payload),
        keepalive: true
      }}).catch(function(){{}});
    }}
  }}

  window._trackEvent = track;

  // Track page load
  track('page_view', null, {{
    url: window.location.pathname,
    referrer: document.referrer || null,
    screen: window.innerWidth + 'x' + window.innerHeight
  }});

  // Слушатель кликов
  document.addEventListener('click', function(e) {{
    var el = e.target.closest('a, button');
    if (!el) return;

    var extra = {{}};
    if (el.dataset && el.dataset.app) {{
      var currentMethod = (document.getElementById('tab-ru') && document.getElementById('tab-ru').classList.contains('active')) ? 'ru' : 'de';
      extra.app = el.dataset.app;
      extra.href = el.href || null;
      extra.method = currentMethod;
      track('sub_option_connect', el, extra);
      track('click_connect_btn', el, extra);
    }} else if (el.id === 'btnCopySub' || el.id === 'btnCopyDe' || el.id === 'btnCopyRu') {{
      var copyMethod = (el.id === 'btnCopyRu') ? 'ru' : 'de';
      track('sub_option_copy', el, {{ method: copyMethod }});
      track('copy_subscription_url', el);
    }} else if (el.id === 'btnToCabinet' || (el.href && el.href.indexOf('/my/') !== -1)) {{
      extra.target_url = el.href;
      track('click_to_cabinet', el, extra);
    }} else if (el.tagName === 'A') {{
      extra.href = el.href || null;
      track('click_link', el, extra);
    }}
  }});

  // Track page unload (время на странице)
  var pageStart = Date.now();
  window.addEventListener('beforeunload', function() {{
    var duration = Math.round((Date.now() - pageStart) / 1000);
    track('page_unload', null, {{ duration_seconds: duration }});
  }});
}})();

var TOKEN = {json_mod.dumps(token)};
var DE_SUB_URL = "https://344988.snk.wtf/sub/" + TOKEN;
var RU_SUB_URL = "https://ru-server.tiinservice.online/sub/" + TOKEN;

function toB64Safe(str) {{
  return btoa(unescape(encodeURIComponent(str))).replace(/\\+/g, '-').replace(/\\//g, '_').replace(/=+$/, '');
}}

function switchServer(srv) {{
  var sub = (srv === 'ru') ? RU_SUB_URL : DE_SUB_URL;
  var tabDe = document.getElementById('tab-de');
  var tabRu = document.getElementById('tab-ru');
  if (tabDe) tabDe.className = 'server-tab' + (srv === 'de' ? ' active' : '');
  if (tabRu) tabRu.className = 'server-tab' + (srv === 'ru' ? ' active' : '');

  try {{
    if (window._trackEvent) {{
      window._trackEvent('sub_option_select', (srv === 'ru' ? tabRu : tabDe), {{
        method: srv,
        domain: (srv === 'ru' ? 'ru-server.tiinservice.online' : '344988.snk.wtf')
      }});
    }}
  }} catch(e) {{}}

  var notice = document.getElementById('serverNotice');
  if (notice) {{
    notice.innerText = (srv === 'ru')
      ? 'Подключение через шлюз в РФ (Selectel СПб) — для обхода блокировок операторов.'
      : 'Подключение через основной европейский сервер (Германия).';
  }}

  var hidden = document.getElementById('subUrlText');
  if (hidden) hidden.innerText = sub;

  var b64 = toB64Safe(sub);
  var enc = encodeURIComponent(sub);

  var el;
  el = document.getElementById('btn-happ'); if (el) el.href = 'happ://add/' + sub + '#TIIN%20VPN';
  el = document.getElementById('btn-hiddify'); if (el) el.href = 'hiddify://import/' + sub + '#%F0%9F%90%BF%20TIIN%20VPN';
  el = document.getElementById('btn-karing'); if (el) el.href = 'karing://install-config?url=' + enc + '&name=%F0%9F%90%BF%20TIIN%20VPN';
  el = document.getElementById('btn-incy'); if (el) el.href = 'incy://import/' + sub;
  el = document.getElementById('btn-shadowrocket'); if (el) el.href = 'shadowrocket://add/sub://' + b64;
  el = document.getElementById('btn-v2rayng'); if (el) el.href = 'v2rayng://install-config?url=' + enc;
  el = document.getElementById('btn-streisand'); if (el) el.href = 'streisand://import/' + sub;
}}

window.switchServer = switchServer;

function copyCustomUrl(text, btn) {{
  var m = (text && text.indexOf('ru-server') !== -1) ? 'ru' : 'de';
  try {{
    if (window._trackEvent) {{
      window._trackEvent('sub_option_copy', btn, {{ method: m, url: text }});
    }}
  }} catch(e) {{}}
  navigator.clipboard.writeText(text).then(function() {{
    var old = btn.innerText;
    btn.innerText = 'Скопировано!';
    btn.style.background = '#10b981';
    btn.style.borderColor = '#10b981';
    setTimeout(function() {{
      btn.innerText = old;
      btn.style.background = '';
      btn.style.borderColor = '';
    }}, 2000);
  }}).catch(function() {{
    prompt('Скопируйте ссылку вручную:', text);
  }});
}}
window.copyCustomUrl = copyCustomUrl;

function copySub() {{
  var hidden = document.getElementById('subUrlText');
  var text = hidden ? hidden.innerText : DE_SUB_URL;
  copyCustomUrl(text, document.getElementById('btnCopySub') || event.target);
}}
</script>
</body>
</html>"""


@sub_router.post("/go-connect/{token}/event")
async def track_go_connect_event(token: str, req: WebEventRequest, request: Request):
    """Слушатель событий на странице Подключить в 1 клик."""
    extra = req.extra_data or {}
    if not isinstance(extra, dict):
        extra = {"raw": str(extra)}
    extra.setdefault("source", "go_connect_one_click")

    element_id = req.element_id
    if extra.get("method") and (not element_id or element_id not in ("de", "ru")):
        element_id = str(extra.get("method"))

    _log_webpage_event(
        web_token=token,
        event_type=req.event_type,
        element_id=element_id,
        element_text=req.element_text,
        extra_data=extra,
        ip=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    return {"ok": True}



@sub_router.api_route("/updates_ui/{token}", methods=["GET", "HEAD"])
@sub_router.api_route("/sub/{token}", methods=["GET", "HEAD"])
async def proxy_subscription(token: str, request: Request = None):
    """Эндпоинт подписки – проксирует ответ от XUI и склеивает с Hysteria."""
    req_client = getattr(request, "client", None) if request else None
    client_ip = (request.headers.get("x-real-ip") or (req_client.host if req_client else "unknown")) if request else "unknown"
    ua = request.headers.get("user-agent", "") if request else ""

    user = get_user_by_web_token(token)
    if not user:
        # Fallback: токен может быть 3x-ui subId (например, 9wbtovi3fhihszxa)
        key_row = execute_query(
            "SELECT user_id, tg_id FROM vpn_keys WHERE subscription_link LIKE %s LIMIT 1",
            (f"%{token}%",),
            fetch="one",
        )
        if key_row:
            if key_row.get("user_id"):
                user = get_user_by_id(key_row["user_id"])
            elif key_row.get("tg_id"):
                user = get_user_by_tg_id(key_row["tg_id"])

    if not user:
        logger.warning(f"[SUB_ERROR] status=404 detail=UserNotFound token={token[:16]} ip={client_ip} ua={ua[:60]}")
        raise HTTPException(status_code=404, detail="Not found")

    key = _pick_vless_key(user)
    if not key:
        logger.warning(f"[SUB_ERROR] status=404 detail=NoActiveKey tg_id={user.get('tg_id')} ip={client_ip} ua={ua[:60]}")
        raise HTTPException(status_code=404, detail="No active subscription")

    _check_retry_burst(f"{token}_{client_ip}", client_ip, ua, user.get("tg_id"))

    platform, app, ver = detect_platform(ua)
    if platform != 'unknown' or app:
        log_user_platform(
            tg_id=user.get("tg_id"), user_id=user.get("id"),
            platform=platform, client_app=app, client_version=ver,
            user_agent=ua,
            ip=client_ip if client_ip != "unknown" else None,
        )

    # Логируем фактический импорт подписки клиентом (различаем шлюз Selectel SPB и Германию)
    req_host = (request.headers.get("host") or "").lower() if request else ""
    fetch_method = "ru" if "ru-server" in req_host else "de"
    _log_webpage_event(
        web_token=token,
        event_type="sub_import_fetch",
        element_id=fetch_method,
        element_text=req_host or "344988.snk.wtf",
        extra_data={"method": fetch_method, "host": req_host, "client_app": app or "unknown"},
        ip=client_ip if client_ip != "unknown" else None,
        user_agent=ua,
    )

    xui_url = key["subscription_link"]
    expires_at = key.get("expires_at")

    # Check if subscription is expired
    now_dt = datetime.utcnow()
    is_expired = expires_at and expires_at <= now_dt

    # Detect VPN client (no text/html in Accept header)
    accept = request.headers.get("accept", "") if request else ""
    is_browser = "text/html" in accept

    # Detect if client requests sing-box format (Clash receives Base64 for native parsing)
    wants_singbox = any(k in ua.lower() for k in ["sing-box", "hiddify", "karing"])

    # If expired and VPN client → return expired message
    if is_expired and not is_browser:
        renewal_url = f"{PORTAL_BASE_URL}/my/{token}"
        expired_vless = f"vless://dummy@expired:443?type=tcp#❌ Подписка истекла — продлите: {renewal_url}"
        raw_body = base64.b64encode(expired_vless.encode())
        
        headers = _build_headers(expires_at)
        async with _CACHE_LOCK:
            _CACHE[token] = (time.time(), raw_body, headers)
        return Response(content=raw_body, headers=headers)

    is_admin = bool(user and user.get("tg_id") == ADMIN_TG_ID)
    routing_link = _happ_routing_deeplink() if (is_admin or "happ" in ua.lower()) else None
    headers = _build_headers(expires_at, is_json=wants_singbox, routing_link=routing_link)

    # Fast 200 OK response for client connectivity/HEAD checks
    if getattr(request, "method", "GET") == "HEAD":
        return Response(content=b"", headers=headers)

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
        seen_remarks = set()
        seen_links = set()
        for line in decoded_sub.splitlines():
            if not line.strip():
                continue
            if '#' in line:
                parts = line.split('#')
                base_link = '#'.join(parts[:-1])
            else:
                base_link = line

            # Очистка несовместимых параметров Post-Quantum (pqv и ML-KEM), вызывающих фрагментацию пакетов на мобильных MTU
            import re
            base_link = re.sub(r'pqv=[^&]+&?', '', base_link)
            base_link = re.sub(r'support-x25519mlkem768=[^&]+&?', '', base_link)
            base_link = base_link.rstrip('?&')

            # Исключаем нерабочие/отключенные узлы по запросу
            if "type=ws" in line or ":14715" in line:
                continue  # WebSocket • MUX (0 подключений)
            elif ":38745" in line:
                continue  # xHTTP • Ultra (дублирует и сбоит на МТС/Мегафон)

            if ":7443" in line:
                active_sni = _get_active_reality_sni()
                if "sni=" in base_link:
                    import re
                    base_link = re.sub(r'sni=[^&]+', f'sni={active_sni}', base_link)
                else:
                    base_link = f"{base_link}&sni={active_sni}"
                remark = "🇩🇪 👁️ Reality • Vision (МТС / РТК)"
            elif line.startswith("hysteria2://") or line.startswith("hysteria://"):
                if ":35443" in line:
                    remark = "🇩🇪 ⚔️ Hysteria2 • Armor-M1"
                elif ":4443" in line:
                    remark = "🇩🇪 🚀 Hysteria2 • Turbo (Быстрый)"
                else:
                    remark = "🇩🇪 🚀 Hysteria2"
            elif line.startswith("tuic://"):
                remark = "🇩🇪 🌐 TUIC"
            elif line.startswith("ss://"):
                remark = "🇩🇪 🛡️ Shadowsocks"
            elif "type=grpc" in line:
                remark = "🇩🇪 🟣 VLESS • gRPC"
            elif ":8081" in line or "amd.com" in line:
                import re
                base_link = base_link.replace(":8081", ":443")
                base_link = re.sub(r'sni=[^&]+', 'sni=swscan.apple.com', base_link)
                base_link = re.sub(r'host=[^&]+', 'host=swscan.apple.com', base_link)
                base_link = re.sub(r'spx=[^&]+', 'spx=%2F', base_link)
                base_link = re.sub(r'mode=[^&]+', 'mode=stream-one', base_link)
                base_link = re.sub(r'flow=[^&]+&?', '', base_link)
                base_link = re.sub(r'extra=[^&]+&?', '', base_link)
                base_link = re.sub(r'fm=[^&]+&?', '', base_link)
                base_link = re.sub(r'&+', '&', base_link)
                base_link = base_link.rstrip('?&')
                remark = "🇩🇪 🛡️ xHTTP • 443 (Admin Test)"
            elif ":48745" in line or ("type=xhttp" in line and ("swscan.apple.com" in line or "update.microsoft.com" in line)):
                active_sni_15 = _get_active_sni("15")
                import re
                if "sni=" in base_link:
                    base_link = re.sub(r'sni=[^&]+', f'sni={active_sni_15}', base_link)
                else:
                    base_link = f"{base_link}&sni={active_sni_15}"
                if "host=" in base_link:
                    base_link = re.sub(r'host=[^&]+', f'host={active_sni_15}', base_link)
                else:
                    base_link = f"{base_link}&host={active_sni_15}"
                base_link = re.sub(r'flow=[^&]+&?', '', base_link)
                base_link = base_link.rstrip('?&')
                remark = "🇩🇪 💎 xHTTP • Armor (МТС / Билайн)"
            elif ":53151" in line or ("security=reality" in line and ("amazon.com" in line or "bahn.de" in line)):
                active_sni_10 = _get_active_sni("10")
                if "sni=" in base_link:
                    import re
                    base_link = re.sub(r'sni=[^&]+', f'sni={active_sni_10}', base_link)
                remark = "🇩🇪 🛡️ Reality • Standard (Bahn)"
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
                remark = "🇩🇪 🌊 xHTTP • Stream (МегаФон)"
            elif ":28745" in line or ("type=xhttp" in line and "google.com" in line):
                remark = "🇩🇪 🟠 xHTTP • Standard"
            elif "type=xhttp" in line:
                remark = "🇩🇪 🟠 xHTTP"
            elif line.startswith("vpn://"):
                remark = "🇩🇪 ⚡ AmneziaWG"
            elif "security=reality" in line:
                remark = "🇩🇪 👁️ Reality"
            else:
                remark = "🇩🇪 🟢 TCP"

            # Исключаем дубликаты инбаундов по base_link или remark
            if remark in seen_remarks or base_link in seen_links:
                continue
            seen_remarks.add(remark)
            seen_links.add(base_link)

            new_lines.append(f"{base_link}#{remark}")
            vless_entries.append((base_link, remark))

        # Сортируем немецкие серверы по надежности и операторам
        def _server_sort_key(entry):
            _, remark = entry
            if "Hysteria2 • Turbo" in remark: return 1
            if "Reality • Vision" in remark: return 2
            if "xHTTP • Armor" in remark: return 3
            if "xHTTP • Stream" in remark: return 4
            if "Reality • Standard" in remark: return 5
            if "Hysteria2 • Armor" in remark: return 6
            if "AmneziaWG" in remark: return 7
            if "Hysteria2" in remark: return 8
            if "Reality" in remark: return 9
            if "xHTTP" in remark: return 10
            return 20
        vless_entries.sort(key=_server_sort_key)
        new_lines = [f"{link}#{remark}" for link, remark in vless_entries]

        # Добавляем Российский шлюз (СПб ➔ Германия) для всех пользователей
        real_vless_uuid = None
        for v_link, _ in vless_entries:
            if v_link.startswith("vless://"):
                try:
                    import urllib.parse
                    p = urllib.parse.urlparse(v_link)
                    real_vless_uuid = p.netloc.split('@')[0]
                    if real_vless_uuid:
                        break
                except Exception:
                    pass

        client_uuid = real_vless_uuid or key.get("client_id")
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
            is_admin = bool(user and user.get("tg_id") == ADMIN_TG_ID)
            ru_vless_remark = "🇷🇺 🛡️ VLESS • Шлюз (РФ ➔ DE)" if is_admin else "🇷🇺 🛡️ XHTTP • Шлюз (РФ ➔ DE)"
            new_lines.insert(1, f"{ru_vless_link}#{ru_vless_remark}")
            vless_entries.insert(1, (ru_vless_link, ru_vless_remark))

        if wants_singbox:
            singbox_cfg = _build_singbox_config(vless_entries)
            raw_body = json.dumps(singbox_cfg, ensure_ascii=False, indent=2).encode('utf-8')
        else:
            if routing_link and ("happ" in ua.lower() or is_admin):
                new_lines.append(routing_link)
            decoded_sub = "\n".join(new_lines)
            raw_body = base64.b64encode(decoded_sub.encode('utf-8'))

        # Store in cache
        async with _CACHE_LOCK:
            _CACHE[cache_key] = (now, raw_body, headers)

    except XUIError as e:
        logger.warning(f"[SUB_ERROR] status=410 detail=XUIClientNotFound token={token[:16]} ip={client_ip} error={e}")
        async with _CACHE_LOCK:
            cached = _CACHE.get(cache_key)
            if cached:
                return Response(content=cached[1], headers=cached[2])
        raise HTTPException(status_code=410, detail="Subscription expired or removed")
    except Exception as e:
        logger.error(f"[SUB_ERROR] status=503 detail=UpstreamFailed token={token[:16]} ip={client_ip} error={e}")
        async with _CACHE_LOCK:
            cached = _CACHE.get(cache_key)
            if cached:
                return Response(content=cached[1], headers=cached[2])
        raise HTTPException(status_code=503, detail="Upstream unavailable")

    return Response(content=raw_body, headers=headers)
