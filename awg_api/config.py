"""AWG 2.0 API configuration."""
import os
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

# API auth
API_PASSWORD = os.getenv("AMNEZIA_WG_API_PASSWORD", "")

# Network
AWG_INTERFACE = "awg0"
AWG_CONF_PATH = "/etc/amnezia/amneziawg/awg0.conf"
SERVER_ADDRESS = "10.10.0.1/24"
NETWORK_PREFIX = "10.10.0."
SERVER_ENDPOINT = os.getenv("WG_HOST", "91.132.161.112")
LISTEN_PORT = int(os.getenv("WG_PORT", "51888"))
DNS = os.getenv("WG_DEFAULT_DNS", "1.1.1.1,8.8.8.8")
MTU = int(os.getenv("WG_MTU", "1360"))
KEEPALIVE = int(os.getenv("WG_PERSISTENT_KEEPALIVE", "25"))

# AllowedIPs for clients (split-tunnel)
ALLOWED_IPS = os.getenv("WG_ALLOWED_IPS", "0.0.0.0/0")

# MySQL
MYSQL_HOST = os.getenv("MYSQL_HOST", "127.0.0.1")
MYSQL_PORT = int(os.getenv("MYSQL_PORT", "3306"))
MYSQL_USER = os.getenv("MYSQL_USER", "")
MYSQL_PASSWORD = os.getenv("MYSQL_PASSWORD", "")
MYSQL_DATABASE = os.getenv("MYSQL_DATABASE", "vpn")

# AWG 2.0 obfuscation params — will be loaded from DB or generated on first run
AWG_PARAMS_DEFAULTS = {
    "jc": 5,
    "jmin": 50,
    "jmax": 1000,
    "s1": 89,
    "s2": 121,
    "h1": (100000, 800000),
    "h2": (100000, 8000000),
    "h3": (100000, 80000000),
    "h4": (100000, 800000000),
}
