import base64
from config import WG_SERVER_PUBLIC_KEY, WG_SERVER_ENDPOINT, WG_BIN 
from nacl.public import PrivateKey
from api.db import get_used_client_ips
import subprocess

# ===== НАСТРОЙКИ =====

WG_NETWORK = "10.10.0."
DNS = "1.1.1.1"

SERVER_PUBLIC_KEY = WG_SERVER_PUBLIC_KEY
SERVER_ENDPOINT = WG_SERVER_ENDPOINT  # example: 1.2.3.4:51820

if not SERVER_PUBLIC_KEY:
    raise RuntimeError("WG_SERVER_PUBLIC_KEY not set")

if not SERVER_ENDPOINT:
    raise RuntimeError("WG_SERVER_ENDPOINT not set")


# ===== КЛЮЧИ =====

def generate_keypair() -> tuple[str, str]:
    """
    Генерирует private/public ключи WireGuard (Curve25519)
    """
    private = PrivateKey.generate()
    public = private.public_key

    private_key = base64.b64encode(bytes(private)).decode()
    public_key = base64.b64encode(bytes(public)).decode()

    return private_key, public_key


# ===== IP =====

def choose_client_ip() -> str:
    """
    Выбирает первый свободный IP из БД
    """
    used_ips = get_used_client_ips()  # set[str]

    for i in range(2, 255):
        ip = f"{WG_NETWORK}{i}"
        if ip not in used_ips:
            return ip

    raise RuntimeError("❌ No free IPs available")


# ===== CONFIG =====

def generate_client_config(
    client_private_key: str,
    client_ip: str,
) -> str:
    return f"""[Interface]
PrivateKey = {client_private_key}
Address = {client_ip}/32
DNS = {DNS}

[Peer]
PublicKey = {SERVER_PUBLIC_KEY}
Endpoint = {SERVER_ENDPOINT}
AllowedIPs = 0.0.0.0/0
PersistentKeepalive = 25
"""

def add_peer_via_wg_set(
    interface: str,
    client_public_key: str,
    client_ip: str,
) -> None:
    """
    Добавляет peer в WireGuard через `wg set`
    Работает без root, если wg имеет cap_net_admin
    """
    try:
        subprocess.run(
            [
                WG_BIN,
                "set",
                interface,
                "peer",
                client_public_key,
                "allowed-ips",
                f"{client_ip}/32",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as e:
        raise RuntimeError(
            f"wg set failed: {e.stderr.strip()}"
        )

def peer_exists(interface: str, client_public_key: str) -> bool:
    result = subprocess.run(
        ["wg", "show", interface, "peers"],
        capture_output=True,
        text=True,
        check=True,
    )
    peers = result.stdout.splitlines()
    return client_public_key in peers

def provision_client(interface: str) -> dict:
    client_private, client_public = generate_keypair()
    client_ip = choose_client_ip()

    if peer_exists(interface, client_public):
        raise RuntimeError("Peer already exists")

    add_peer_via_wg_set(
        interface=interface,
        client_public_key=client_public,
        client_ip=client_ip,
    )

    client_config = generate_client_config(
        client_private_key=client_private,
        client_ip=client_ip,
    )

    return {
        "private_key": client_private,
        "public_key": client_public,
        "ip": client_ip,
        "config": client_config,
    }