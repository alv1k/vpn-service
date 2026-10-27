#!/usr/bin/env python3
"""
Inbound & Operator Switch Monitor for VPN Service.
Tracks real-time transitions between VPN inbounds and mobile/fixed ISPs in Russia.
"""
import os
import sys
import time
import json
import re
import socket
import argparse
import ipaddress
import logging
from datetime import datetime, timezone
from collections import defaultdict, Counter
from pathlib import Path
import urllib.request
import urllib.parse

# Paths
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))
try:
    from config import ADMIN_TG_ID, TELEGRAM_BOT_TOKEN
except Exception as e:
    ADMIN_TG_ID = None
    TELEGRAM_BOT_TOKEN = None
    env_path = BASE_DIR / ".env"
    if env_path.exists():
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("ADMIN_TG_ID=") and not ADMIN_TG_ID:
                    val = line.split("=", 1)[1].strip().strip('"').strip("'")
                    ADMIN_TG_ID = int(val) if val.isdigit() else None
                elif line.startswith("TELEGRAM_BOT_TOKEN=") and not TELEGRAM_BOT_TOKEN:
                    TELEGRAM_BOT_TOKEN = line.split("=", 1)[1].strip().strip('"').strip("'")

DATA_DIR = BASE_DIR / "data"
LOG_DIR = BASE_DIR / "logs"
STATE_FILE = DATA_DIR / "inbound_switches.json"
EVENTS_LOG_FILE = LOG_DIR / "inbound_switches.log"
ACCESS_LOG_PATH = Path("/var/log/x-ui/access.log")

DATA_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(EVENTS_LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ]
)
logger = logging.getLogger("switch_monitor")

# Port to Friendly Name
INBOUND_NAMES = {
    "4443": "Hysteria2 Turbo (4443)",
    "7443": "Reality Vision (7443)",
    "48745": "xHTTP Armor (48745)",
    "47447": "xHTTP Stream (47447)",
    "53151": "Reality Standard (53151)",
    "35443": "Hysteria2 Armor (35443)",
    "38745": "xHTTP Ultra (38745)",
    "14715": "WS TLS (14715)",
    "8081": "Anti-DPI Testing (8081)",
    "8443": "RU Gateway (8443)",
}

# Known Russian Subnet Ranges for Fast Offline Lookup
KNOWN_ISPS = [
    (ipaddress.ip_network("178.176.0.0/15"), "MegaFon"),
    (ipaddress.ip_network("83.149.0.0/16"), "MegaFon"),
    (ipaddress.ip_network("85.26.0.0/16"), "MegaFon"),
    (ipaddress.ip_network("31.173.0.0/16"), "MegaFon"),
    (ipaddress.ip_network("178.178.0.0/15"), "MTS"),
    (ipaddress.ip_network("213.87.0.0/16"), "MTS"),
    (ipaddress.ip_network("176.97.0.0/16"), "MTS"),
    (ipaddress.ip_network("91.76.0.0/14"), "Beeline"),
    (ipaddress.ip_network("91.78.0.0/15"), "Beeline"),
    (ipaddress.ip_network("91.79.0.0/16"), "Beeline"),
    (ipaddress.ip_network("217.118.64.0/18"), "Beeline"),
    (ipaddress.ip_network("94.245.128.0/18"), "Rostelecom (YKT)"),
    (ipaddress.ip_network("77.37.0.0/16"), "Rostelecom"),
    (ipaddress.ip_network("178.46.0.0/15"), "Rostelecom"),
    (ipaddress.ip_network("188.168.0.0/15"), "Rostelecom"),
    (ipaddress.ip_network("176.59.0.0/16"), "Tele2 / T2"),
    (ipaddress.ip_network("95.153.0.0/16"), "Tele2 / T2"),
    (ipaddress.ip_network("46.191.0.0/16"), "Ufanet"),
    (ipaddress.ip_network("92.126.0.0/15"), "TTK"),
    (ipaddress.ip_network("127.0.0.0/8"), "Localhost"),
    (ipaddress.ip_network("10.0.0.0/8"), "Internal VPN / Bridge"),
]

