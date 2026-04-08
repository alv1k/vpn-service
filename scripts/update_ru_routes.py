#!/usr/bin/env python3
"""
Fetch Russian IP ranges and configure split tunneling:
- AmneziaWG: compute complement of RU CIDRs for AllowedIPs
- SoftEther: push RU CIDRs as static routes (bypass VPN)

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

VPNCMD = "/opt/softether/vpncmd"
SE_PASSWORD = None  # loaded from .env
SE_HUB = "VPN"

# Aggregation levels
AWG_PREFIX_LEVEL = 16   # /16 aggregation for AmneziaWG (manageable config size)
SE_PREFIX_LEVEL = 8     # /8 aggregation for SoftEther (max 64 entries)
SE_MAX_ROUTES = 64


def load_env():
    global SE_PASSWORD
    with open(os.path.join(PROJECT_DIR, ".env")) as f:
        for line in f:
            m = re.match(r"^SOFTETHER_SERVER_PASSWORD=(.+)$", line.strip())
            if m:
                SE_PASSWORD = m.group(1)


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
        ["sudo", "systemctl", "restart", "awg-api"],
        check=True, capture_output=True, text=True,
    )
    log.info("Restarted awg-api service with new AllowedIPs")


def update_softether(ru_networks: list[ipaddress.IPv4Network]):
    """Push RU routes to SoftEther DHCP (bypass VPN for RU IPs)."""
    aggregated = aggregate_to_prefix(ru_networks, SE_PREFIX_LEVEL)
    log.info(f"SoftEther: {len(ru_networks)} RU CIDRs -> {len(aggregated)} after /{SE_PREFIX_LEVEL} aggregation")

    if len(aggregated) > SE_MAX_ROUTES:
        log.warning(f"SoftEther: {len(aggregated)} routes exceeds {SE_MAX_ROUTES} limit, truncating")
        aggregated = aggregated[:SE_MAX_ROUTES]

    # Format: network/subnetmask/gateway — gateway 0.0.0.0 = client's original gateway
    route_entries = []
    for net in aggregated:
        route_entries.append(f"{net.network_address}/{net.netmask}/0.0.0.0")
    push_route = ",".join(route_entries)

    cmd_base = [
        VPNCMD, "127.0.0.1:5555", "/SERVER",
        f"/PASSWORD:{SE_PASSWORD}",
        f"/HUB:{SE_HUB}",
        "/CMD",
    ]

    # Get current DHCP settings to preserve them
    result = subprocess.run(cmd_base + ["DhcpGet"], capture_output=True, text=True, timeout=10)
    if result.returncode != 0:
        raise RuntimeError(f"DhcpGet failed: {result.stderr or result.stdout}")

    # Parse current values
    dhcp = {}
    for line in result.stdout.splitlines():
        if "|" in line:
            key, val = line.split("|", 1)
            dhcp[key.strip()] = val.strip()

    start = dhcp.get("Start Distribution Address Band", "192.168.30.10")
    end = dhcp.get("End Distribution Address Band", "192.168.30.200")
    mask = dhcp.get("Subnet Mask", "255.255.255.0")
    expire = dhcp.get("Lease Limit (Seconds)", "7200")
    gw = dhcp.get("Default Gateway Address", "192.168.30.1")
    dns1 = dhcp.get("DNS Server Address 1", "192.168.30.1")
    dns2 = dhcp.get("DNS Server Address 2", "None")
    domain = dhcp.get("Domain Name", "")
    savelog = dhcp.get("Save NAT and DHCP Operation Log", "Yes")

    if dns2 == "None":
        dns2 = "none"

    dhcp_cmd = [
        "DhcpSet",
        f"/START:{start}", f"/END:{end}", f"/MASK:{mask}",
        f"/EXPIRE:{expire}", f"/GW:{gw}",
        f"/DNS:{dns1}", f"/DNS2:{dns2}",
        f"/DOMAIN:{domain}", f"/LOG:{savelog.lower()}",
        f"/PUSHROUTE:{push_route}",
    ]

    result = subprocess.run(cmd_base + dhcp_cmd, capture_output=True, text=True, timeout=10)
    if result.returncode != 0:
        raise RuntimeError(f"DhcpSet failed: {result.stderr or result.stdout}")
    log.info(f"SoftEther: pushed {len(aggregated)} RU routes via DHCP")


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

    errors = []

    try:
        update_amneziawg(ru_networks)
    except Exception as e:
        log.error(f"AmneziaWG update failed: {e}")
        errors.append(f"AmneziaWG: {e}")

    try:
        update_softether(ru_networks)
    except Exception as e:
        log.error(f"SoftEther update failed: {e}")
        errors.append(f"SoftEther: {e}")

    log.info("=== RU Routes Update End ===")

    if errors:
        send_telegram(f"⚠️ <b>RU Routes Update — partial failure</b>\n" + "\n".join(errors))
    else:
        send_telegram("✅ <b>RU Routes Updated</b>\nSplit tunneling refreshed for AmneziaWG + SoftEther")


if __name__ == "__main__":
    main()
