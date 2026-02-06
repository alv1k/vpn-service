import os
from dotenv import load_dotenv

load_dotenv()

YOO_KASSA_SHOP_ID = os.getenv("YOO_KASSA_SHOP_ID")
YOO_KASSA_SECRET_KEY = os.getenv("YOO_KASSA_SECRET_KEY")

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
