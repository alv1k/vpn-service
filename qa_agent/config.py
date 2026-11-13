"""
Configuration for QA Agent Alex (QA Sentinel).
"""
import os
import sys
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV_FILE = os.path.join(BASE_DIR, ".env")
load_dotenv(ENV_FILE)

# Telegram MTProto Credentials
API_ID = int(os.getenv("TELEGRAM_API_ID", 2040))
API_HASH = os.getenv("TELEGRAM_API_HASH", "b18441a1ff607e10a989891a5462e627")
SESSION_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "qa_alex")

# Target Bot and Admin
BOT_USERNAME = os.getenv("QA_BOT_USERNAME", "tiin_service_bot")
ADMIN_TG_ID = int(os.getenv("ADMIN_TG_ID", 364224373))
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")

# AGY Inference Gateway
AGY_BASE_URL = os.getenv("AGY_BASE_URL", "http://127.0.0.1:8799/v1")
AGY_INTERNAL_KEY = os.getenv("AGY_INTERNAL_KEY", "")

# Xray binary and testing ports
XRAY_BIN = "/usr/local/x-ui/bin/xray-linux-amd64"
TEST_SOCKS_PORT = 10850
TEST_HTTP_PORT = 10851

# Thresholds & SLA
MAX_BOT_LATENCY_SEC = 2.5
DEFAULT_WAIT_TIMEOUT = 25.0
