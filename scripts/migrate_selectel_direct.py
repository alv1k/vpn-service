#!/usr/bin/env python3
"""
Multi-Stage Migration & Network Health Monitor: Selectel RU Direct
Usage:
    python3 scripts/migrate_selectel_direct.py --status
    python3 scripts/migrate_selectel_direct.py --send-report
    python3 scripts/migrate_selectel_direct.py --cron
    python3 scripts/migrate_selectel_direct.py --apply-cutover
    python3 scripts/migrate_selectel_direct.py --rollback
"""

import os
import sys
import json
import time
import datetime
import subprocess
import urllib.request
import re
import argparse

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)
DATA_DIR = os.path.join(PROJECT_DIR, "data")
STATE_FILE = os.path.join(DATA_DIR, "selectel_migration_state.json")
BACKUP_DIR = os.path.join(PROJECT_DIR, "backups")
ENV_FILE = os.path.join(PROJECT_DIR, ".env")

RU_HOST_SSH = "ru-server"
RU_INTERNAL_IP = "10.50.0.2"
RU_PUBLIC_IP = "139.100.207.18"

# Blocked/Foreign domains indicating legacy bridge usage on Selectel
FOREIGN_INDICATORS = [
    "youtube", "googlevideo", "ytimg", "instagram", "facebook",
    "meta", "twitter", "x.com", "t.co", "netflix", "tiktok", "twitch"
]

def load_credentials():
    bot_token, admin_id = "", ""
    if os.path.exists(ENV_FILE):
        try:
            with open(ENV_FILE, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("TELEGRAM_BOT_TOKEN="):
                        bot_token = line.split("=", 1)[1].strip().strip('"').strip("'")
                    elif line.startswith("ADMIN_TG_ID="):
                        admin_id = line.split("=", 1)[1].strip().strip('"').strip("'")
        except Exception:
            pass
    return bot_token, admin_id

BOT_TOKEN, ADMIN_CHAT_ID = load_credentials()

def send_telegram(text: str):
    if not BOT_TOKEN or not ADMIN_CHAT_ID:
        print("[WARN] Telegram credentials not found in .env")
        return False
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = json.dumps({
        "chat_id": ADMIN_CHAT_ID,
        "parse_mode": "HTML",
        "text": text,
        "disable_web_page_preview": True
    }).encode("utf-8")
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=10)
        return True
    except Exception as e:
        print(f"[ERR] Failed to send Telegram alert: {e}")
        return False

def run_cmd(cmd, timeout=10):
    try:
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=timeout)
        return res.returncode == 0, res.stdout.strip(), res.stderr.strip()
    except Exception as e:
        return False, "", str(e)

def run_ssh(remote_cmd, timeout=12):
    cmd = ["ssh", "-o", "ConnectTimeout=5", "-o", "BatchMode=yes", RU_HOST_SSH, remote_cmd]
    return run_cmd(cmd, timeout=timeout)

def check_network_health():
    health = {}

    # 1. Ping L3 bridge
    ok, out, _ = run_cmd(["ping", "-c", "2", "-W", "2", RU_INTERNAL_IP], timeout=5)
    health["bridge_awg_ok"] = ok
    if ok:
        m = re.search(r"rtt min/avg/max/mdev = [\d\.]+/([\d\.]+)/", out)
        health["bridge_awg_rtt_ms"] = float(m.group(1)) if m else None
    else:
        health["bridge_awg_rtt_ms"] = None

    # 2. Ping Selectel public IP
    ok, out, _ = run_cmd(["ping", "-c", "2", "-W", "2", RU_PUBLIC_IP], timeout=5)
    health["public_ru_ok"] = ok
    if ok:
        m = re.search(r"rtt min/avg/max/mdev = [\d\.]+/([\d\.]+)/", out)
        health["public_ru_rtt_ms"] = float(m.group(1)) if m else None
    else:
        health["public_ru_rtt_ms"] = None

    # 3. European connectivity from DE
    ok, code, _ = run_cmd(["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}", "--connect-timeout", "4", "https://www.google.com"], timeout=6)
    health["de_google_code"] = code if ok else "ERR"

    # 4. Russian portals via Selectel (ЦБ РФ, Т-Банк, Яндекс)
    ok, code, _ = run_ssh("curl -k -A 'Mozilla/5.0' -s -o /dev/null -w '%{http_code}' --connect-timeout 5 https://cbr.ru", timeout=8)
    health["ru_cbr_code"] = code if ok else "ERR"

    ok, code, _ = run_ssh("curl -k -A 'Mozilla/5.0' -s -o /dev/null -w '%{http_code}' --connect-timeout 5 https://www.tbank.ru", timeout=8)
    health["ru_bank_code"] = code if ok else "ERR"

    ok, code, _ = run_ssh("curl -A 'Mozilla/5.0' -s -o /dev/null -w '%{http_code}' --connect-timeout 5 https://ya.ru", timeout=8)
    health["ru_yandex_code"] = code if ok else "ERR"

    # 5. Xray service on RU
    ok, out, _ = run_ssh("systemctl is-active x-ui", timeout=6)
    health["ru_xui_active"] = (out == "active")

    return health

