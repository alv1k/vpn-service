#!/usr/bin/env python3
"""
Мониторинг AWG handshake-ов: обнаружение шаринга конфигов.

Если один peer за последние WINDOW_HOURS часов подключался с более чем
MAX_UNIQUE_IPS разных IP — конфиг расшарен. Peer отключается, админ
получает уведомление в Telegram.

Состояние хранится в /var/lib/awg_monitor/state.json

Cron: */5 * * * *
"""
import json
import logging
import os
import subprocess
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("awg_monitor")

# ── Config ──────────────────────────────────────────────────────────────────
MAX_UNIQUE_IPS = 3          # больше 3 разных IP за окно → шаринг
WINDOW_HOURS = 6            # окно наблюдения
STATE_DIR = "/var/lib/awg_monitor"
STATE_FILE = os.path.join(STATE_DIR, "state.json")
AWG_INTERFACE = "awg0"
AUTO_DISABLE = True         # True = автоотключение, False = только алерт


def _load_state() -> dict:
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return {}


def _save_state(state: dict):
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def _get_awg_peers() -> list[dict]:
    """Parse `awg show <iface> dump` into list of peer dicts."""
    result = subprocess.run(
        ["awg", "show", AWG_INTERFACE, "dump"],
        capture_output=True, text=True, check=False,
    )
    if result.returncode != 0:
        log.error("awg show failed: %s", result.stderr)
        return []

    peers = []
    for line in result.stdout.strip().splitlines()[1:]:  # skip interface line
        parts = line.split("\t")
        if len(parts) < 7:
            continue
        endpoint = parts[2]
        ip = endpoint.rsplit(":", 1)[0] if endpoint != "(none)" else None
        peers.append({
            "public_key": parts[0],
            "allowed_ips": parts[3],
            "handshake_ts": int(parts[4]) if parts[4] != "0" else 0,
            "endpoint_ip": ip,
        })
    return peers


def _resolve_peer_names() -> dict:
    """Map public_key → client_name via awg_clients DB."""
    try:
        from awg_api import db as awg_db
        clients = awg_db.list_clients()
        return {c["public_key"]: c["name"] for c in clients}
    except Exception as e:
        log.warning("Cannot load client names: %s", e)
        return {}


def _send_alert(text: str):
    """Send Telegram alert to admin."""
    try:
        from dotenv import load_dotenv
        load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))
        bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
        admin_id = os.getenv("ADMIN_TG_ID")
        if not bot_token or not admin_id:
            log.warning("No bot token or admin ID for alert")
            return
        import urllib.request
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        data = json.dumps({"chat_id": admin_id, "text": text, "parse_mode": "HTML"}).encode()
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=10)
        log.info("Alert sent to admin")
    except Exception as e:
        log.error("Failed to send alert: %s", e)


def _disable_peer(public_key: str, name_map: dict):
    """Disable peer in AWG DB and reload interface."""
    try:
        from awg_api import db as awg_db
        from awg_api import awg_manager
        clients = awg_db.list_clients()
        for c in clients:
            if c["public_key"] == public_key:
                awg_db.update_client_enabled(c["id"], False)
                awg_manager.write_server_conf()
                if awg_manager.is_interface_up():
                    awg_manager.reload_interface()
                log.info("DISABLED peer %s (%s)", c["name"], c["id"])
                return
    except Exception as e:
        log.error("Failed to disable peer: %s", e)


def main():
    now = time.time()
    cutoff = now - WINDOW_HOURS * 3600
    state = _load_state()
    peers = _get_awg_peers()
    name_map = _resolve_peer_names()

    for peer in peers:
        pk = peer["public_key"]
        ip = peer["endpoint_ip"]
        hs = peer["handshake_ts"]

        if not ip or hs == 0:
            continue

        # Handshake слишком старый — пропускаем
        if hs < cutoff:
            continue

        # Инициализируем state для peer
        if pk not in state:
            state[pk] = {"ips": {}, "alerted": False, "disabled": False}

        # Записываем IP и timestamp
        state[pk]["ips"][ip] = hs

        # Чистим старые записи
        state[pk]["ips"] = {
            k: v for k, v in state[pk]["ips"].items()
            if v > cutoff
        }

    # Проверяем на шаринг
    for pk, data in state.items():
        unique_ips = len(data["ips"])
        name = name_map.get(pk, pk[:16])

        if unique_ips > MAX_UNIQUE_IPS:
            ip_list = ", ".join(sorted(data["ips"].keys()))
            log.warning(
                "SHARING DETECTED: %s — %d unique IPs in %dh: %s",
                name, unique_ips, WINDOW_HOURS, ip_list,
            )

            if not data.get("alerted"):
                _send_alert(
                    f"⚠️ <b>AWG шаринг конфига</b>\n\n"
                    f"Клиент: <b>{name}</b>\n"
                    f"Уникальных IP за {WINDOW_HOURS}ч: <b>{unique_ips}</b>\n"
                    f"IP: <pre>{ip_list}</pre>"
                )
                data["alerted"] = True

            if AUTO_DISABLE and not data.get("disabled"):
                _disable_peer(pk, name_map)
                data["disabled"] = True
        else:
            # Сброс флагов если всё в норме
            data["alerted"] = False
            data["disabled"] = False

    # Чистим state от peer-ов без записей
    state = {pk: d for pk, d in state.items() if d["ips"]}

    _save_state(state)
    log.info("Checked %d peers, state saved", len(peers))


if __name__ == "__main__":
    main()
