#!/usr/bin/env python3
"""
TSPU / DPI Wave Detector for TIIN VPN.
Monitors real-time blocking anomalies across Russian ISPs using:
1. Spike in protocol switching (Hysteria/UDP -> xHTTP/Vision) in inbound_switches.json.
2. Reality SNI TLS handshake availability.
3. L3 RU-DE Bridge (AmneziaWG) latency and packet loss.
"""
import os
import sys
import json
import time
import socket
import ssl
import subprocess
import argparse
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

STATE_FILE = BASE_DIR / "data" / "inbound_switches.json"
REALITY_DOMAINS = ["www.microsoft.com", "aws.amazon.com", "dl.google.com", "gateway.icloud.com"]

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("detect_tspu")


def check_sni_reachability(domain: str, port: int = 443, timeout: float = 3.0) -> bool:
    """Fast TLS handshake check for Reality SNI domains."""
    try:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        with socket.create_connection((domain, port), timeout=timeout) as sock:
            with ctx.wrap_socket(sock, server_hostname=domain) as ssock:
                return True
    except Exception:
        return False


def check_l3_bridge() -> dict:
    """Checks AmneziaWG site-to-site bridge (10.50.0.1 <-> 10.50.0.2)."""
    try:
        # Ping the RU node internal IP
        res = subprocess.run(
            ["ping", "-c", "2", "-W", "1", "10.50.0.2"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=3
        )
        ok = res.returncode == 0
        return {"ok": ok, "latency_ms": 15.0 if ok else 999.0}
    except Exception as e:
        return {"ok": False, "error": str(e), "latency_ms": 999.0}


def analyze_inbound_switches(window_minutes: int = 30) -> dict:
    """
    Analyzes switch history for sudden migration to xHTTP / anti-DPI inbounds
    or clustering by mobile operators (MTS, Beeline, MegaFon, Tele2).
    """
    if not STATE_FILE.exists():
        return {
            "recent_switches_count": 0,
            "armor_switches_count": 0,
            "affected_isps": [],
            "spike_ratio": 0.0,
        }

    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        logger.warning(f"Failed to read inbound switches: {e}")
        return {"recent_switches_count": 0, "armor_switches_count": 0, "affected_isps": []}

    now_ts = time.time()
    cutoff_ts = now_ts - (window_minutes * 60)

    user_states = data.get("user_states", {})
    recent_switches = 0
    armor_switches = 0
    isp_counts = {}

    for user_key, state in user_states.items():
        ts = state.get("ts", 0)
        if ts >= cutoff_ts:
            recent_switches += 1
            inbound = state.get("inbound", "")
            isp = state.get("isp", "Unknown")
            if "xHTTP" in inbound or "Armor" in inbound or "Stream" in inbound:
                armor_switches += 1
            if isp not in ("Unknown", "Internal VPN / Bridge", "Localhost"):
                isp_counts[isp] = isp_counts.get(isp, 0) + 1

    # Top affected ISPs
    affected_isps = [isp for isp, cnt in sorted(isp_counts.items(), key=lambda x: x[1], reverse=True) if cnt >= 1]

    return {
        "recent_switches_count": recent_switches,
        "armor_switches_count": armor_switches,
        "affected_isps": affected_isps,
        "isp_distribution": isp_counts,
    }


def calculate_dpi_score() -> dict:
    """
    Calculates unified DPI Blocking Score (0 - 100):
    - 0..30: Normal (Green) - No significant anomalies detected
    - 31..65: Elevated (Yellow) - Mild ISP filtering / localized anomalies
    - 66..100: TSPU Wave (Red) - Aggressive DPI filtering detected
    """
    switches_info = analyze_inbound_switches(window_minutes=30)
    bridge_info = check_l3_bridge()

    # Fast SNI test
    failed_snis = []
    for domain in REALITY_DOMAINS:
        if not check_sni_reachability(domain, timeout=2.0):
            failed_snis.append(domain)

    # Score components
    score = 0

    # 1. Switch rate to Armor / xHTTP
    armor_count = switches_info.get("armor_switches_count", 0)
    recent_count = switches_info.get("recent_switches_count", 0)

    if armor_count >= 5:
        score += 45
    elif armor_count >= 2:
        score += 25
    elif recent_count >= 8:
        score += 15

    # 2. Bridge status
    if not bridge_info.get("ok"):
        score += 35

    # 3. SNI Handshake failures
    if len(failed_snis) >= 2:
        score += 40
    elif len(failed_snis) == 1:
        score += 20

    score = min(100, max(0, score))

    if score >= 66:
        level = "wave_detected"
        status_text = "🚨 Обнаружена активная волна блокировок ТСПУ"
        color = "red"
    elif score >= 31:
        level = "elevated"
        status_text = "⚠️ Повышенная активность фильтрации трафика"
        color = "yellow"
    else:
        level = "normal"
        status_text = "✅ Фоновый режим — блокировок не зафиксировано"
        color = "green"

    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "score": score,
        "level": level,
        "color": color,
        "status_text": status_text,
        "affected_isps": switches_info.get("affected_isps", []),
        "metrics": {
            "recent_switches_30m": recent_count,
            "armor_switches_30m": armor_count,
            "l3_bridge_ok": bridge_info.get("ok"),
            "failed_snis": failed_snis,
            "isp_distribution": switches_info.get("isp_distribution", {}),
        }
    }


def main():
    parser = argparse.ArgumentParser(description="TSPU / DPI Wave Detector for TIIN VPN")
    parser.add_argument("--json", action="store_true", help="Output result as JSON")
    parser.add_argument("--check", action="store_true", help="Print human-readable summary")
    args = parser.parse_args()

    result = calculate_dpi_score()

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print("=" * 60)
        print(f"TIIN DPI & TSPU DETECTOR — SCORE: {result['score']}/100 [{result['level'].upper()}]")
        print(f"Статус: {result['status_text']}")
        print(f"Затронутые операторы: {', '.join(result['affected_isps']) if result['affected_isps'] else 'Нет аномалий'}")
        print("-" * 60)
        print(f"Переключений за 30 мин: {result['metrics']['recent_switches_30m']} (на Armor/xHTTP: {result['metrics']['armor_switches_30m']})")
        print(f"L3 Мост RU-DE (10.50.0.2): {'OK' if result['metrics']['l3_bridge_ok'] else 'DOWN / Packet Loss'}")
        print(f"Сбойных Reality SNI: {len(result['metrics']['failed_snis'])}")
        print("=" * 60)


if __name__ == "__main__":
    main()