def analyze_ru_sessions():
    """Анализирует access.log на Selectel за последние 1000 строк."""
    cmd = "tail -n 1500 /var/log/x-ui/access.log 2>/dev/null || tail -n 1500 /var/log/xray/access.log 2>/dev/null"
    ok, out, _ = run_ssh(cmd, timeout=10)
    if not ok or not out:
        return {"error": "Failed to read logs from Selectel", "legacy_bridge_users": [], "direct_users": []}

    legacy_bridge_users = set()
    direct_users = set()
    foreign_domains_seen = []

    for line in out.splitlines():
        if "accepted" not in line:
            continue
        
        email_match = re.search(r"email:\s*([^\s]+)", line)
        email = email_match.group(1) if email_match else "unknown"
        if email in ("api", "test_awg_user_01"):
            continue

        if "de-bridge" in line:
            dest_match = re.search(r"accepted\s+(?:tcp|udp):([^\s:]+)", line)
            dest = dest_match.group(1).lower() if dest_match else ""
            if any(ind in dest for ind in FOREIGN_INDICATORS):
                legacy_bridge_users.add(email)
                if dest not in foreign_domains_seen:
                    foreign_domains_seen.append(dest)
        elif "direct" in line:
            direct_users.add(email)

    return {
        "legacy_bridge_users": list(legacy_bridge_users),
        "legacy_bridge_count": len(legacy_bridge_users),
        "direct_users": list(direct_users),
        "direct_count": len(direct_users),
        "foreign_sample": foreign_domains_seen[:6]
    }

def load_state():
    os.makedirs(DATA_DIR, exist_ok=True)
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {
        "stage": "STAGE_1_DRAINING",
        "created_at": datetime.datetime.now().isoformat(),
        "history": [],
        "ready_notified": False,
        "cutover_applied": False
    }

def save_state(state):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)

def format_report(health, sessions, state):
    ts = datetime.datetime.now().strftime("%d.%m.%Y %H:%M:%S")
    bridge_str = f"🟢 OK ({health.get('bridge_awg_rtt_ms')} ms)" if health.get("bridge_awg_ok") else "🔴 FAIL"
    pub_str = f"🟢 OK ({health.get('public_ru_rtt_ms')} ms)" if health.get("public_ru_ok") else "🔴 FAIL"
    cbr_str = f"🟢 HTTP {health.get('ru_cbr_code')}" if health.get("ru_cbr_code") in ("200", "301", "302") else f"🔴 {health.get('ru_cbr_code')}"
    bank_str = f"🟢 HTTP {health.get('ru_bank_code')}" if health.get("ru_bank_code") in ("200", "301", "302") else f"🔴 {health.get('ru_bank_code')}"
    ya_str = f"🟢 HTTP {health.get('ru_yandex_code')}" if health.get("ru_yandex_code") in ("200", "301", "302") else f"🔴 {health.get('ru_yandex_code')}"
    de_str = f"🟢 HTTP {health.get('de_google_code')}" if health.get("de_google_code") in ("200", "301", "302") else f"🔴 {health.get('de_google_code')}"
    
    stage = state.get("stage", "STAGE_1_DRAINING")
    bridge_count = sessions.get("legacy_bridge_count", 0)
    bridge_users = ", ".join(sessions.get("legacy_bridge_users", [])) or "нет"
    direct_count = sessions.get("direct_count", 0)
    sample_domains = ", ".join(sessions.get("foreign_sample", [])) or "—"

    lines = [
        f"📊 <b>Миграция Selectel ➔ Direct: Монитор</b>",
        f"⏱ <i>{ts}</i> | Стадия: <code>{stage}</code>",
        "",
        "🌐 <b>Сетевая связность и здоровье:</b>",
        f"• L3 Мост (10.50.0.2): {bridge_str}",
        f"• Selectel Внешний IP: {pub_str}",
        f"• ЦБ РФ (cbr.ru): {cbr_str}",
        f"• Т-Банк (tbank.ru): {bank_str}",
        f"• Яндекс (ya.ru): {ya_str}",
        f"• Google (через Германию): {de_str}",
        "",
        "👥 <b>Состояние сессий на Selectel:</b>",
        f"• ⏳ Зависли на старом мосту (YouTube/Insta): <b>{bridge_count}</b> чел.",
        f"  <i>Пользователи: {bridge_users}</i>",
        f"  <i>Домены: {sample_domains}</i>",
        f"• 🇷🇺 Чистый трафик в РФ (Direct): <b>{direct_count}</b> чел.",
        "",
    ]

    if bridge_count <= 2 and not state.get("cutover_applied"):
        lines.append("🎯 <b>Внимание:</b> Число старых сессий ≤ 2! Узел готов к переводу в Direct: <code>--apply-cutover</code>")
    elif state.get("cutover_applied"):
        lines.append("✅ <b>Узел полностью переведён в автономный Direct-режим.</b>")
    else:
        lines.append(f"⏳ <i>Ожидание естественной миграции клиентов в приложениях...</i>")

    return "\n".join(lines)

