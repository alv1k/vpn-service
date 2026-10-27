#!/usr/bin/env python3
"""
Скрипт синхронизации и обновления базы черных списков (Threat Feed).
Обновляет списки зондов (OONI, M-Lab) и систем мониторинга цензуры (ЦМУ ССОП / РКН)
в AdGuard Home и Xray на обеих нодах (DE Hetzner + RU Selectel).
"""
import argparse
import json
import logging
import sqlite3
import subprocess
import sys
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger("threat_feed")

BASELINE_THREAT_DOMAINS = [
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
    "antizapret.info",
    "isitblockedinrussia.com"
]

def read_file_sudo(path: str) -> str:
    res = subprocess.run(["sudo", "cat", path], capture_output=True, text=True, check=True)
    return res.stdout

def write_file_sudo(path: str, content: str):
    tmp = f"/tmp/{Path(path).name}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(content)
    subprocess.run(["sudo", "cp", tmp, path], check=True)
    subprocess.run(["sudo", "rm", "-f", tmp], check=True)

def update_adguard_rules(yaml_path: str, domains: list[str]) -> bool:
    """Обновляет user_rules в AdGuardHome.yaml."""
    try:
        content = read_file_sudo(yaml_path)
    except Exception as e:
        logger.warning(f"Failed to read AdGuard yaml: {e}")
        return False

    lines = content.splitlines(keepends=True)
    new_rules = [f"  - '||{d}^'\n" for d in sorted(domains)]
    
    in_user_rules = False
    output_lines = []
    found_user_rules = False

    for line in lines:
        if line.startswith("user_rules:"):
            found_user_rules = True
            in_user_rules = True
            output_lines.append("user_rules:\n")
            output_lines.extend(new_rules)
            continue
        if in_user_rules:
            if line.startswith("  -") or line.strip() == "[]":
                continue
            else:
                in_user_rules = False
        output_lines.append(line)

    if not found_user_rules:
        output_lines.append("user_rules:\n")
        output_lines.extend(new_rules)

    new_content = "".join(output_lines)
    write_file_sudo(yaml_path, new_content)
    subprocess.run(["sudo", "systemctl", "reload-or-restart", "AdGuardHome"], check=True)
    logger.info(f"Updated AdGuard Home rules at {yaml_path} with {len(domains)} domains.")
    return True

def update_xui_threat_rules(db_path: str, domains: list[str]) -> bool:
    """Обновляет правило blocked в x-ui.db."""
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute("SELECT value FROM settings WHERE key='xrayTemplateConfig'")
    row = c.fetchone()
    if not row:
        conn.close()
        return False

    cfg = json.loads(row[0])
    rules = cfg.get("routing", {}).get("rules", [])
    
    threat_domains = [f"domain:{d}" for d in sorted(domains)]
    
    target_rule = None
    for r in rules:
        if r.get("outboundTag") == "blocked" and "domain" in r:
            target_rule = r
            break

    if target_rule:
        target_rule["domain"] = threat_domains
    else:
        new_r = {
            "type": "field",
            "outboundTag": "blocked",
            "domain": threat_domains
        }
        rules.insert(1, new_r)

    c.execute("UPDATE settings SET value = ? WHERE key = 'xrayTemplateConfig'", (json.dumps(cfg, ensure_ascii=False),))
    conn.commit()
    conn.close()
    logger.info(f"Updated Xray threat rules in {db_path} with {len(domains)} domains.")
    return True

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sync-ru", action="store_true", default=True, help="Синхронизировать с RU-нодой")
    args = parser.parse_args()

    domains = list(set(BASELINE_THREAT_DOMAINS))
    logger.info(f"Processing threat feed update ({len(domains)} domains)...")

    # 1. Update DE AdGuard Home & Xray
    update_adguard_rules("/opt/AdGuardHome/AdGuardHome.yaml", domains)
    update_xui_threat_rules("/etc/x-ui/x-ui.db", domains)

    # 2. Update RU node via SSH
    if args.sync_ru:
        try:
            logger.info("Syncing threat feed with RU-нодой (Selectel)...")
            domains_json = json.dumps(domains)
            remote_script = f"""
import json, sqlite3, subprocess
from pathlib import Path

domains = {domains_json}

# 1. AdGuard on RU
p = Path('/opt/AdGuardHome/AdGuardHome.yaml')
if p.exists():
    with open(p, 'r', encoding='utf-8') as f:
        lines = f.readlines()
    new_rules = [f"  - '||{{d}}^'\\n" for d in sorted(domains)]
    in_user_rules = False
    output_lines = []
    found_user_rules = False
    for line in lines:
        if line.startswith("user_rules:"):
            found_user_rules = True
            in_user_rules = True
            output_lines.append("user_rules:\\n")
            output_lines.extend(new_rules)
            continue
        if in_user_rules:
            if line.startswith("  -") or line.strip() == "[]":
                continue
            else:
                in_user_rules = False
        output_lines.append(line)
    if not found_user_rules:
        output_lines.append("user_rules:\\n")
        output_lines.extend(new_rules)
    with open('/tmp/AdGuardHome_ru.yaml', 'w', encoding='utf-8') as f:
        f.write(''.join(output_lines))
    subprocess.run(['sudo', 'cp', '/tmp/AdGuardHome_ru.yaml', '/opt/AdGuardHome/AdGuardHome.yaml'], check=True)
    subprocess.run(['sudo', 'systemctl', 'restart', 'AdGuardHome'], check=True)

# 2. Xray on RU
conn = sqlite3.connect('/etc/x-ui/x-ui.db')
c = conn.cursor()
c.execute("SELECT value FROM settings WHERE key='xrayTemplateConfig'")
row = c.fetchone()
if row:
    cfg = json.loads(row[0])
    rules = cfg.get("routing", {{}}).get("rules", [])
    threat_domains = [f"domain:{{d}}" for d in sorted(domains)]
    target_rule = None
    for r in rules:
        if r.get("outboundTag") == "blocked" and "domain" in r:
            target_rule = r
            break
    if target_rule:
        target_rule["domain"] = threat_domains
    else:
        rules.insert(1, {{"type": "field", "outboundTag": "blocked", "domain": threat_domains}})
    c.execute("UPDATE settings SET value = ? WHERE key = 'xrayTemplateConfig'", (json.dumps(cfg, ensure_ascii=False),))
    conn.commit()
conn.close()
"""
            subprocess.run(["ssh", "-o", "ConnectTimeout=5", "ru-server", "python3"], input=remote_script, text=True, check=True)
            logger.info("RU node threat feed successfully updated!")
        except Exception as e:
            logger.error(f"Failed to sync with RU node: {e}")

    logger.info("Threat feed update finished successfully!")

if __name__ == "__main__":
    main()
