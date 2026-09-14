import sys
import os
import logging
from dotenv import load_dotenv

# .env в корне проекта (переопределяется через ENV_FILE)
_env_file = os.getenv("ENV_FILE", ".env")
DOTENV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), _env_file)
load_dotenv(DOTENV_PATH)

# YooKassa: боевые креды
YOO_KASSA_SHOP_ID = os.getenv("YOO_KASSA_SHOP_ID")
YOO_KASSA_SECRET_KEY = os.getenv("YOO_KASSA_SECRET_KEY")

# YooKassa: тестовые креды (для админского тест-режима)
YOO_KASSA_TEST_SHOP_ID = os.getenv("YOO_KASSA_TEST_SHOP_ID")
YOO_KASSA_TEST_SECRET_KEY = os.getenv("YOO_KASSA_TEST_SECRET_KEY")

WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
BOT_USERNAME = os.getenv("BOT_USERNAME", "tiin_service_bot")

WG_SERVER_PUBLIC_KEY = os.getenv("WG_SERVER_PUBLIC_KEY")
WG_SERVER_ENDPOINT = os.getenv("WG_SERVER_ENDPOINT")
WG_DNS = os.getenv("WG_DNS")

MYSQL_HOST = os.getenv("MYSQL_HOST")
MYSQL_PORT = int(os.getenv("MYSQL_PORT", "3306"))
MYSQL_USER = os.getenv("MYSQL_USER")
MYSQL_PASSWORD = os.getenv("MYSQL_PASSWORD")
MYSQL_DATABASE = os.getenv("MYSQL_DATABASE")

AMNEZIA_WG_API_URL = os.getenv("AMNEZIA_WG_API_URL", "http://localhost:51821")
AMNEZIA_WG_API_PASSWORD = os.getenv("AMNEZIA_WG_API_PASSWORD")

# WG_BIN = os.getenv("WG_BIN")
# WG_INTERFACE = os.getenv("WG_INTERFACE")
# WG_CONF_PATH = os.getenv("WG_CONF_PATH")



# 3x-ui API
XUI_HOST = os.getenv("XUI_HOST")
XUI_USERNAME = os.getenv("XUI_USERNAME")
XUI_PASSWORD = os.getenv("XUI_PASSWORD")
XUI_TOTP_SECRET = os.getenv("XUI_TOTP_SECRET", "")
XUI_SUB_PATH = os.getenv("XUI_SUB_PATH")

# VLESS настройки
VLESS_DOMAIN = os.getenv("VLESS_DOMAIN")
VLESS_PORT = int(os.getenv("VLESS_PORT", "443"))
VLESS_PATH = os.getenv("VLESS_PATH")
VLESS_INBOUND_ID = int(os.getenv("VLESS_INBOUND_ID", "1"))
VLESS_PBK = os.getenv("VLESS_PBK")
_VLESS_SID_RAW = os.getenv("VLESS_SID", "")
VLESS_SID_LIST = [s.strip() for s in _VLESS_SID_RAW.split(",") if s.strip()]
VLESS_SID = VLESS_SID_LIST[0] if VLESS_SID_LIST else ""
VLESS_SNI = os.getenv("VLESS_SNI")

# Hysteria 2 настройки
HYSTERIA_PORT = int(os.getenv("HYSTERIA_PORT", "54321"))
HYSTERIA_SNI = os.getenv("HYSTERIA_SNI") or VLESS_SNI or VLESS_DOMAIN
HYSTERIA_INBOUND_ID = int(os.getenv("HYSTERIA_INBOUND_ID", "4"))

# VLESS XHTTP inbound (дополнительный на Reality)
VLESS_HTTP_PORT = int(os.getenv("VLESS_HTTP_PORT", "47447"))
VLESS_XHTTP_PBK = os.getenv("VLESS_XHTTP_PBK")
_VLESS_XHTTP_SID_RAW = os.getenv("VLESS_XHTTP_SID", "")
VLESS_XHTTP_SID_LIST = [s.strip() for s in _VLESS_XHTTP_SID_RAW.split(",") if s.strip()]
VLESS_XHTTP_SID = VLESS_XHTTP_SID_LIST[0] if VLESS_XHTTP_SID_LIST else (VLESS_SID_LIST[0] if VLESS_SID_LIST else "")
VLESS_HTTP_INBOUND_ID = int(os.getenv("VLESS_HTTP_INBOUND_ID", "2"))
VLESS_WS_INBOUND_ID = int(os.getenv("VLESS_WS_INBOUND_ID", "9"))
VLESS_REALITY_V1_INBOUND_ID = int(os.getenv("VLESS_REALITY_V1_INBOUND_ID", "10"))

# Centralized active inbound IDs list for client provisioning
_DEFAULT_ACTIVE_INBOUNDS = "1,2,4,10,14"
_RAW_ACTIVE_INBOUNDS = os.getenv("ACTIVE_INBOUND_IDS", _DEFAULT_ACTIVE_INBOUNDS)
ACTIVE_INBOUND_IDS = [int(x.strip()) for x in _RAW_ACTIVE_INBOUNDS.split(",") if x.strip().isdigit()]