_ISP_CACHE = {}


def resolve_isp(ip_str: str) -> str:
    """Определяет оператора связи по IP-адресу с кэшированием."""
    if not ip_str:
        return "Unknown"
    if ip_str in _ISP_CACHE:
        return _ISP_CACHE[ip_str]

    try:
        ip_obj = ipaddress.ip_address(ip_str)
        # Fast local CIDR matching
        for net, isp_name in KNOWN_ISPS:
            if ip_obj in net:
                _ISP_CACHE[ip_str] = isp_name
                return isp_name
    except ValueError:
        return "Unknown"

    # Fallback to reverse DNS or generic country
    try:
        host, _, _ = socket.gethostbyaddr(ip_str)
        host_l = host.lower()
        if "megafon" in host_l:
            isp = "MegaFon"
        elif "mts" in host_l:
            isp = "MTS"
        elif "beeline" in host_l or "corbina" in host_l:
            isp = "Beeline"
        elif "tele2" in host_l:
            isp = "Tele2 / T2"
        elif "rt" in host_l or "rostelecom" in host_l or "sakhainform" in host_l:
            isp = "Rostelecom"
        else:
            isp = f"RU ISP ({host.split('.')[-2] if '.' in host else host})"
    except Exception:
        isp = "Other / RU ISP"

    _ISP_CACHE[ip_str] = isp
    return isp


def parse_inbound_from_tag(tag: str) -> str:
    """Извлекает порт и человекочитаемое имя из тега лога Xray."""
    # Tag formats: inbound-4443, in-47447-tcp, inbound-7443, in-48745-tcp
    m = re.search(r'(?:inbound-|in-)(\d+)', tag)
    if m:
        port = m.group(1)
        return INBOUND_NAMES.get(port, f"Inbound {port}")
    return tag


_USER_CACHE = {}


def resolve_user_display(email: str) -> str:
    """Резолвит имя пользователя из базы данных для алерта (с кэшированием)."""
    if not email:
        return "Unknown"
    if email in _USER_CACHE:
        return _USER_CACHE[email]

    display = f"<code>{email}</code>"
    tg_id = None
    if email.startswith("tiin_") or email.startswith("vless_"):
        parts = email.split("_", 1)
        if len(parts) > 1 and parts[1].isdigit():
            tg_id = int(parts[1])
    elif email.isdigit():
        tg_id = int(email)

    if tg_id:
        try:
            from api.db import execute_query
            row = execute_query(
                "SELECT first_name, old_first_name FROM users WHERE tg_id = %s",
                (tg_id,),
                fetch="one"
            )
            if row:
                name = row.get("first_name") or row.get("old_first_name")
                if name:
                    display = f"{name} (<code>{email}</code>)"
        except Exception as e:
            logger.debug(f"Failed to resolve name for {email}: {e}")

    _USER_CACHE[email] = display
    return display


