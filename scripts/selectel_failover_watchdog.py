#!/usr/bin/env python3
"""
Selectel SPB Egress Watchdog (3-Level Failover Sentinel)
Architecture:
Level 1 (Primary):    de-bridge     (Direct Hetzner DE, port 10811)
Level 2 (Failover 1): de-via-nl     (Transit via NL to DE xHTTP, port 10813, keeps DE exit IP)
Level 3 (Failover 2): out-nl-direct (Direct Amsterdam NL exit, port 10812, cold standby)
Safeguards:
1. TTFB <= 0.80s strict check on Primary URL for direct DE recovery (defeats Linux 1s/3s SYN retransmission mask).
2. Continuous health monitoring of de-via-nl (10813) in Level 2 with auto-escalation to Level 3.
3. Read-only SQLite traffic accounting for Selectel 5 TB limit.
4. Robust timeouts and clean state resumption on restart.
"""
import os
import sys
import time
import json
import sqlite3
import datetime
import subprocess

XRAY_BIN = "/usr/local/x-ui/bin/xray-linux-amd64"
XRAY_API_SERVER = "127.0.0.1:62789"
BALANCER_TAG = "eu-failover"

TAG_PRIMARY = "de-bridge"
TAG_FAILOVER_1 = "de-via-nl"
TAG_FAILOVER_2 = "out-nl-direct"

PROBE_PORT_DE = 10811
PROBE_PORT_DE_VIA_NL = 10813
PROBE_PORT_NL = 10812
PROBE_PORT_OLCRTC = 10808

URL_PRIMARY = "https://speed.cloudflare.com/__down?bytes=131072"
URL_SECONDARY = "https://344988.snk.wtf/ruleset/ruleset-geoip-ru.json"

DEFAULT_MIN_BYTES = 51200
CHECK_INTERVAL_NORMAL = 30
RETRY_INTERVAL_FAIL = 15
FAIL_THRESHOLD = 3
RECOVER_THRESHOLD = 10

TTFB_MAX_DE_RECOVERY = 0.80
TTFB_MAX_TRANSIT = 2.00

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
ADMIN_TG_ID = os.getenv("ADMIN_TG_ID", "364224373")
ADMIN_CHAT_ID = ADMIN_TG_ID

DB_FILE = "/etc/x-ui/x-ui.db"
TRAFFIC_STATE_FILE = "/root/data/ru_traffic_state.json"
MAX_LIMIT_TB = 5.0
THRESHOLDS_TB = [4.0, 4.5, 4.75]

LOG_FILE = "/root/logs/selectel-watchdog.log"
STATE_FILE = "/root/data/selectel_watchdog_state.json"

def log(msg):
    os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass

def get_min_bytes(port=None):
    if port == PROBE_PORT_DE:
        val = os.environ.get("WATCHDOG_DE_MIN_BYTES")
        if val:
            try:
                return int(val)
            except Exception:
                pass
    try:
        return int(os.environ.get("WATCHDOG_MIN_BYTES", DEFAULT_MIN_BYTES))
    except Exception:
        return DEFAULT_MIN_BYTES

def notify(text):
    if not BOT_TOKEN:
        return False
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = json.dumps({"chat_id": ADMIN_CHAT_ID, "parse_mode": "HTML", "text": text})

    proxies = [
        ["--socks5-hostname", f"127.0.0.1:{PROBE_PORT_NL}"],
        ["--socks5-hostname", f"127.0.0.1:{PROBE_PORT_DE_VIA_NL}"],
        ["--socks5-hostname", f"127.0.0.1:{PROBE_PORT_DE}"],
        ["--socks5-hostname", f"127.0.0.1:{PROBE_PORT_OLCRTC}"],
        []
    ]
    for p in proxies:
        cmd = ["curl", "-s", "-m", "5", "-X", "POST", "-H", "Content-Type: application/json", "-d", payload, url]
        if p:
            cmd = [cmd[0]] + p + cmd[1:]
        try:
            res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, timeout=7)
            if res.returncode == 0 and '"ok":true' in res.stdout:
                log(f"Telegram notification sent successfully (via {p[1] if p else 'direct'}).")
                return True
        except Exception:
            continue
    log("Telegram notification failed via all proxy and direct attempts.")
    return False

