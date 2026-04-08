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
        # Log only the command name, not args (may contain passwords)
        logger.error(f"vpncmd error: command={args[0] if args else '?'}")
        raise RuntimeError(f"vpncmd failed: {args[0] if args else '?'}")
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
    """Set user expiry date. Format: YYYY/MM/DD or YYYY/MM/DD HH:MM:SS"""
    # vpncmd requires full datetime format: YYYY/MM/DD HH:MM:SS
    if len(expires_str) == 10:  # YYYY/MM/DD only
        expires_str = f"{expires_str} 23:59:59"
    try:
        _run("UserExpiresSet", username, f"/EXPIRES:{expires_str}")
        return True
    except RuntimeError as e:
        logger.error(f"Failed to set expiry for {username}: {e}")
        return False


def delete_user(username: str) -> bool:
    try:
        _run("UserDelete", username)
        logger.info(f"SoftEther user deleted: {username}")
        return True
    except RuntimeError as e:
        logger.error(f"Failed to delete user {username}: {e}")
        return False


def disable_user(username: str) -> bool:
    """Disable user by setting expiry to past date."""
    try:
        _run("UserExpiresSet", username, "/EXPIRES:2000/01/01 00:00:00")
        logger.info(f"SoftEther user disabled: {username}")
        return True
    except RuntimeError as e:
        logger.error(f"Failed to disable user {username}: {e}")
        return False


def list_sessions() -> list[dict]:
    """List active sessions (connected users) in the hub."""
    try:
        output = _run("SessionList")
    except RuntimeError:
        return []

    sessions = []
    current = {}
    for line in output.splitlines():
        if "|" not in line or line.startswith("---"):
            continue
        key, _, value = line.partition("|")
        key = key.strip()
        value = value.strip()

        if key == "Session Name":
            if current:
                sessions.append(current)
            current = {"session": value}
        elif key == "User Name":
            current["username"] = value
        elif key == "Source Host Name":
            current["source"] = value
        elif key == "Transfer Bytes":
            current["transfer_bytes"] = int(value.replace(",", "")) if value.replace(",", "").isdigit() else 0

    if current:
        sessions.append(current)

    # Filter out SecureNAT internal session
    return [s for s in sessions if s.get("username") != "SecureNAT"]


def list_users() -> list[dict]:
    """List all users in the hub. Returns list of dicts with user info."""
    try:
        output = _run("UserList")
    except RuntimeError:
        return []

    users = []
    current = {}
    for line in output.splitlines():
        if "|" not in line or line.startswith("---"):
            continue
        key, _, value = line.partition("|")
        key = key.strip()
        value = value.strip()

        if key == "User Name":
            if current:
                users.append(current)
            current = {"username": value}
        elif key == "Auth Method":
            current["auth"] = value
        elif key == "Num Logins":
            current["num_logins"] = int(value) if value.isdigit() else 0
        elif key == "Last Login":
            current["last_login"] = value if value != "(None)" else None
        elif key == "Expiration Date":
            current["expires"] = value if value != "No Expiration" else None
        elif key == "Transfer Bytes":
            current["transfer_bytes"] = int(value.replace(",", "")) if value.replace(",", "").isdigit() else 0

    if current:
        users.append(current)

    return users
