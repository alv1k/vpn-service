#!/usr/bin/env python3
"""
VPN Inbound & ISP Switches History Aggregator
Parses /home/alvik/vpn-service/logs/inbound_switches.log and generates
a comprehensive aggregated summary (JSON and Markdown).
Optionally archives historical logs to log.gz.
"""

import os
import re
import sys
import gzip
import json
import shutil
import logging
from collections import defaultdict
from datetime import datetime

LOG_FILE = "/home/alvik/vpn-service/logs/inbound_switches.log"
DATA_DIR = "/home/alvik/vpn-service/data"
JSON_OUTPUT = os.path.join(DATA_DIR, "inbound_switches_summary.json")
MD_OUTPUT = os.path.join(DATA_DIR, "inbound_switches_summary.md")
ARCHIVE_OUTPUT = "/home/alvik/vpn-service/logs/inbound_switches_202609_202611.log.gz"

SWITCH_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d+ \[INFO\] 🔄 \[SWITCH\] "
    r"User: (?P<user>[^\|]+) \| ISP: (?P<isp>[^\|]+) \| "
    r"From: (?P<from_inbound>[^➔]+) ➔ To: (?P<to_inbound>[^\|]+) \| "
    r"Gap: (?P<gap>\d+)s \| IP: (?P<ip>.*)$"
)

ROAMING_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d+ \[INFO\] 📡 \[ROAMING\] "
    r"User: (?P<user>[^\|]+) \| Inbound: (?P<inbound>[^\|]+) \| "
    r"From ISP: (?P<from_isp>[^\(]+) \((?P<prev_ip>[^\)]+)\) ➔ To ISP: (?P<to_isp>[^\(]+) \((?P<curr_ip>[^\)]+)\)$"
)


