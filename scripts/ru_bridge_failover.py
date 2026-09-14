#!/usr/bin/env python3
"""
RU Bridge Health & Failover Manager
Primary: Hysteria 2 Bridge (hy2-bridge.service -> 127.0.0.1:10888)
Backup: olcRTC WebRTC Bridge (olcrtc-client.service -> 127.0.0.1:10808)
"""
import os
import sys
import time
import json
import sqlite3
import subprocess
import datetime
import urllib.request

DB_FILE = "/etc/x-ui/x-ui.db"
STATE_FILE = "/root/data/ru_bridge_state"
LOG_FILE = "/root/logs/ru-bridge-failover.log"
TRAFFIC_STATE_FILE = "/root/data/ru_traffic_state.json"

BOT_TOKEN = "8075947163:AAEQ5A4rmMLRXjynOiNH3lXWQV-EHwCkdn8"
ADMIN_CHAT_ID = "364224373"

FAIL_THRESHOLD = 2
RECOVER_THRESHOLD = 2

MAX_LIMIT_TB = 5.0
ALERT_THRESHOLD_TB = 4.0
ALERT_THRESHOLD_BYTES = int(ALERT_THRESHOLD_TB * 1024**4)

def log(msg):
    os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(f"[{ts}] {msg}\n")

def notify(text):
    if not BOT_TOKEN:
        return
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = json.dumps({"chat_id": ADMIN_CHAT_ID, "parse_mode": "HTML", "text": text}).encode("utf-8")
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=5)
    except Exception as e:
        log(f"Telegram notify error: {e}")

def read_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                parts = f.read().strip().split(":")
                return parts[0], int(parts[1]), int(parts[2])
        except Exception:
            pass
    return "up", 0, 0

def write_state(status, fail_count, recover_count):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        f.write(f"{status}:{fail_count}:{recover_count}")

def check_hy2_bridge():
    try:
        res = subprocess.run(
            [
                "curl", "-s", "-o", "/dev/null", "-w", "%{http_code}",
                "--socks5-hostname", "127.0.0.1:10888",
                "--connect-timeout", "4", "--max-time", "6",
                "https://www.google.com"
            ],
            stdout=subprocess.PIPE, text=True, timeout=8
        )
        code = res.stdout.strip()
        return code.startswith(("2", "3"))
    except Exception:
        return False

def switch_outbound_routing(target_tag):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT value FROM settings WHERE key = 'xrayTemplateConfig'")
    row = c.fetchone()
    if not row:
        conn.close()
        return False
    cfg = json.loads(row[0])
    changed = False
    
    outbounds = cfg.get("outbounds", [])
    has_olcrtc = any(ob.get("tag") == "olcrtc-germany" for ob in outbounds)
    if not has_olcrtc and target_tag == "olcrtc-germany":
        outbounds.append({
            "tag": "olcrtc-germany",
            "protocol": "socks",
            "settings": {"servers": [{"address": "127.0.0.1", "port": 10808}]}
        })
        changed = True

    for rule in cfg.get("routing", {}).get("rules", []):
        if rule.get("network") in ("tcp,udp", "tcp") and "inboundTag" not in rule and "port" not in rule and "domain" not in rule and "ip" not in rule:
            if rule.get("outboundTag") != target_tag:
                rule["outboundTag"] = target_tag
                changed = True
    if changed:
        c.execute("UPDATE settings SET value = ? WHERE key = 'xrayTemplateConfig'", (json.dumps(cfg),))
        conn.commit()
    conn.close()
    return changed

def check_traffic_limit():
    try:
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute("SELECT SUM(up + down) FROM client_traffics")
        res = c.fetchone()
        current_lifetime_bytes = res[0] if (res and res[0] is not None) else 0
        conn.close()

        now = datetime.datetime.now()
        current_period = f"{now.year}-{now.month}" if now.day >= 21 else f"{now.year}-{now.month-1 if now.month > 1 else 12}"

        last_recorded_period = ""
        last_alert_period = ""
        period_baseline_bytes = 0

        if os.path.exists(TRAFFIC_STATE_FILE):
            try:
                with open(TRAFFIC_STATE_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    last_recorded_period = data.get("last_period", "")
                    last_alert_period = data.get("last_alert_period", "")
                    period_baseline_bytes = data.get("period_baseline_bytes", 0)
            except Exception:
                pass

        if last_recorded_period and last_recorded_period != current_period:
            period_baseline_bytes = current_lifetime_bytes
            log(f"New billing period {current_period} started. Baseline={period_baseline_bytes} bytes.")

        period_bytes = max(0, current_lifetime_bytes - period_baseline_bytes)
        period_gb = period_bytes / (1024**3)
        period_tb = period_bytes / (1024**4)

        if period_bytes >= ALERT_THRESHOLD_BYTES and last_alert_period != current_period:
            msg = "\n".join([
                "⚠️ <b>Внимание: Высокий расход трафика на РУ-сервере (Selectel)</b>",
                "",
                f"📊 <b>Расход в текущем периоде:</b> {period_tb:.2f} ТБ ({period_gb:.1f} ГБ)",
                f"🎯 <b>Порог алерта:</b> {ALERT_THRESHOLD_TB:.1f} ТБ",
                f"🚫 <b>Лимит сервера:</b> {MAX_LIMIT_TB:.1f} ТБ",
                "📅 <b>Расчетный период до:</b> 21 числа следующего месяца"
            ])
            notify(msg)
            last_alert_period = current_period

        with open(TRAFFIC_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump({
                "last_period": current_period,
                "last_alert_period": last_alert_period,
                "period_baseline_bytes": period_baseline_bytes,
                "lifetime_bytes": current_lifetime_bytes,
                "period_bytes": period_bytes
            }, f)
    except Exception as e:
        log(f"Traffic check error: {e}")

def main():
    check_traffic_limit()

    status, fail_count, recover_count = read_state()
    is_healthy = check_hy2_bridge()

    if is_healthy:
        recover_count += 1
        fail_count = 0

        if status == "down" and recover_count >= RECOVER_THRESHOLD:
            log(f"RECOVERED: Hysteria 2 bridge is healthy again (successes: {recover_count})")
            switch_outbound_routing("hy2-germany")
            subprocess.run(["systemctl", "restart", "x-ui"])
            subprocess.run(["systemctl", "stop", "olcrtc-client.service"])
            log("Switched global routing back to hy2-germany. Stopped olcrtc-client.service.")
            status = "up"
            recover_count = 0
        elif status != "down":
            status = "up"
    else:
        fail_count += 1
        recover_count = 0

        if status == "up" and fail_count >= FAIL_THRESHOLD:
            log(f"FAILOVER: Hysteria 2 bridge failed (failures: {fail_count}). Starting olcRTC backup...")
            subprocess.run(["systemctl", "start", "olcrtc-client.service"])
            time.sleep(3)
            switch_outbound_routing("olcrtc-germany")
            subprocess.run(["systemctl", "restart", "x-ui"])
            log("Switched global routing to olcrtc-germany (olcRTC active)")
            
            msg = "\n".join([
                "🚨 <b>RU-Bridge: Сработал аварийный Failover!</b>",
                "",
                "❌ <b>Основной мост (Hysteria 2)</b> временно недоступен.",
                "🔄 <b>Действие:</b> Автоматически запущен аварийный видеомост <code>olcRTC (Jitsi)</code>.",
                "👤 Пользователи продолжают работать без обрыва соединения."
            ])
            notify(msg)
            status = "down"
            fail_count = 0

    write_state(status, fail_count, recover_count)

if __name__ == "__main__":
    main()
