#!/usr/bin/env python3
"""
Скрипт проверки и автоматической ротации SNI и параметров для:
- Inbound #1  (VLESS TCP Reality, порт 7443)
- Inbound #2  (VLESS xHTTP Stream, порт 47447)
- Inbound #14 (VLESS xHTTP Ultra, порт 38745)
"""
import os
import sys
import json
import time
import socket
import ssl
import urllib.request
import asyncio
import logging
from pathlib import Path

# Add project root to sys.path
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from config import ADMIN_TG_ID, TELEGRAM_BOT_TOKEN
import urllib.request
import urllib.parse

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger("sni_checker")

STATE_FILE = BASE_DIR / "data" / "active_sni.json"

INBOUND_POOLS = {
    "1": {
        "name": "Inbound #1 (Reality TCP Vision)",
        "port": 7443,
        "default": "tile.openstreetmap.org",
        "candidates": [
            {"sni": "tile.openstreetmap.org", "desc": "OpenStreetMap Tiles"},
            {"sni": "ftp.fau.de", "desc": "FAU University Mirror"},
            {"sni": "www.uni-frankfurt.de", "desc": "Frankfurt University"},
        ]
    },
    "2": {
        "name": "Inbound #2 (xHTTP Stream)",
        "port": 47447,
        "default": "www.speedtest.net",
        "candidates": [
            {"sni": "www.speedtest.net", "desc": "Ookla Speedtest CDN"},
            {"sni": "download.videolan.org", "desc": "VideoLAN VLC CDN"},
        ]
    },
    "14": {
        "name": "Inbound #14 (xHTTP Ultra)",
        "port": 38745,
        "default": "www.speedtest.net",
        "candidates": [
            {"sni": "www.speedtest.net", "desc": "Ookla Speedtest CDN"},
            {"sni": "www.uni-heidelberg.de", "desc": "Heidelberg University"},
        ]
    }
}


def test_tls13(host: str, port: int = 443, timeout: float = 3.5) -> dict:
    """Проверяет TLS 1.3 и валидный HTTP handshake."""
    start = time.perf_counter()
    ctx = ssl.create_default_context()
    ctx.check_hostname = True
    ctx.verify_mode = ssl.CERT_REQUIRED
    ctx.set_alpn_protocols(["h2", "http/1.1"])

    try:
        with socket.create_connection((host, port), timeout=timeout) as sock:
            with ctx.wrap_socket(sock, server_hostname=host) as ssock:
                version = ssock.version()
                alpn = ssock.selected_alpn_protocol()
                cipher = ssock.cipher()
                elapsed_ms = (time.perf_counter() - start) * 1000

                if version != "TLSv1.3":
                    return {"ok": False, "error": f"Requires TLSv1.3, got {version}"}

                # Также делаем легкую проверку GET
                try:
                    req = urllib.request.Request(f"https://{host}", headers={"User-Agent": "Mozilla/5.0"})
                    with urllib.request.urlopen(req, context=ctx, timeout=3) as resp:
                        if resp.status in (200, 301, 302, 304, 403):
                            return {
                                "ok": True,
                                "version": version,
                                "alpn": alpn,
                                "cipher": cipher[0] if cipher else "Unknown",
                                "rtt_ms": round(elapsed_ms, 1),
                            }
                except Exception as ex:
                    pass

                return {
                    "ok": True,
                    "version": version,
                    "alpn": alpn,
                    "cipher": cipher[0] if cipher else "Unknown",
                    "rtt_ms": round(elapsed_ms, 1),
                }
    except Exception as e:
        return {"ok": False, "error": str(e)}


def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {
        "active_sni_1": "tile.openstreetmap.org",
        "active_sni_2": "www.samsung.com",
        "active_sni_14": "www.speedtest.net",
    }


def save_state(state: dict):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


async def send_admin_alert(text: str):
    if not ADMIN_TG_ID or not TELEGRAM_BOT_TOKEN:
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {
            "chat_id": int(ADMIN_TG_ID),
            "text": text,
            "parse_mode": "HTML"
        }
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=5) as response:
            pass
    except Exception as e:
        logger.error(f"Failed to send Telegram SNI rotation alert: {e}")


async def main():
    logger.info("🔍 Running multi-inbound SNI health check...")
    state = load_state()
    rotations = []

    for inb_key, pool_info in INBOUND_POOLS.items():
        state_key = f"active_sni_{inb_key}"
        current_active = state.get(state_key, pool_info["default"])
        logger.info(f"\n--- Checking {pool_info['name']} (Current: {current_active}) ---")

        results = []
        current_status = None

        for item in pool_info["candidates"]:
            sni = item["sni"]
            res = test_tls13(sni)
            res["sni"] = sni
            res["desc"] = item["desc"]
            results.append(res)

            if sni == current_active:
                current_status = res

            status_str = f"✅ OK ({res.get('rtt_ms')}ms, {res.get('alpn')})" if res["ok"] else f"❌ FAIL ({res.get('error')})"
            logger.info(f"[{sni}] {status_str}")

        valid = [r for r in results if r["ok"]]
        valid.sort(key=lambda x: x.get("rtt_ms", 9999))

        if not valid:
            logger.critical(f"🚨 CRITICAL: No valid SNI for {pool_info['name']}!")
            await send_admin_alert(
                f"🚨 <b>[SNI Alert - {pool_info['name']}]</b>\n"
                f"Все SNI кандидаты в пуле инбаунда не прошли проверку TLS 1.3!"
            )
            continue

        best = valid[0]
        needs_rotation = False
        reason = ""

        if not current_status or not current_status["ok"]:
            needs_rotation = True
            err_msg = current_status.get("error", "Not tested") if current_status else "Not in pool"
            reason = f"Текущий SNI (<code>{current_active}</code>) недоступен: <i>{err_msg}</i>"
        elif not current_active:
            needs_rotation = True
            reason = "Первичная инициализация"

        if needs_rotation:
            new_sni = best["sni"]
            logger.info(f"🔄 Rotating {pool_info['name']}: {current_active} -> {new_sni}")
            state[state_key] = new_sni
            rotations.append({
                "inbound": pool_info["name"],
                "old": current_active,
                "new": new_sni,
                "desc": best["desc"],
                "rtt": best["rtt_ms"],
                "alpn": best.get("alpn"),
                "reason": reason
            })
        else:
            state[state_key] = current_active
            logger.info(f"✅ {pool_info['name']} active SNI [{current_active}] is healthy ({current_status.get('rtt_ms')}ms).")

    state["active_sni"] = state.get("active_sni_1", "tile.openstreetmap.org")
    state["last_check"] = int(time.time())
    save_state(state)

    if rotations:
        for r in rotations:
            alert_text = (
                f"🔄 <b>[SNI Auto-Rotation: {r['inbound']}]</b>\n\n"
                f"⚠️ <b>Причина:</b> {r['reason']}\n\n"
                f"✅ <b>Новый активный SNI:</b> <code>{r['new']}</code>\n"
                f"🏷 <b>Описание:</b> {r['desc']}\n"
                f"⚡ <b>RTT:</b> {r['rtt']} ms | ALPN: {r['alpn']}\n\n"
                "<i>Новый SNI и host автоматически применены в подписках клиентов.</i>"
            )
            await send_admin_alert(alert_text)


if __name__ == "__main__":
    asyncio.run(main())