def parse_and_aggregate(log_path: str):
    if not os.path.exists(log_path):
        print(f"Log file not found: {log_path}", file=sys.stderr)
        sys.exit(1)

    print(f"Reading and analyzing {log_path}...")

    total_lines = 0
    total_switches = 0
    total_roaming = 0

    earliest_ts = None
    latest_ts = None

    # Time distributions: YYYY-MM and YYYY-WW
    monthly_switches = defaultdict(int)
    monthly_roaming = defaultdict(int)

    # Gap (flapping) analysis
    gap_buckets = {
        "< 5s (Flapping)": 0,
        "5s - 30s (Rapid)": 0,
        "30s - 300s (Moderate)": 0,
        "> 300s (Organic)": 0,
    }

    # Switches matrix: (from, to) -> count
    switch_matrix = defaultdict(int)

    # ISP switches: isp -> { "from ➔ to": count }
    isp_switches = defaultdict(lambda: defaultdict(int))
    isp_total_switches = defaultdict(int)

    # Top users
    user_switches = defaultdict(int)
    user_roaming = defaultdict(int)

    # Roaming matrix: (from_isp, to_isp) -> count
    roaming_matrix = defaultdict(int)

    with open(log_path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            total_lines += 1
            if total_lines % 200000 == 0:
                print(f"  Processed {total_lines:,} lines...")

            if "🔄 [SWITCH]" in line:
                m = SWITCH_RE.match(line)
                if m:
                    dt_str = m.group(1)
                    user = m.group("user").strip()
                    isp = m.group("isp").strip()
                    from_inb = m.group("from_inbound").strip()
                    to_inb = m.group("to_inbound").strip()
                    gap = int(m.group("gap"))

                    total_switches += 1
                    if earliest_ts is None:
                        earliest_ts = dt_str
                    latest_ts = dt_str

                    month_key = dt_str[:7]
                    monthly_switches[month_key] += 1

                    if gap < 5:
                        gap_buckets["< 5s (Flapping)"] += 1
                    elif gap <= 30:
                        gap_buckets["5s - 30s (Rapid)"] += 1
                    elif gap <= 300:
                        gap_buckets["30s - 300s (Moderate)"] += 1
                    else:
                        gap_buckets["> 300s (Organic)"] += 1

                    pair_key = f"{from_inb} ➔ {to_inb}"
                    switch_matrix[pair_key] += 1
                    isp_switches[isp][pair_key] += 1
                    isp_total_switches[isp] += 1
                    user_switches[user] += 1

            elif "📡 [ROAMING]" in line:
                m = ROAMING_RE.match(line)
                if m:
                    dt_str = m.group(1)
                    user = m.group("user").strip()
                    from_isp = m.group("from_isp").strip()
                    to_isp = m.group("to_isp").strip()

                    total_roaming += 1
                    if earliest_ts is None:
                        earliest_ts = dt_str
                    latest_ts = dt_str

                    month_key = dt_str[:7]
                    monthly_roaming[month_key] += 1

                    user_roaming[user] += 1
                    roaming_pair = f"{from_isp} ➔ {to_isp}"
                    roaming_matrix[roaming_pair] += 1

    print(f"Finished processing {total_lines:,} lines.")
    print(f"Total Switches: {total_switches:,}, Total Roaming: {total_roaming:,}")

    # Prepare JSON structure
    top_switch_matrix = sorted(switch_matrix.items(), key=lambda x: x[1], reverse=True)
    top_isp_switches = {}
    for isp, total in sorted(isp_total_switches.items(), key=lambda x: x[1], reverse=True)[:15]:
        top_transitions = sorted(isp_switches[isp].items(), key=lambda x: x[1], reverse=True)[:5]
        top_isp_switches[isp] = {
            "total": total,
            "top_transitions": [{"pair": p, "count": c} for p, c in top_transitions],
        }

    top_users_sw = sorted(user_switches.items(), key=lambda x: x[1], reverse=True)[:25]
    top_users_rm = sorted(user_roaming.items(), key=lambda x: x[1], reverse=True)[:25]
    top_roaming_pairs = sorted(roaming_matrix.items(), key=lambda x: x[1], reverse=True)[:20]

    summary_data = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "source_log": log_path,
        "earliest_event": earliest_ts,
        "latest_event": latest_ts,
        "total_lines_analyzed": total_lines,
        "total_switches": total_switches,
        "total_roaming": total_roaming,
        "monthly_switches": dict(sorted(monthly_switches.items())),
        "monthly_roaming": dict(sorted(monthly_roaming.items())),
        "gap_distribution": gap_buckets,
        "switch_matrix": [{"pair": k, "count": v} for k, v in top_switch_matrix],
        "isp_distribution": top_isp_switches,
        "top_users_by_switches": [{"user": u, "count": c} for u, c in top_users_sw],
        "top_users_by_roaming": [{"user": u, "count": c} for u, c in top_users_rm],
        "top_roaming_isp_transitions": [{"pair": k, "count": v} for k, v in top_roaming_pairs],
    }

    # Save JSON
    with open(JSON_OUTPUT, "w", encoding="utf-8") as f:
        json.dump(summary_data, f, ensure_ascii=False, indent=2)
    print(f"Summary JSON saved to: {JSON_OUTPUT}")

    # Generate Markdown Report
    flapping_count = gap_buckets.get("< 5s (Flapping)", 0)
    flapping_pct = (flapping_count / total_switches * 100) if total_switches else 0

    md_lines = [
        "# 📊 Аналитический срез переключений инбаундов и операторов VPN",
        f"*Период: {earliest_ts} — {latest_ts} | Всего событий: {total_lines:,}*",
        "",
        "## 1. Сводные метрики",
        f"- **Всего переключений протоколов/инбаундов (SWITCH)**: `{total_switches:,}`",
        f"- **Всего смен сетей/операторов (ROAMING)**: `{total_roaming:,}`",
        f"- **Событий с частым скачком / флаппингом (< 5с)**: `{flapping_count:,}` ({flapping_pct:.1f}%)",
        "",
        "### Помесячная динамика",
        "| Месяц | Переключения (SWITCH) | Смены сетей (ROAMING) |",
        "|---|---|---|",
    ]

    all_months = sorted(set(list(monthly_switches.keys()) + list(monthly_roaming.keys())))
    for m in all_months:
        sw = monthly_switches.get(m, 0)
        rm = monthly_roaming.get(m, 0)
        md_lines.append(f"| **{m}** | {sw:,} | {rm:,} |")

    md_lines.extend([
        "",
        "## 2. Анализ задержек переключения (Flapping & User Behavior)",
        "| Категория задержки | Количество | Доля (%) | Интерпретация |",
        "|---|---|---|---|",
    ])
    for cat, count in gap_buckets.items():
        pct = (count / total_switches * 100) if total_switches else 0
        md_lines.append(f"| `{cat}` | {count:,} | {pct:.1f}% |")

    md_lines.extend([
        "",
        "## 3. Матрица переходов (Топ направлений смены инбаундов)",
        "| Переход (Откуда ➔ Куда) | Количество | Доля от всех переходов |",
        "|---|---|---|",
    ])
    for pair, count in top_switch_matrix[:15]:
        pct = (count / total_switches * 100) if total_switches else 0
        md_lines.append(f"| `{pair}` | {count:,} | {pct:.1f}% |")

    md_lines.extend([
        "",
        "## 4. Распределение переключений по ключевым операторам РФ",
        "| Оператор / Провайдер | Всего переключений | Топ переход |",
        "|---|---|---|",
    ])
    for isp, data in list(top_isp_switches.items())[:12]:
        tot = data["total"]
        top_trans = data["top_transitions"][0] if data["top_transitions"] else {"pair": "N/A", "count": 0}
        md_lines.append(f"| **{isp}** | {tot:,} | `{top_trans['pair']}` ({top_trans['count']:,}) |")

    md_lines.extend([
        "",
        "## 5. Топ-10 пользователей по числу переключений",
        "| Пользователь | Количество переключений |",
        "|---|---|",
    ])
    for u, count in top_users_sw[:10]:
        md_lines.append(f"| `{u}` | {count:,} |")

    with open(MD_OUTPUT, "w", encoding="utf-8") as f:
        f.write("\n".join(md_lines) + "\n")
    print(f"Summary Markdown saved to: {MD_OUTPUT}")


def archive_log(log_path: str, archive_path: str):
    print(f"Compressing {log_path} to {archive_path} (gzip -9)...")
    with open(log_path, "rb") as f_in:
        with gzip.open(archive_path, "wb", compresslevel=9) as f_out:
            shutil.copyfileobj(f_in, f_out)

    orig_size = os.path.getsize(log_path)
    arch_size = os.path.getsize(archive_path)
    print(f"Archived successfully: {orig_size / 1024 / 1024:.1f} MB ➔ {arch_size / 1024 / 1024:.1f} MB (saved {(orig_size - arch_size) / 1024 / 1024:.1f} MB)")

    # Reset log file
    with open(log_path, "w", encoding="utf-8") as f:
        f.write(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S,000')} [INFO] Log rotated. Previous history archived to {archive_path}\n")
    print(f"Reset {log_path} to fresh state.")


def main():
    archive_mode = "--archive" in sys.argv
    parse_and_aggregate(LOG_FILE)
    if archive_mode:
        archive_log(LOG_FILE, ARCHIVE_OUTPUT)


if __name__ == "__main__":
    main()