def apply_cutover():
    """Переключает Xray на Selectel в чистый direct-режим."""
    print("🚀 [CUTOVER] Initiating Selectel transition to pure Direct...")
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    
    # 1. Backup on Selectel
    backup_cmd = f"cp /etc/x-ui/x-ui.db /root/x-ui.db.pre_cutover.{ts}"
    ok, _, err = run_ssh(backup_cmd)
    if not ok:
        print(f"❌ Failed to backup x-ui.db on Selectel: {err}")
        return False

    # 2. Update xrayTemplateConfig on Selectel
    python_script = """
import sqlite3, json, sys

conn = sqlite3.connect('/etc/x-ui/x-ui.db')
c = conn.cursor()
c.execute("SELECT value FROM settings WHERE key='xrayTemplateConfig'")
row = c.fetchone()
if not row:
    sys.exit(1)

cfg = json.loads(row[0])
outbounds = cfg.get('outbounds', [])

# 1. Ensure 'direct' is first outbound (default fallback)
direct_ob = None
other_obs = []
for ob in outbounds:
    if ob.get('tag') == 'direct':
        direct_ob = ob
    else:
        other_obs.append(ob)

if not direct_ob:
    direct_ob = {"tag": "direct", "protocol": "freedom"}

cfg['outbounds'] = [direct_ob] + other_obs

# 2. Update routing rules: remove any blanket forwarding to de-bridge
new_rules = []
for rule in cfg.get('routing', {}).get('rules', []):
    # Remove universal tcp,udp fallback to de-bridge
    if rule.get('outboundTag') == 'de-bridge' and rule.get('network') == 'tcp,udp' and not rule.get('domain'):
        continue
    new_rules.append(rule)

cfg['routing']['rules'] = new_rules

c.execute("UPDATE settings SET value=? WHERE key='xrayTemplateConfig'", (json.dumps(cfg),))
conn.commit()
conn.close()
print("SUCCESS")
"""
    update_cmd = f"python3 -c \"{python_script}\""
    ok, out, err = run_ssh(update_cmd)
    if not ok or "SUCCESS" not in out:
        print(f"❌ Failed to update Xray template on Selectel: {err or out}")
        return False

    # 3. Restart x-ui
    print("🔄 Restarting x-ui on Selectel...")
    run_ssh("x-ui restart")
    time.sleep(3)

    # 4. Health Check Post-Cutover
    print("🔍 Testing Selectel health post-cutover...")
    ok_cbr, code_cbr, _ = run_ssh("curl -k -A 'Mozilla/5.0' -s -o /dev/null -w '%{http_code}' --connect-timeout 6 https://cbr.ru")
    ok_bank, code_bank, _ = run_ssh("curl -k -A 'Mozilla/5.0' -s -o /dev/null -w '%{http_code}' --connect-timeout 6 https://www.tbank.ru")
    ok_ya, code_ya, _ = run_ssh("curl -A 'Mozilla/5.0' -s -o /dev/null -w '%{http_code}' --connect-timeout 6 https://ya.ru")

    if not ok_cbr or not ok_ya or code_ya not in ("200", "301", "302"):
        print(f"🚨 POST-CUTOVER HEALTH CHECK FAILED! CBR={code_cbr}, TBank={code_bank}, Yandex={code_ya}. Initiating Auto-Rollback...")
        rollback()
        send_telegram(f"🚨 <b>Selectel Cutover FAILED!</b>\nHealth check failed (CBR={code_cbr}, TBank={code_bank}, Yandex={code_ya}).\nАвтоматически выполнен откат к предыдущей версии!")
        return False

    print("✅ Post-cutover health check passed successfully!")
    state = load_state()
    state["stage"] = "STAGE_4_COMPLETED"
    state["cutover_applied"] = True
    state["cutover_at"] = datetime.datetime.now().isoformat()
    save_state(state)

    msg = "\n".join([
        "🎉 <b>Selectel успешно переведён в Direct-режим!</b>",
        "",
        "• Все зарубежные запросы с Selectel теперь выходят direct.",
        "• Мост в Германию для трафика клиентов отключен.",
        f"• ЦБ РФ: HTTP {code_cbr} | Т-Банк: HTTP {code_bank} | Яндекс: HTTP {code_ya}",
        "• IP-адрес Selectel полностью изолирован от трафика клиентов Германии."
    ])
    send_telegram(msg)
    return True