# RU Server (Secondary Node) 3x-ui API
RU_XUI_HOST = os.getenv("RU_XUI_HOST", "https://139.100.207.18:2053/0ruabxwdsz96R7jSIN")
RU_XUI_USERNAME = os.getenv("RU_XUI_USERNAME", XUI_USERNAME)
RU_XUI_PASSWORD = os.getenv("RU_XUI_PASSWORD", XUI_PASSWORD)
_RAW_RU_INBOUNDS = os.getenv("RU_ACTIVE_INBOUND_IDS", "1,2")
RU_ACTIVE_INBOUND_IDS = [int(x.strip()) for x in _RAW_RU_INBOUNDS.split(",") if x.strip().isdigit()]

# Server location (for display in client apps)
SERVER_LOCATION = os.getenv("SERVER_LOCATION", "Germany")

# Winback Campaign Constants
WINBACK_DISCOUNT_PERCENT = int(os.getenv("WINBACK_DISCOUNT_PERCENT", "20"))
WINBACK_GIFT_DAYS = int(os.getenv("WINBACK_GIFT_DAYS", "3"))

# AmneziaWG (3x-ui Inbound)
AWG_INBOUND_ID = int(os.getenv("AWG_INBOUND_ID", "17"))
AWG_PORT = int(os.getenv("AWG_PORT", "443"))
AWG_ENDPOINT = os.getenv("AWG_ENDPOINT", f"{VLESS_DOMAIN}:{AWG_PORT}")
AWG_MTU = int(os.getenv("AWG_MTU", "1280"))
AWG_ALLOWED_IPS = os.getenv(
    "AWG_ALLOWED_IPS",
    "1.1.1.1/32, 8.8.8.8/32, 10.8.1.0/24, 20.0.0.0/11, 31.13.0.0/16, 34.64.0.0/10, 45.64.40.0/22, 57.144.0.0/14, "
    "64.233.160.0/19, 66.22.196.0/22, 66.220.144.0/20, 69.63.176.0/20, 69.171.224.0/19, 74.125.0.0/16, "
    "104.16.0.0/12, 104.244.42.0/24, 108.177.0.0/17, 129.134.0.0/16, 142.250.0.0/15, 157.240.0.0/16, "
    "162.158.0.0/15, 172.64.0.0/13, 172.217.0.0/16, 173.194.0.0/16, 185.89.216.0/22, 192.178.0.0/15, "
    "199.16.0.0/12, 209.85.128.0/17, 216.58.192.0/19, 216.239.32.0/19"
)
# SMTP (for email auth codes)
SMTP_HOST = os.getenv("SMTP_HOST")
SMTP_PORT = os.getenv("SMTP_PORT", "587")
SMTP_USER = os.getenv("SMTP_USER")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD")
SMTP_FROM = os.getenv("SMTP_FROM")

# MTProto Proxy
MTPROTO_SERVER = os.getenv("MTPROTO_SERVER", "tiinservice.online")
MTPROTO_PORT = os.getenv("MTPROTO_PORT", "8443")
MTPROTO_SECRET = os.getenv("MTPROTO_SECRET", "")

REFERRAL_REWARD_DAYS = int(os.getenv("REFERRAL_REWARD_DAYS", "10"))
REFERRAL_NEWCOMER_DAYS = int(os.getenv("REFERRAL_NEWCOMER_DAYS", "10"))

_admin_tg_raw = os.getenv("ADMIN_TG_ID")
if not _admin_tg_raw:
    raise RuntimeError("ADMIN_TG_ID must be set in .env")
ADMIN_TG_ID = int(_admin_tg_raw)

# Finance API
FINANCE_API_URL = os.getenv("FINANCE_API_URL", "http://127.0.0.1:3000")
FINANCE_BOT_USER = os.getenv("FINANCE_BOT_USER", "receipt_bot")
FINANCE_BOT_PASS = os.getenv("FINANCE_BOT_PASS", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

logger = logging.getLogger(__name__)

_REQUIRED_VARS = {
    "Core": {
        "TELEGRAM_BOT_TOKEN": TELEGRAM_BOT_TOKEN,
    },
    "YooKassa": {
        "YOO_KASSA_SHOP_ID": YOO_KASSA_SHOP_ID,
        "YOO_KASSA_SECRET_KEY": YOO_KASSA_SECRET_KEY,
    },
    "MySQL": {
        "MYSQL_HOST": MYSQL_HOST,
        "MYSQL_USER": MYSQL_USER,
        "MYSQL_PASSWORD": MYSQL_PASSWORD,
        "MYSQL_DATABASE": MYSQL_DATABASE,
    },
    "3x-ui": {
        "XUI_HOST": XUI_HOST,
        "XUI_USERNAME": XUI_USERNAME,
        "XUI_PASSWORD": XUI_PASSWORD,
        "XUI_SUB_PATH": XUI_SUB_PATH,
    },
    "VLESS": {
        "VLESS_DOMAIN": VLESS_DOMAIN,
        "VLESS_PBK": VLESS_PBK,
        "VLESS_SID": VLESS_SID,
        "VLESS_SNI": VLESS_SNI,
    },
    "Hysteria": {
        "HYSTERIA_PORT": HYSTERIA_PORT,
        "HYSTERIA_SNI": HYSTERIA_SNI,
    },
}


def validate_config():
    """Проверяет наличие всех обязательных переменных окружения."""
    missing = []
    for group, vars_dict in _REQUIRED_VARS.items():
        for name, value in vars_dict.items():
            if not value:
                missing.append(f"  [{group}] {name}")
    if missing:
        msg = "Missing required environment variables:\n" + "\n".join(missing)
        logger.critical(msg)
        sys.exit(1)