class SwitchTracker:
    def __init__(self, state_file=STATE_FILE):
        self.state_file = Path(state_file)
        self.user_states = {}  # {email: {"inbound": str, "ip": str, "isp": str, "ts": float}}
        self.device_states = {}  # {f"{email}@{ip}": {"inbound": str, "isp": str, "ts": float}}
        self.switch_matrix = defaultdict(Counter)  # {from_inbound: Counter(to_inbound)}
        self.isp_switches = defaultdict(Counter)  # {isp: Counter("from->to")}
        self.user_switch_counts = Counter()
        self.total_switches = 0
        self.total_roaming = 0
        self.recent_fallback_events = []
        self.alert_cooldowns = {}
        self.blocked_attempts = defaultdict(list)
        self.load_state()

    def send_telegram_alert(self, text: str):
        """Отправляет алерт админу в Telegram."""
        if not TELEGRAM_BOT_TOKEN or not ADMIN_TG_ID:
            logger.warning("Telegram alert skipped: TELEGRAM_BOT_TOKEN or ADMIN_TG_ID is not configured.")
            return
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
            payload = {
                "chat_id": ADMIN_TG_ID,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            }
            data = urllib.parse.urlencode(payload).encode("utf-8")
            req = urllib.request.Request(url, data=data, headers={"User-Agent": "TiinSwitchMonitor/1.0"})
            with urllib.request.urlopen(req, timeout=5) as resp:
                pass
        except Exception as e:
            logger.warning(f"Failed to send Telegram switch alert: {e}")

    def check_anomaly_alert(self, isp: str, prev_inbound: str, new_inbound: str, email: str, now_ts: float, client_ip: str = ""):
        """Проверяет всплеск вынужденных переключений с Hysteria/Reality на xHTTP/Armor."""
        cutoff = now_ts - 900
        self.recent_fallback_events = [ev for ev in self.recent_fallback_events if ev["ts"] > cutoff]

        is_fallback = ("Hysteria" in prev_inbound or "Reality" in prev_inbound) and ("xHTTP" in new_inbound or "Armor" in new_inbound or "Stream" in new_inbound)
        if not is_fallback or isp in ("Unknown", "Localhost", "Internal VPN / Bridge"):
            return

        self.recent_fallback_events.append({
            "ts": now_ts,
            "isp": isp,
            "from": prev_inbound,
            "to": new_inbound,
            "email": email,
            "ip": client_ip,
        })

        matching = [ev for ev in self.recent_fallback_events if ev["isp"] == isp]
        unique_users = {ev["email"] for ev in matching}
        unique_ips = {ev["ip"] for ev in matching if ev.get("ip")}

        # Фильтрация шума от мультидевайсных аккаунтов:
        # Волна блокировки оператора: минимум 2 РАЗНЫХ пользователя упали на xHTTP
        # Одиночный пользователь: минимум 5 подтвержденных переключений со строго ОДНОГО IP
        is_mass_wave = len(unique_users) >= 2
        is_single_user_persistent = len(unique_users) == 1 and len(matching) >= 5 and len(unique_ips) == 1

        if is_mass_wave or is_single_user_persistent:
            cooldown_key = f"{isp}_{prev_inbound}"
            last_alert = self.alert_cooldowns.get(cooldown_key, 0)
            if now_ts - last_alert > 1800:
                self.alert_cooldowns[cooldown_key] = now_ts
                users_list = [resolve_user_display(u) for u in sorted(unique_users)]
                if len(users_list) == 1:
                    user_label = "▫️ <b>Пользователь (одно устройство):</b>"
                    ip_info = f" [IP: <code>{list(unique_ips)[0]}</code>]" if unique_ips else ""
                    users_str = f"{users_list[0]}{ip_info}"
                    footer = f"<i>ℹ️ Локальная нестабильность/блокировка у одного клиента на операторе {isp}. Трафик подхвачен xHTTP.</i>"
                else:
                    user_label = f"▫️ <b>Пользователи ({len(users_list)}):</b>"
                    users_str = ", ".join(users_list[:5])
                    if len(users_list) > 5:
                        users_str += f" (+еще {len(users_list) - 5})"
                    footer = f"<i>🚨 <b>Внимание:</b> Подтверждена волна блокировки UDP/Reality оператором {isp} (затронуто {len(unique_users)} клиентов). Трафик подхвачен xHTTP.</i>"

                alert_text = (
                    f"🚨 <b>DPI Sentinel Alert • {isp}</b>\n\n"
                    f"⚠️ <b>Зафиксирована серия переключений протоколов!</b>\n"
                    f"▫️ <b>С узла:</b> <code>{prev_inbound}</code>\n"
                    f"▫️ <b>На узел:</b> <code>{new_inbound}</code>\n"
                    f"{user_label} {users_str}\n"
                    f"▫️ <b>Событий за 15 мин:</b> {len(matching)} (пользователей: {len(unique_users)})\n"
                    f"▫️ <b>Время:</b> {datetime.now().strftime('%H:%M:%S')}\n\n"
                    f"{footer}"
                )
                logger.warning(f"🚨 Anomaly triggered for {isp}: {len(matching)} fallback events in 15m. Sending TG alert.")
                self.send_telegram_alert(alert_text)

    def load_state(self):
        if self.state_file.exists():
            try:
                with open(self.state_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.user_states = data.get("user_states", {})
                    self.device_states = data.get("device_states", {})
                    self.total_switches = data.get("total_switches", 0)
                    self.total_roaming = data.get("total_roaming", 0)
                    for k, v in data.get("switch_matrix", {}).items():
                        self.switch_matrix[k] = Counter(v)
                    for k, v in data.get("isp_switches", {}).items():
                        self.isp_switches[k] = Counter(v)
                    self.user_switch_counts = Counter(data.get("user_switch_counts", {}))
            except Exception as e:
                logger.warning(f"Failed to load state: {e}")

    def save_state(self):
        # Удаляем устаревшие сессии устройств старше 48 часов
        cutoff_device = time.time() - 172800
        self.device_states = {k: v for k, v in self.device_states.items() if v.get("ts", 0) > cutoff_device}

        data = {
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "total_switches": self.total_switches,
            "total_roaming": self.total_roaming,
            "user_states": self.user_states,
            "device_states": self.device_states,
            "switch_matrix": {k: dict(v) for k, v in self.switch_matrix.items()},
            "isp_switches": {k: dict(v) for k, v in self.isp_switches.items()},
            "user_switch_counts": dict(self.user_switch_counts),
        }
        temp_file = self.state_file.with_suffix(".tmp")
        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        temp_file.replace(self.state_file)

    def process_blocked_event(self, timestamp_str: str, client_ip: str, target: str, email: str):
        """Threat Sentinel: фиксация попыток зондирования / атак и алерт в Telegram при превышении порога."""
        if not email or email in ("api", "none", "bwd2scof", "test_awg_user_01", "test_awg_user") or client_ip in ("127.0.0.1", "::1"):
            return

        now = time.time()
        self.blocked_attempts[email] = [t for t in self.blocked_attempts[email] if now - t < 60]
        self.blocked_attempts[email].append(now)

        count = len(self.blocked_attempts[email])
        if count >= 15:
            cooldown_key = f"threat_{email}"
            if now - self.alert_cooldowns.get(cooldown_key, 0) > 1800:
                self.alert_cooldowns[cooldown_key] = now
                isp_name = resolve_isp(client_ip)
                user_disp = resolve_user_display(email)
                msg = (
                    f"🚨 <b>VPN Threat Sentinel: Зондирование / Сканирование!</b>\n\n"
                    f"👤 <b>Клиент:</b> {user_disp}\n"
                    f"🌐 <b>IP / Провайдер:</b> <code>{client_ip}</code> ({isp_name})\n"
                    f"🎯 <b>Цель:</b> <code>{target}</code>\n"
                    f"⚠️ <b>Число blocked-попыток:</b> {count} за 60 сек\n\n"
                    f"<i>Все запросы сброшены в blackhole (0 байт наружу). Сервер в безопасности.</i>"
                )
                self.send_telegram_alert(msg)
                logger.warning(f"THREAT ALERT: {email} triggered {count} blocked events (last target: {target})")

    def process_event(self, timestamp_str: str, client_ip: str, inbound_raw: str, email: str):
        # Ignore API, local healthchecks and system/bridge accounts
        if not email or email in ("api", "none", "bwd2scof", "test_awg_user_01", "test_awg_user") or client_ip in ("127.0.0.1", "::1"):
            return

        inbound = parse_inbound_from_tag(inbound_raw)
        isp = resolve_isp(client_ip)
        now_ts = time.time()

        device_key = f"{email}@{client_ip}"
        prev_device = self.device_states.get(device_key)
        prev_user = self.user_states.get(email)

        # 1. Проверка переключения инбаунда:
        # Учитывается строго per-device (по конкретному IP пользователя),
        # что устраняет ложные срабатывания от чередующихся пакетов с разных девайсов (Multi-Device)
        if prev_device:
            prev_inbound = prev_device.get("inbound")
            if prev_inbound and prev_inbound != inbound:
                dt = now_ts - prev_device.get("ts", now_ts)
                self.total_switches += 1
                self.user_switch_counts[email] += 1
                self.switch_matrix[prev_inbound][inbound] += 1
                self.isp_switches[isp][f"{prev_inbound} ➔ {inbound}"] += 1

                log_msg = (
                    f"🔄 [SWITCH] User: {email} | ISP: {isp} | "
                    f"From: {prev_inbound} ➔ To: {inbound} | "
                    f"Gap: {int(dt)}s | IP: {client_ip}"
                )
                logger.info(log_msg)
                self.check_anomaly_alert(isp, prev_inbound, inbound, email, now_ts, client_ip)
        elif prev_user:
            # Новый IP для пользователя. Проверяем роуминг, если предыдущий IP неактивен > 30s
            prev_isp = prev_user.get("isp")
            prev_ip = prev_user.get("ip")
            prev_ts = prev_user.get("ts", now_ts)
            dt = now_ts - prev_ts
            if prev_isp and prev_isp != isp and dt >= 30:
                self.total_roaming += 1
                log_msg = (
                    f"📡 [ROAMING] User: {email} | Inbound: {inbound} | "
                    f"From ISP: {prev_isp} ({prev_ip}) ➔ To ISP: {isp} ({client_ip})"
                )
                logger.info(log_msg)

        # Обновляем состояние устройства и пользователя
        self.device_states[device_key] = {
            "inbound": inbound,
            "isp": isp,
            "ts": now_ts,
            "last_seen": timestamp_str,
        }
        self.user_states[email] = {
            "inbound": inbound,
            "ip": client_ip,
            "isp": isp,
            "ts": now_ts,
            "last_seen": timestamp_str,
        }

    def generate_report(self) -> str:
        lines = []
        lines.append("=" * 70)
        lines.append("📊 ОТЧЕТ ПО ПЕРЕКЛЮЧЕНИЯМ ИНБАУНДОВ И ОПЕРАТОРОВ (TIIN VPN)")
        lines.append(f"Время формирования: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        lines.append(f"Всего зафиксировано переключений серверов: {self.total_switches}")
        lines.append(f"Всего зафиксировано смен сетей (роуминг): {self.total_roaming}")
        lines.append("=" * 70)

        # 1. Топ переходов между инбаундами
        lines.append("\n🔄 МАТРИЦА ПЕРЕХОДОВ (С какого сервера на какой переключаются):")
        if self.switch_matrix:
            for src_ib, targets in self.switch_matrix.items():
                for tgt_ib, count in targets.most_common():
                    lines.append(f"  • {src_ib:28s} ➔ {tgt_ib:28s} : {count:3d} раз")
        else:
            lines.append("  (Пока нет зафиксированных переключений)")

        # 2. Переключения в разрезе операторов
        lines.append("\n📱 ПЕРЕКЛЮЧЕНИЯ ПО ОПЕРАТОРАМ СВЯЗИ:")
        if self.isp_switches:
            for isp_name, transitions in self.isp_switches.items():
                lines.append(f"  [{isp_name}]")
                for trans, cnt in transitions.most_common(5):
                    lines.append(f"     └─ {trans} : {cnt} раз")
        else:
            lines.append("  (Нет данных)")

        # 3. Топ пользователей по числу переключений
        lines.append("\n👥 ТОП ПОЛЬЗОВАТЕЛЕЙ ПО ЧАСТОТЕ ПЕРЕКЛЮЧЕНИЙ:")
        if self.user_switch_counts:
            for email, cnt in self.user_switch_counts.most_common(10):
                cur_state = self.user_states.get(email, {})
                cur_ib = cur_state.get("inbound", "Unknown")
                cur_isp = cur_state.get("isp", "Unknown")
                lines.append(f"  • {email:22s} : {cnt:3d} переключений (Сейчас: {cur_ib} на {cur_isp})")
        else:
            lines.append("  (Все пользователи стабильно сидят на своих узлах)")

        lines.append("\n" + "=" * 70)
        return "\n".join(lines)


def process_log_line(line: str, tracker: SwitchTracker):
    """Парсит стандартную строку access.log от Xray."""
    # Example: 2026/09/18 04:04:25.973256 from 139.100.207.18:45850 accepted tcp:... [inbound-4443 -> ...] email: vless_6335998601
    if "email:" not in line or " from " not in line:
        return

    try:
        parts = line.split()
        ts_str = f"{parts[0]} {parts[1]}"
        
        # Extract IP
        ip_with_port = parts[3]
        ip = ip_with_port.split(":")[0] if ":" in ip_with_port else ip_with_port

        # Extract Inbound
        inbound_raw = "unknown"
        m_tag = re.search(r'\[(inbound-[^ \]]+|in-[^ \]]+)', line)
        if m_tag:
            inbound_raw = m_tag.group(1)

        # Extract Email
        m_email = re.search(r'email:\s*([^\s]+)', line)
        email = m_email.group(1) if m_email else ""

        # Threat Sentinel: проверка на сброс в blackhole (OONI, РКН honeypot, сканирование)
        if "-> blocked" in line or ">> blocked" in line:
            m_target = re.search(r'accepted\s+([^\s]+)\s+\[', line)
            target = m_target.group(1) if m_target else "unknown"
            tracker.process_blocked_event(ts_str, ip, target, email)
            return

        tracker.process_event(ts_str, ip, inbound_raw, email)
    except Exception:
        pass


def scan_history(tracker: SwitchTracker, lines_to_read: int = 50000):
    """Сканирует исторический лог для заполнения матрицы."""
    if not ACCESS_LOG_PATH.exists():
        logger.warning(f"Log file {ACCESS_LOG_PATH} not found.")
        return

    logger.info(f"Scanning up to {lines_to_read} recent lines from {ACCESS_LOG_PATH}...")
    try:
        import subprocess
        cmd = ["sudo", "tail", "-n", str(lines_to_read), str(ACCESS_LOG_PATH)]
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if res.returncode == 0:
            lines = res.stdout.splitlines()
            for line in lines:
                process_log_line(line, tracker)
            tracker.save_state()
            logger.info(f"Processed {len(lines)} historical lines. Total switches recorded: {tracker.total_switches}")
    except Exception as e:
        logger.error(f"Error scanning history: {e}")


def tail_live(tracker: SwitchTracker):
    """Слушает лог в реальном времени."""
    logger.info("Starting live monitoring daemon...")
    import subprocess
    cmd = ["sudo", "tail", "-F", "-n", "0", str(ACCESS_LOG_PATH)]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    last_save = time.time()
    try:
        for line in proc.stdout:
            process_log_line(line, tracker)
            if time.time() - last_save > 30:  # Save state every 30s
                tracker.save_state()
                last_save = time.time()
    except KeyboardInterrupt:
        logger.info("Stopping switch monitor...")
    finally:
        tracker.save_state()
        proc.terminate()


def main():
    parser = argparse.ArgumentParser(description="VPN Inbound and ISP Switch Monitor")
    parser.add_argument("--report", action="store_true", help="Print current statistics report")
    parser.add_argument("--scan", type=int, default=0, help="Scan N historical lines and update state")
    parser.add_argument("--daemon", action="store_true", help="Run in continuous background daemon mode")
    args = parser.parse_args()

    tracker = SwitchTracker()

    if args.scan > 0:
        scan_history(tracker, args.scan)

    if args.report:
        if not args.scan and tracker.total_switches == 0:
            scan_history(tracker, 10000)
        print(tracker.generate_report())
        return

    if args.daemon:
        scan_history(tracker, 5000)
        tail_live(tracker)
    else:
        # Default: scan recent and print report
        scan_history(tracker, 10000)
        print(tracker.generate_report())


if __name__ == "__main__":
    main()
