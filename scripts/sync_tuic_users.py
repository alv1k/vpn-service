#!/usr/bin/env python3
"""
Скрипт синхронизации активных клиентов из 3x-ui (Inbound #4) в TUIC v5 (/etc/sing-box/config.json).
Проверяет валидность сгенерированного конфига через `sing-box check` и при изменениях
выполняет перезагрузку сервиса `sing-box-tuic.service`.
"""
import json
import logging
import os
import subprocess
import sqlite3
import sys

CONFIG_PATH = "/etc/sing-box/config.json"
XUI_DB_PATH = "/etc/x-ui/x-ui.db"
CERT_PATH = "/etc/letsencrypt/live/344988.snk.wtf/fullchain.pem"
KEY_PATH = "/etc/letsencrypt/live/344988.snk.wtf/privkey.pem"
LISTEN_PORT = 8445
SERVER_NAME = "344988.snk.wtf"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


def get_active_clients():
    if not os.path.exists(XUI_DB_PATH):
        logging.error(f"X-UI DB not found at {XUI_DB_PATH}")
        return []

    conn = sqlite3.connect(XUI_DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT settings FROM inbounds WHERE id=4")
    row = cur.fetchone()
    conn.close()

    if not row or not row[0]:
        logging.error("Inbound #4 not found in X-UI database")
        return []

    try:
        data = json.loads(row[0])
        clients = data.get("clients", [])
    except Exception as e:
        logging.error(f"Failed to parse Inbound #4 settings: {e}")
        return []

    tuic_users = []
    for c in clients:
        if c.get("enable"):
            email = c.get("email", "")
            uid = c.get("id", "")
            auth = c.get("auth", "")
            pwd = auth if auth else uid
            if uid and pwd:
                tuic_users.append({
                    "name": email,
                    "uuid": uid,
                    "password": pwd
                })
    return tuic_users


def generate_config(users):
    return {
        "log": {
            "level": "info",
            "timestamp": True
        },
        "inbounds": [
            {
                "type": "tuic",
                "tag": "tuic-in",
                "listen": "::",
                "listen_port": LISTEN_PORT,
                "users": users,
                "congestion_control": "bbr",
                "tls": {
                    "enabled": True,
                    "server_name": SERVER_NAME,
                    "certificate_path": CERT_PATH,
                    "key_path": KEY_PATH,
                    "alpn": ["h3"]
                }
            }
        ],
        "outbounds": [
            {
                "type": "direct",
                "tag": "direct"
            }
        ]
    }


def main():
    users = get_active_clients()
    if not users:
        logging.warning("No active users found for TUIC v5. Skipping sync.")
        return 0

    new_config = generate_config(users)
    new_json_str = json.dumps(new_config, indent=2, ensure_ascii=False)

    old_json_str = ""
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                old_json_str = f.read()
        except Exception as e:
            logging.warning(f"Could not read existing config: {e}")

    if new_json_str == old_json_str:
        logging.info(f"TUIC v5 config is up to date ({len(users)} users). No reload needed.")
        return 0

    tmp_path = "/tmp/sing-box-tuic-check.json"
    with open(tmp_path, "w", encoding="utf-8") as f:
        f.write(new_json_str)

    # Validate syntax via sing-box check
    check_res = subprocess.run(["/usr/local/bin/sing-box", "check", "-c", tmp_path], capture_output=True, text=True)
    if check_res.returncode != 0:
        logging.error(f"Generated sing-box config failed check: {check_res.stderr}")
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        return 1

    # Apply configuration
    try:
        subprocess.run(["sudo", "cp", tmp_path, CONFIG_PATH], check=True)
        subprocess.run(["sudo", "chmod", "640", CONFIG_PATH], check=True)
        os.remove(tmp_path)
        logging.info(f"Updated {CONFIG_PATH} with {len(users)} users.")
    except Exception as e:
        logging.error(f"Failed to copy config: {e}")
        return 1

    # Reload/restart systemd service if running
    svc_check = subprocess.run(["systemctl", "is-active", "--quiet", "sing-box-tuic.service"])
    if svc_check.returncode == 0:
        logging.info("Reloading sing-box-tuic service...")
        subprocess.run(["sudo", "systemctl", "restart", "sing-box-tuic.service"], check=False)
    else:
        logging.info("sing-box-tuic service is not active yet.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
