"""
SoftEther VPN Server client wrapper.
Manages users via vpncmd CLI.
"""
import logging
import subprocess

from config import SOFTETHER_VPNCMD, SOFTETHER_SERVER_PASSWORD, SOFTETHER_HUB

logger = logging.getLogger(__name__)

_CMD_BASE = [
    SOFTETHER_VPNCMD, "127.0.0.1:5555", "/SERVER",
    f"/PASSWORD:{SOFTETHER_SERVER_PASSWORD}",
    f"/HUB:{SOFTETHER_HUB}",
    "/CMD",
]


def _run(*args) -> str:
    """Run a vpncmd command and return stdout. Raises RuntimeError on failure."""
    cmd = _CMD_BASE + list(args)
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
    if result.returncode != 0:
        logger.error(f"vpncmd error: {result.stderr or result.stdout}")
        raise RuntimeError(f"vpncmd failed: {' '.join(args)}")
    return result.stdout


def create_user(username: str, password: str) -> bool:
    """Create a user with password authentication."""
    try:
        _run("UserCreate", username, "/GROUP:none", "/REALNAME:none", "/NOTE:none")
        _run("UserPasswordSet", username, f"/PASSWORD:{password}")
        logger.info(f"SoftEther user created: {username}")
        return True
    except RuntimeError as e:
        logger.error(f"Failed to create SoftEther user {username}: {e}")
        return False


def set_user_expiry(username: str, expires_str: str) -> bool:
    """Set user expiry date. Format: YYYY/MM/DD"""
    try:
        _run("UserExpiresSet", username, f"/EXPIRES:{expires_str}")
        return True
    except RuntimeError:
        return False


def delete_user(username: str) -> bool:
    try:
        _run("UserDelete", username)
        logger.info(f"SoftEther user deleted: {username}")
        return True
    except RuntimeError:
        return False


def disable_user(username: str) -> bool:
    """Disable user by setting expiry to past date."""
    try:
        _run("UserExpiresSet", username, "/EXPIRES:2000/01/01")
        logger.info(f"SoftEther user disabled: {username}")
        return True
    except RuntimeError:
        return False