def check_channel(port, strict_ttfb=None, single_url=False):
    min_bytes = get_min_bytes(port)
    urls = [("Cloudflare", URL_PRIMARY)] if single_url else [("Cloudflare", URL_PRIMARY), ("Direct-DE", URL_SECONDARY)]

    for name, target_url in urls:
        cmd = [
            "curl", "-s", "-o", "/dev/null",
            "-w", "%{http_code}:%{size_download}:%{time_total}:%{time_starttransfer}",
            "--socks5-hostname", f"127.0.0.1:{port}",
            "--connect-timeout", "6",
            "--max-time", "10",
            target_url
        ]
        try:
            res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, timeout=12)
            if res.returncode == 0:
                parts = res.stdout.strip().split(":")
                if len(parts) >= 4:
                    http_code = int(parts[0])
                    download_bytes = int(float(parts[1]))
                    time_total = float(parts[2])
                    ttfb = float(parts[3])

                    if strict_ttfb is not None and ttfb > strict_ttfb:
                        log(f"[SYN/TTFB SLOW] Port {port} {name}: TTFB={ttfb:.3f}s > {strict_ttfb:.2f}s threshold (likely SYN drop/retransmission)")
                        return False, f"TTFB slow ({ttfb:.3f}s > {strict_ttfb:.2f}s)"

                    if http_code == 200 and download_bytes >= min_bytes:
                        return True, f"{name}: {download_bytes} bytes in {time_total:.2f}s (TTFB {ttfb:.3f}s)"
                    elif http_code == 200:
                        log(f"[TRUNCATED] Port {port} {name} returned HTTP 200 but only {download_bytes} bytes (< {min_bytes})")
        except Exception as e:
            log(f"Port {port} probe {name} error: {e}")

    return False, "Failed all URL checks"

def get_current_override():
    try:
        cmd = [XRAY_BIN, "api", "bi", f"--server={XRAY_API_SERVER}", BALANCER_TAG]
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, timeout=5)
        out = res.stdout
        if "- Selecting Override:" in out:
            override_section = out.split("- Selecting Override:")[1].split("- Selects:")[0]
            if TAG_FAILOVER_1 in override_section:
                return TAG_FAILOVER_1
            if TAG_FAILOVER_2 in override_section:
                return TAG_FAILOVER_2
    except Exception as e:
        log(f"get_current_override error: {e}")
    return None

def set_balancer_override(tag):
    try:
        if tag:
            cmd = [XRAY_BIN, "api", "bo", f"--server={XRAY_API_SERVER}", "-b", BALANCER_TAG, tag]
            log(f"Setting Xray balancer override: {BALANCER_TAG} -> {tag}")
        else:
            cmd = [XRAY_BIN, "api", "bo", f"--server={XRAY_API_SERVER}", "-b", BALANCER_TAG, "-r"]
            log(f"Resetting Xray balancer override: {BALANCER_TAG} -> default (cleared)")
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=5)
        if res.returncode == 0:
            return True
        log(f"Balancer override command failed: {res.stderr}")
    except Exception as e:
        log(f"set_balancer_override error: {e}")
    return False

def check_traffic_limit():
    try:
        uri = f"file:{DB_FILE}?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
        c = conn.cursor()
        c.execute("SELECT sum(up) + sum(down) FROM inbounds")
        row = c.fetchone()
        inbounds_traffic = row[0] if row and row[0] else 0
        c.execute("SELECT sum(up) + sum(down) FROM client_traffics")
        row_ct = c.fetchone()
        clients_traffic = row_ct[0] if row_ct and row_ct[0] else 0
        conn.close()

        total_bytes = max(inbounds_traffic, clients_traffic)
        total_tb = total_bytes / (1024 ** 4)

        notified_state = {}
        if os.path.exists(TRAFFIC_STATE_FILE):
            try:
                with open(TRAFFIC_STATE_FILE, "r") as f:
                    notified_state = json.load(f)
            except Exception:
                pass

        for threshold in THRESHOLDS_TB:
            th_str = str(threshold)
            if total_tb >= threshold and not notified_state.get(th_str):
                pct = int((threshold / MAX_LIMIT_TB) * 100)
                msg = "\n".join([
                    f"⚠️ <b>Предупреждение: Трафик Selectel SPB достиг {pct}%!</b>",
                    "",
                    f"📊 Использовано: <b>{total_tb:.2f} ТБ</b> из <b>{MAX_LIMIT_TB} ТБ</b>.",
                    "ℹ️ При превышении 5 ТБ сервер начнет тарифицироваться по 90 руб/ТБ.",
                    "Проверьте распределение нагрузки между нодами."
                ])
                if notify(msg):
                    notified_state[th_str] = True
                    os.makedirs(os.path.dirname(TRAFFIC_STATE_FILE), exist_ok=True)
                    with open(TRAFFIC_STATE_FILE, "w") as f:
                        json.dump(notified_state, f)
    except Exception as e:
        log(f"check_traffic_limit error: {e}")

