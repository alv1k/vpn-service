#!/usr/bin/env python3
"""
Fetch Russian IP ranges and configure split tunneling:
- AmneziaWG: compute complement of RU CIDRs for AllowedIPs

Run via cron weekly or manually.
"""
import ipaddress
import logging
import os
import re
import subprocess
import sys
import urllib.request

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)

PROJECT_DIR = "/home/alvik/vpn-service"
ENV_FILE = os.path.join(PROJECT_DIR, ".env")
DATA_DIR = os.path.join(PROJECT_DIR, "data")
RU_CIDRS_FILE = os.path.join(DATA_DIR, "ru_cidrs.txt")

IPDENY_URL = "https://www.ipdeny.com/ipblocks/data/aggregated/ru-aggregated.zone"
RIPE_URL = "https://stat.ripe.net/data/country-resource-list/data.json?resource=RU"

# Aggregation levels
AWG_PREFIX_LEVEL = 16   # /16 aggregation for AmneziaWG (manageable config size)


def load_env():
    pass


def fetch_ru_cidrs() -> list[ipaddress.IPv4Network]:
    """Fetch RU IP ranges from ipdeny (fallback: RIPE)."""
    try:
        log.info(f"Fetching RU CIDRs from ipdeny...")
        req = urllib.request.Request(IPDENY_URL, headers={"User-Agent": "vpn-split-tunnel/1.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            text = resp.read().decode()
        networks = []
        for line in text.strip().splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                networks.append(ipaddress.IPv4Network(line, strict=False))
        log.info(f"Fetched {len(networks)} RU CIDRs from ipdeny")
        return networks
    except Exception as e:
        log.warning(f"ipdeny failed ({e}), trying RIPE...")

    import json
    req = urllib.request.Request(RIPE_URL, headers={"User-Agent": "vpn-split-tunnel/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode())
    networks = []
    for entry in data["data"]["resources"]["ipv4"]:
        if "/" in entry:
            networks.append(ipaddress.IPv4Network(entry, strict=False))
        elif "-" in entry:
            start, end = entry.split("-")
            networks.extend(
                ipaddress.summarize_address_range(
                    ipaddress.IPv4Address(start.strip()),
                    ipaddress.IPv4Address(end.strip()),
                )
            )
    log.info(f"Fetched {len(networks)} RU CIDRs from RIPE")
    return networks


def aggregate_to_prefix(networks: list[ipaddress.IPv4Network], prefix: int) -> list[ipaddress.IPv4Network]:
    """Aggregate networks to a given prefix level (e.g. /16 or /8)."""
    supernets = set()
    for net in networks:
        if net.prefixlen <= prefix:
            supernets.add(net)
        else:
            supernets.add(net.supernet(new_prefix=prefix))
    return list(ipaddress.collapse_addresses(sorted(supernets)))


def compute_complement(exclude: list[ipaddress.IPv4Network]) -> list[ipaddress.IPv4Network]:
    """Compute 0.0.0.0/0 minus the given networks."""
    result = [ipaddress.IPv4Network("0.0.0.0/0")]
    for net in sorted(exclude):
        new_result = []
        for r in result:
            if r.overlaps(net):
                new_result.extend(r.address_exclude(net))
            else:
                new_result.append(r)
        result = new_result
    return list(ipaddress.collapse_addresses(sorted(result)))


XRAY_CONFIG = "/usr/local/x-ui/bin/config.json"

def update_xray_routing():
    """Ensure xray config has RU split-tunneling rules (geoip:ru + geosite:ru -> direct)."""
    import json as _json

    with open(XRAY_CONFIG) as f:
        cfg = _json.load(f)

    routing = cfg.setdefault("routing", {})
    rules = routing.setdefault("rules", [])

    # Check if RU rules already exist
    has_geoip_ru = any(
        r.get("outboundTag") == "direct" and "geoip:ru" in r.get("ip", [])
        for r in rules
    )
    has_geosite_ru = any(
        r.get("outboundTag") == "direct" and "geosite:ru" in r.get("domain", [])
        for r in rules
    )

    changed = False

    if not has_geoip_ru:
        # Insert RU geoip rule before the last rule (bittorrent block)
        rules.insert(len(rules) - 1, {
            "type": "field",
            "outboundTag": "direct",
            "ip": ["geoip:ru"]
        })
        changed = True
        log.info("Added geoip:ru -> direct rule to xray config")

    if not has_geosite_ru:
        rules.insert(len(rules) - 1, {
            "type": "field",
            "outboundTag": "direct",
            "domain": ["geosite:ru"]
        })
        changed = True
        log.info("Added geosite:ru -> direct rule to xray config")

    if changed:
        with open(XRAY_CONFIG, "w") as f:
            _json.dump(cfg, f, indent=2)
        log.info("Updated xray config with RU split-tunneling rules")
    else:
        log.info("xray config already has RU split-tunneling rules")


def update_amneziawg(ru_networks: list[ipaddress.IPv4Network]):
    """Update WG_ALLOWED_IPS in .env and restart container."""
    aggregated = aggregate_to_prefix(ru_networks, AWG_PREFIX_LEVEL)
    log.info(f"AmneziaWG: {len(ru_networks)} RU CIDRs -> {len(aggregated)} after /{AWG_PREFIX_LEVEL} aggregation")

    complement = compute_complement(aggregated)
    log.info(f"AmneziaWG: {len(complement)} complement CIDRs (AllowedIPs entries)")

    allowed_ips = ", ".join(str(n) for n in complement)

    # Update .env
    env_path = os.path.join(PROJECT_DIR, ".env")
    with open(env_path) as f:
        content = f.read()

    if re.search(r"^WG_ALLOWED_IPS=", content, re.MULTILINE):
        content = re.sub(r"^WG_ALLOWED_IPS=.*$", f"WG_ALLOWED_IPS={allowed_ips}", content, flags=re.MULTILINE)
    else:
        content += f"\nWG_ALLOWED_IPS={allowed_ips}\n"

    with open(env_path, "w") as f:
        f.write(content)
    log.info("Updated WG_ALLOWED_IPS in .env")

    # Restart native AWG API to pick up new AllowedIPs from .env
    subprocess.run(
        ["sudo", "-n", "/usr/bin/systemctl", "restart", "awg-api"],
        check=True, capture_output=True, text=True,
    )
    log.info("Restarted awg-api service with new AllowedIPs")


def send_telegram(message: str):
    """Send notification to admin via Telegram."""
    with open(os.path.join(PROJECT_DIR, ".env")) as f:
        env = f.read()
    token = re.search(r"^TELEGRAM_BOT_TOKEN=(.+)$", env, re.MULTILINE)
    chat = re.search(r"^ADMIN_TG_ID=(.+)$", env, re.MULTILINE)
    if not token or not chat:
        return
    data = f"chat_id={chat.group(1)}&text={message}&parse_mode=HTML".encode()
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token.group(1)}/sendMessage",
        data=data,
    )
    try:
        urllib.request.urlopen(req, timeout=10)
    except Exception as e:
        log.warning(f"Failed to send Telegram alert: {e}")


def main():
    load_env()

    log.info("=== RU Routes Update Start ===")
    ru_networks = fetch_ru_cidrs()

    # Save raw CIDRs for reference
    with open(RU_CIDRS_FILE, "w") as f:
        for net in sorted(ru_networks):
            f.write(f"{net}\n")
    log.info(f"Saved {len(ru_networks)} CIDRs to {RU_CIDRS_FILE}")

    # Обновить sing-box rule-set для Happ
    try:
        subprocess.run(
            ["/home/alvik/vpn-service/scripts/generate_ru_ruleset.sh"],
            check=True, capture_output=True, text=True,
        )
        log.info("Generated sing-box geoip-ru rule-set")
    except Exception as e:
        log.error(f"Failed to generate sing-box rule-set: {e}")

    # Обновить xray config (VLESS/Hysteria split-tunneling)
    try:
        update_xray_routing()
    except Exception as e:
        log.error(f"xray routing update failed: {e}")
        errors.append(f"xray: {e}")

    errors = []

    try:
        update_amneziawg(ru_networks)
    except Exception as e:
        log.error(f"AmneziaWG update failed: {e}")
        errors.append(f"AmneziaWG: {e}")

    log.info("=== RU Routes Update End ===")

    if errors:
        send_telegram(f"⚠️ <b>RU Routes Update — partial failure</b>\n" + "\n".join(errors))
    else:
        send_telegram("✅ <b>RU Routes Updated</b>\nSplit tunneling refreshed for AmneziaWG + VLESS/Hysteria")


if __name__ == "__main__":
    main()
