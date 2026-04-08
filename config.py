import sys
import os
import logging
from dotenv import load_dotenv

# .env в корне проекта
DOTENV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
load_dotenv(DOTENV_PATH)


# YooKassa: боевые креды
YOO_KASSA_SHOP_ID = os.getenv("YOO_KASSA_SHOP_ID")
YOO_KASSA_SECRET_KEY = os.getenv("YOO_KASSA_SECRET_KEY")

# YooKassa: тестовые креды (для админского тест-режима)
YOO_KASSA_TEST_SHOP_ID = os.getenv("YOO_KASSA_TEST_SHOP_ID")
YOO_KASSA_TEST_SECRET_KEY = os.getenv("YOO_KASSA_TEST_SECRET_KEY")

WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

WG_SERVER_PUBLIC_KEY = os.getenv("WG_SERVER_PUBLIC_KEY")
WG_SERVER_ENDPOINT = os.getenv("WG_SERVER_ENDPOINT")
WG_DNS = os.getenv("WG_DNS")

MYSQL_HOST = os.getenv("MYSQL_HOST")
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

# Server location (for display in client apps)
SERVER_LOCATION = os.getenv("SERVER_LOCATION", "Germany")

# AmneziaWG
AMNEZIA_CONTAINER = os.getenv("AMNEZIA_CONTAINER")

XUI_SUB_HOST = os.getenv("XUI_SUB_HOST")  # e.g. https://344988.snk.wtf:2096
XUI_SUB_PATH = os.getenv("XUI_SUB_PATH")  # e.g. m5t2vx84r

# SoftEther VPN
SOFTETHER_VPNCMD = os.getenv("SOFTETHER_VPNCMD", "/opt/softether/vpncmd")
SOFTETHER_SERVER_PASSWORD = os.getenv("SOFTETHER_SERVER_PASSWORD")
SOFTETHER_HUB = os.getenv("SOFTETHER_HUB", "VPN")
SOFTETHER_CONNECT_HOST = os.getenv("SOFTETHER_CONNECT_HOST")
SOFTETHER_CONNECT_PORT = int(os.getenv("SOFTETHER_CONNECT_PORT", "443"))

# SMTP (for email auth codes)
SMTP_HOST = os.getenv("SMTP_HOST")
SMTP_PORT = os.getenv("SMTP_PORT", "587")
SMTP_USER = os.getenv("SMTP_USER")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD")
SMTP_FROM = os.getenv("SMTP_FROM")

# MTProto Proxy
MTPROTO_SERVER = os.getenv("MTPROTO_SERVER", "tiinservice.ru")
MTPROTO_PORT = os.getenv("MTPROTO_PORT", "8443")
MTPROTO_SECRET = os.getenv("MTPROTO_SECRET", "")

REFERRAL_REWARD_DAYS = int(os.getenv("REFERRAL_REWARD_DAYS", "3"))
REFERRAL_NEWCOMER_DAYS = int(os.getenv("REFERRAL_NEWCOMER_DAYS", "3"))

_admin_tg_raw = os.getenv("ADMIN_TG_ID")
if not _admin_tg_raw:
    raise RuntimeError("ADMIN_TG_ID must be set in .env")
ADMIN_TG_ID = int(_admin_tg_raw)


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
    },
    "VLESS": {
        "VLESS_DOMAIN": VLESS_DOMAIN,
        "VLESS_PBK": VLESS_PBK,
        "VLESS_SID": VLESS_SID,
        "VLESS_SNI": VLESS_SNI,
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

    logger.info("Config validation passed")