def stop_olcrtc_if_running():
    try:
        res = subprocess.run(["systemctl", "is-active", "olcrtc-client.service"], stdout=subprocess.PIPE, text=True)
        if res.stdout.strip() == "active":
            log("Stopping olcrtc-client.service...")
            subprocess.run(["systemctl", "stop", "olcrtc-client.service"], check=False)
    except Exception as e:
        log(f"stop_olcrtc error: {e}")

def start_olcrtc_if_needed():
    try:
        res = subprocess.run(["systemctl", "is-active", "olcrtc-client.service"], stdout=subprocess.PIPE, text=True)
        if res.stdout.strip() != "active":
            log("Starting olcrtc-client.service as emergency failover...")
            subprocess.run(["systemctl", "start", "olcrtc-client.service"], check=False)
    except Exception as e:
        log(f"start_olcrtc error: {e}")

def main():
    log("=== Selectel 3-Level Failover Watchdog Starting ===")

    current_override = get_current_override()
    if current_override == TAG_FAILOVER_1:
        state = "FAILOVER_DE_VIA_NL"
        log(f"Startup check: Active override '{TAG_FAILOVER_1}' detected. Resuming in FAILOVER_DE_VIA_NL state.")
    elif current_override == TAG_FAILOVER_2:
        state = "FAILOVER_NL"
        log(f"Startup check: Active override '{TAG_FAILOVER_2}' detected. Resuming in FAILOVER_NL state.")
    else:
        state = "PRIMARY_DE"
        log("Startup check: No override active. Initializing in PRIMARY_DE state.")
        stop_olcrtc_if_running()

    fail_count = 0
    recover_count = 0
    transit_fail_count = 0
    pending_alert = None
    last_traffic_check = 0

    while True:
        try:
            now_ts = time.time()
            if now_ts - last_traffic_check > 1800:
                check_traffic_limit()
                last_traffic_check = now_ts

            if state == "PRIMARY_DE":
                de_ok, de_msg = check_channel(PROBE_PORT_DE)

                if de_ok:
                    fail_count = 0
                    log(f"[HEALTHY] Direct DE passed ({de_msg}). State: PRIMARY_DE")
                    if get_current_override():
                        log("Warning: Override lingered while state was PRIMARY_DE. Clearing override.")
                        set_balancer_override(None)
                    time.sleep(CHECK_INTERVAL_NORMAL)
                else:
                    fail_count += 1
                    log(f"[WARN] Direct DE health check failed ({fail_count}/{FAIL_THRESHOLD}): {de_msg}")
                    if fail_count < FAIL_THRESHOLD:
                        time.sleep(RETRY_INTERVAL_FAIL)
                    else:
                        log(f"[TRIGGER] Direct DE failed {FAIL_THRESHOLD} times! Pre-checking Level 2 (de-via-nl)...")
                        transit_ok, transit_msg = check_channel(PROBE_PORT_DE_VIA_NL, strict_ttfb=TTFB_MAX_TRANSIT)

                        if transit_ok:
                            log(f"[FAILOVER LEVEL 2] de-via-nl is healthy ({transit_msg}). Switching to de-via-nl!")
                            set_balancer_override(TAG_FAILOVER_1)
                            state = "FAILOVER_DE_VIA_NL"
                            fail_count = 0
                            recover_count = 0
                            transit_fail_count = 0

                            msg = "\n".join([
                                "🛡️ <b>Selectel SPB: Активирован Failover Level 2 (de-via-nl)</b>",
                                "",
                                f"⚠️ <b>Прямой DE:</b> {de_msg} (фильтрация ТСПУ).",
                                "🔄 <b>Маршрут:</b> <code>Selectel ➔ NL (Транзит) ➔ Hetzner DE</code>.",
                                "🇩🇪 <b>Выходной IP:</b> Сохранён немецкий IP (Hetzner DE).",
                                "🇳🇱 Гео-профиль Нидерландов остаётся чистым (0% веб-нагрузки на NL)."
                            ])
                            if not notify(msg):
                                pending_alert = msg
                            time.sleep(CHECK_INTERVAL_NORMAL)
                        else:
                            log(f"[WARN] de-via-nl probe failed: {transit_msg}. Pre-checking Level 3 (out-nl-direct)...")
                            nl_ok, nl_msg = check_channel(PROBE_PORT_NL)
                            if nl_ok:
                                log(f"[FAILOVER LEVEL 3] out-nl-direct healthy ({nl_msg}). Switching to NL Cold Standby!")
                                set_balancer_override(TAG_FAILOVER_2)
                                state = "FAILOVER_NL"
                                fail_count = 0
                                recover_count = 0

                                msg = "\n".join([
                                    "🚨 <b>Selectel SPB: Активирован Failover Level 3 (out-nl-direct)</b>",
                                    "",
                                    f"❌ <b>Hetzner DE:</b> Недоступен ни напрямую, ни через транзит.",
                                    f"🟢 <b>Amsterdam NL:</b> Здоров ({nl_msg}).",
                                    "🔄 <b>Действие:</b> Весь зарубежный трафик переведён на узел Нидерландов."
                                ])
                                if not notify(msg):
                                    pending_alert = msg
                                time.sleep(CHECK_INTERVAL_NORMAL)
                            else:
                                log("[CRITICAL] Both DE and NL probes failed! Triggering emergency olcRTC...")
                                state = "BOTH_DOWN"
                                fail_count = 0
                                recover_count = 0
                                start_olcrtc_if_needed()

                                msg = "\n".join([
                                    "🚨 <b>КРИТИЧЕСКАЯ АВАРИЯ: Все каналы в Европу недоступны!</b>",
                                    "",
                                    "❌ Прямой DE, транзитный DE и узел NL провалили проверку.",
                                    "🔄 <b>Действие:</b> Запущен аварийный WebRTC туннель <code>olcRTC</code>."
                                ])
                                if not notify(msg):
                                    pending_alert = msg
                                time.sleep(CHECK_INTERVAL_NORMAL)

            elif state == "FAILOVER_DE_VIA_NL":
                if get_current_override() != TAG_FAILOVER_1:
                    log(f"Notice: Enforcing override to {TAG_FAILOVER_1} in Xray...")
                    set_balancer_override(TAG_FAILOVER_1)

                # 1. Monitor transit channel health
                transit_ok, transit_msg = check_channel(PROBE_PORT_DE_VIA_NL, strict_ttfb=TTFB_MAX_TRANSIT)
                if not transit_ok:
                    transit_fail_count += 1
                    log(f"[WARN] de-via-nl probe failed ({transit_fail_count}/{FAIL_THRESHOLD}): {transit_msg}")
                    if transit_fail_count >= FAIL_THRESHOLD:
                        log("de-via-nl failed 3 times! Escalating to Level 3 (out-nl-direct)...")
                        nl_ok, nl_msg = check_channel(PROBE_PORT_NL)
                        if nl_ok:
                            set_balancer_override(TAG_FAILOVER_2)
                            state = "FAILOVER_NL"
                            transit_fail_count = 0
                            recover_count = 0
                            msg = "🚨 <b>Selectel SPB: Эскалация на Level 3 (out-nl-direct) — транзит DE недоступен!</b>"
                            notify(msg)
                            time.sleep(CHECK_INTERVAL_NORMAL)
                            continue
                else:
                    transit_fail_count = 0

                # 2. Check Direct DE recovery with STRICT TTFB <= 0.80s on SINGLE URL
                de_ok, de_msg = check_channel(PROBE_PORT_DE, strict_ttfb=TTFB_MAX_DE_RECOVERY, single_url=True)

                if de_ok:
                    recover_count += 1
                    log(f"[RECOVERY PROBE] Direct DE passed strict TTFB check ({recover_count}/{RECOVER_THRESHOLD}): {de_msg}")
                    if recover_count >= RECOVER_THRESHOLD:
                        log(f"[RECOVERED] Direct DE passed {RECOVER_THRESHOLD} strict checks! Reverting to {TAG_PRIMARY}...")
                        set_balancer_override(None)
                        state = "PRIMARY_DE"
                        recover_count = 0
                        fail_count = 0
                        stop_olcrtc_if_running()

                        msg = "\n".join([
                            "✅ <b>Selectel SPB: Прямой канал Hetzner DE полностью восстановился!</b>",
                            "",
                            f"🟢 <b>Прямой DE:</b> Стабилен ({RECOVER_THRESHOLD} строгих тестов с TTFB < {TTFB_MAX_DE_RECOVERY}с подряд, потерь SYN 0%).",
                            f"🔄 <b>Действие:</b> Балансировщик Xray возвращен на основной прямой канал <code>{TAG_PRIMARY}</code>.",
                            "🧊 Каскадный транзит деактивирован."
                        ])
                        notify(msg)
                else:
                    if recover_count > 0:
                        log(f"[RECOVERY RESET] Direct DE strict check failed after {recover_count} successes: {de_msg}. Resetting counter.")
                    recover_count = 0

                time.sleep(CHECK_INTERVAL_NORMAL)

            elif state == "FAILOVER_NL":
                if get_current_override() != TAG_FAILOVER_2:
                    log(f"Notice: Enforcing override to {TAG_FAILOVER_2} in Xray...")
                    set_balancer_override(TAG_FAILOVER_2)

                # Check if de-via-nl has recovered to de-escalate and save NL clean IP
                transit_ok, transit_msg = check_channel(PROBE_PORT_DE_VIA_NL, strict_ttfb=TTFB_MAX_TRANSIT)
                if transit_ok:
                    log(f"[DE-ESCALATION] de-via-nl is healthy ({transit_msg}). De-escalating from NL to de-via-nl...")
                    set_balancer_override(TAG_FAILOVER_1)
                    state = "FAILOVER_DE_VIA_NL"
                    transit_fail_count = 0
                    recover_count = 0
                    msg = "🛡️ <b>Selectel SPB: Деэскалация с Level 3 на Level 2 (de-via-nl). Чистый профиль NL сохранён.</b>"
                    notify(msg)
                    time.sleep(CHECK_INTERVAL_NORMAL)
                    continue

                # Check direct DE recovery
                de_ok, de_msg = check_channel(PROBE_PORT_DE, strict_ttfb=TTFB_MAX_DE_RECOVERY, single_url=True)
                if de_ok:
                    recover_count += 1
                    if recover_count >= RECOVER_THRESHOLD:
                        set_balancer_override(None)
                        state = "PRIMARY_DE"
                        recover_count = 0
                        stop_olcrtc_if_running()
                        msg = "✅ <b>Selectel SPB: Прямой канал Hetzner DE восстановился из Cold Standby!</b>"
                        notify(msg)
                else:
                    recover_count = 0

                time.sleep(CHECK_INTERVAL_NORMAL)

            elif state == "BOTH_DOWN":
                if pending_alert:
                    if notify(pending_alert):
                        pending_alert = None

                transit_ok, _ = check_channel(PROBE_PORT_DE_VIA_NL, strict_ttfb=TTFB_MAX_TRANSIT)
                nl_ok, _ = check_channel(PROBE_PORT_NL)
                de_ok, _ = check_channel(PROBE_PORT_DE, strict_ttfb=TTFB_MAX_DE_RECOVERY, single_url=True)

                if de_ok:
                    set_balancer_override(None)
                    stop_olcrtc_if_running()
                    state = "PRIMARY_DE"
                    notify("✅ <b>Selectel SPB: Прямой DE восстановился после критической аварии!</b>")
                elif transit_ok:
                    set_balancer_override(TAG_FAILOVER_1)
                    stop_olcrtc_if_running()
                    state = "FAILOVER_DE_VIA_NL"
                    notify("🛡️ <b>Selectel SPB: de-via-nl восстановился после аварии!</b>")
                elif nl_ok:
                    set_balancer_override(TAG_FAILOVER_2)
                    stop_olcrtc_if_running()
                    state = "FAILOVER_NL"
                    notify("🔄 <b>Selectel SPB: NL восстановился после аварии.</b>")

                time.sleep(CHECK_INTERVAL_NORMAL)

        except Exception as e:
            log(f"Unhandled loop error: {e}")
            time.sleep(10)

if __name__ == "__main__":
    main()
