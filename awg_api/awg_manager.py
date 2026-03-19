"""AWG 2.0 interface manager — generates configs, manages awg0 interface."""
import logging
import subprocess
import tempfile
from typing import Optional

from . import db
from .config import (
    AWG_INTERFACE, AWG_CONF_PATH, SERVER_ADDRESS, SERVER_ENDPOINT,
    LISTEN_PORT, DNS, MTU, KEEPALIVE, ALLOWED_IPS,
)

logger = logging.getLogger(__name__)


def _run(cmd: list[str], check=True, capture=True) -> subprocess.CompletedProcess:
    logger.debug(f"Running: {' '.join(cmd)}")
    return subprocess.run(cmd, check=check, capture_output=capture, text=True)


def generate_keypair() -> tuple[str, str]:
    """Generate AWG private + public key pair."""
    priv = _run(["awg", "genkey"]).stdout.strip()
    result = subprocess.run(
        ["awg", "pubkey"], input=priv, capture_output=True, text=True, check=True
    )
    pub = result.stdout.strip()
    return priv, pub


def generate_preshared_key() -> str:
    return _run(["awg", "genpsk"]).stdout.strip()


def _format_awg_params(srv: dict) -> str:
    """Format AWG obfuscation params for config file."""
    lines = []
    for key in ("jc", "jmin", "jmax", "s1", "s2", "s3", "s4"):
        val = srv.get(key)
        if val is not None:
            lines.append(f"{key.capitalize() if len(key) <= 2 else key[0].upper() + key[1:]} = {val}")

    # H1-H4: can be ranges like "100000-800000"
    for key in ("h1", "h2", "h3", "h4"):
        val = srv.get(key)
        if val is not None:
            lines.append(f"{key.upper()} = {val}")

    # I1-I5: CPS concealment packets (AWG 2.0)
    for key in ("i1", "i2", "i3", "i4", "i5"):
        val = srv.get(key)
        if val:
            lines.append(f"{key.upper()} = {val}")

    return "\n".join(lines)


def write_server_conf():
    """Regenerate awg0.conf from database state."""
    srv = db.get_server_config()
    if not srv:
        raise RuntimeError("No server config in DB")

    clients = db.list_clients()
    awg_params = _format_awg_params(srv)

    conf = f"""[Interface]
PrivateKey = {srv['private_key']}
Address = {SERVER_ADDRESS}
ListenPort = {srv['listen_port']}
PostUp = iptables -t nat -A POSTROUTING -s 10.10.0.0/24 -o ens3 -j MASQUERADE; iptables -A INPUT -p udp -m udp --dport {srv['listen_port']} -j ACCEPT; iptables -A FORWARD -i {AWG_INTERFACE} -j ACCEPT; iptables -A FORWARD -o {AWG_INTERFACE} -j ACCEPT;
PostDown = iptables -t nat -D POSTROUTING -s 10.10.0.0/24 -o ens3 -j MASQUERADE; iptables -D INPUT -p udp -m udp --dport {srv['listen_port']} -j ACCEPT; iptables -D FORWARD -i {AWG_INTERFACE} -j ACCEPT; iptables -D FORWARD -o {AWG_INTERFACE} -j ACCEPT;
{awg_params}
"""

    for c in clients:
        if c["enabled"]:
            conf += f"""
[Peer]
# {c['name']}
PublicKey = {c['public_key']}
PresharedKey = {c['preshared_key']}
AllowedIPs = {c['address']}/32
"""

    with open(AWG_CONF_PATH, "w") as f:
        f.write(conf)

    logger.info(f"Server config written: {AWG_CONF_PATH} ({len(clients)} clients)")


def generate_client_conf(client_id: str) -> Optional[str]:
    """Generate a client .conf file with AWG 2.0 params."""
    client = db.get_client(client_id)
    if not client:
        return None

    srv = db.get_server_config()
    if not srv:
        return None

    awg_params = _format_awg_params(srv)

    conf = f"""[Interface]
PrivateKey = {client['private_key']}
Address = {client['address']}/32
DNS = {DNS}
MTU = {MTU}
{awg_params}

[Peer]
PublicKey = {srv['public_key']}
PresharedKey = {client['preshared_key']}
AllowedIPs = {ALLOWED_IPS}
Endpoint = {SERVER_ENDPOINT}:{srv['listen_port']}
PersistentKeepalive = {KEEPALIVE}
"""
    return conf


def reload_interface():
    """Hot-reload awg0 config without dropping existing connections."""
    try:
        # Use awg syncconf for zero-downtime reload
        strip_result = subprocess.run(
            ["awg-quick", "strip", AWG_INTERFACE],
            capture_output=True, text=True, check=True,
        )
        with tempfile.NamedTemporaryFile(mode="w", suffix=".conf", delete=False) as f:
            f.write(strip_result.stdout)
            tmp_path = f.name

        _run(["awg", "syncconf", AWG_INTERFACE, tmp_path])
        subprocess.run(["rm", "-f", tmp_path], check=False)
        logger.info("Interface reloaded via syncconf")
    except subprocess.CalledProcessError as e:
        logger.error(f"Failed to reload interface: {e.stderr}")
        raise


def interface_up():
    """Bring up awg0 interface."""
    _run(["awg-quick", "up", AWG_INTERFACE])
    logger.info(f"{AWG_INTERFACE} is up")


def interface_down():
    """Bring down awg0 interface."""
    _run(["awg-quick", "down", AWG_INTERFACE], check=False)
    logger.info(f"{AWG_INTERFACE} is down")


def is_interface_up() -> bool:
    result = _run(["awg", "show", AWG_INTERFACE], check=False)
    return result.returncode == 0
