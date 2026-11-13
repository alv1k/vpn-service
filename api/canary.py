"""
Canary Trap Endpoints.
Catches fuzzers, scanners, and bot probes exploring non-existent API routes.
Instantly bans offending IPs via fail2ban.
"""
import logging
import subprocess
from fastapi import APIRouter, Request, Response

logger = logging.getLogger(__name__)
canary_router = APIRouter()

WHITELIST_IPS = {"127.0.0.1", "::1", "91.132.161.112", "193.23.211.141"}


def trigger_canary_ban(ip: str, path: str, method: str = ""):
    """Bans offending IP immediately via fail2ban and notifies via Telegram."""
    if not ip or ip == "unknown" or ip in WHITELIST_IPS:
        return

    logger.warning(f"🚨 CANARY TRAP TRIGGERED: IP {ip} probed fake route {path}. Banning IP immediately!")
    try:
        subprocess.run(
            ["/usr/bin/sudo", "-n", "/usr/bin/fail2ban-client", "set", "nginx-404-fuzz", "banip", ip],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
    except Exception as e:
        logger.error(f"Failed to ban IP {ip} via fail2ban: {e}")

    try:
        details = f"{method} {path}".strip()
        subprocess.Popen(
            [
                "/usr/bin/python3",
                "/home/alvik/scripts/notify_ban.py",
                "--ip", ip,
                "--jail", "canary-trap",
                "--reason", "Попадание в скрытую Canary-ловушку (сканирование API)",
                "--details", details,
            ]
        )
    except Exception as e:
        logger.error(f"Failed to send canary notification: {e}")


TRAP_ROUTES = [
    ("/api/auth/verify-code", ["POST"]),
    ("/api/auth/check-code", ["POST"]),
    ("/api/auth/confirm", ["POST"]),
    ("/api/auth/login", ["POST", "GET"]),
    ("/api/web/test/link", ["GET", "POST"]),
    ("/api/web/test/config", ["GET", "POST"]),
    ("/api/web/test/status", ["GET", "POST"]),
]


async def canary_handler(request: Request):
    ip = request.headers.get("x-real-ip") or (request.client.host if request.client else "unknown")
    trigger_canary_ban(ip, request.url.path, request.method)
    return Response(
        content='{"detail":"Not Found"}',
        status_code=404,
        media_type="application/json",
    )


for route_path, methods in TRAP_ROUTES:
    canary_router.add_api_route(route_path, canary_handler, methods=methods)