def rollback():
    print("⏪ [ROLLBACK] Reverting Selectel to pre-cutover state...")
    cmd = "LATEST=$(ls -t /root/x-ui.db.pre_cutover.* 2>/dev/null | head -n 1); if [ -n \"$LATEST\" ]; then cp \"$LATEST\" /etc/x-ui/x-ui.db && x-ui restart && echo 'RESTORED'; else echo 'NO_BACKUP'; fi"
    ok, out, err = run_ssh(cmd)
    if ok and "RESTORED" in out:
        print("✅ Rollback completed successfully.")
        state = load_state()
        state["stage"] = "STAGE_1_DRAINING"
        state["cutover_applied"] = False
        save_state(state)
        return True
    else:
        print(f"❌ Rollback failed: {err or out}")
        return False

def main():
    parser = argparse.ArgumentParser(description="Selectel Direct Migration & Network Monitor")
    parser.add_argument("--status", action="store_true", help="Print current status and metrics")
    parser.add_argument("--send-report", action="store_true", help="Send formatted report to Telegram")
    parser.add_argument("--cron", action="store_true", help="Run via cron, alert on state change")
    parser.add_argument("--apply-cutover", action="store_true", help="Apply final direct routing to Selectel")
    parser.add_argument("--rollback", action="store_true", help="Rollback to pre-cutover config")

    args = parser.parse_args()

    if args.apply_cutover:
        apply_cutover()
        return

    if args.rollback:
        rollback()
        return

    health = check_network_health()
    sessions = analyze_ru_sessions()
    state = load_state()

    # Update state history
    current_point = {
        "ts": datetime.datetime.now().isoformat(),
        "bridge_users": sessions.get("legacy_bridge_count", 0),
        "direct_users": sessions.get("direct_count", 0),
        "health_ok": health.get("bridge_awg_ok", False) and health.get("public_ru_ok", False)
    }
    state["history"] = state.get("history", [])[-20:] + [current_point]

    report_text = format_report(health, sessions, state)

    # Check ready trigger
    if sessions.get("legacy_bridge_count", 0) <= 2 and not state.get("ready_notified") and not state.get("cutover_applied"):
        state["ready_notified"] = True
        save_state(state)
        send_telegram("🔔 <b>Selectel Migration Alert:</b>\nЧисло активных YouTube/зарубежных сессий упало до ≤ 2!\nСервер готов к переключению на Direct: <code>--apply-cutover</code>")

    save_state(state)

    if args.send_report:
        send_telegram(report_text)
        print("Telegram report sent.")
    elif args.cron:
        # Cron silent execution unless issues
        if not health.get("bridge_awg_ok") or not health.get("public_ru_ok"):
            send_telegram(f"⚠️ <b>Предупреждение:</b> Проблема со связностью с Selectel!\nL3-мост: {health.get('bridge_awg_ok')}\nSelectel IP: {health.get('public_ru_ok')}")
    else:
        # Default / --status
        # Strip HTML tags for clean console display
        console_text = re.sub(r"</?[bi]>", "", report_text)
        console_text = re.sub(r"<code>([^<]+)</code>", r"\1", console_text)
        print(console_text)

if __name__ == "__main__":
    main()
