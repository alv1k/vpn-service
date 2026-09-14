"""
Модуль синхронизации клиентов между основным сервером и вторичными нодами (RU-шлюз).
Выполняет добавление, продление, деактивацию и сверку клиентов в 3x-ui на RU-ноде.
Все вызовы обёрнуты в try/except для изоляции от сбоев сети.
"""
import logging
import time
from datetime import datetime, timezone
import requests
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

from config import (
    RU_XUI_HOST,
    RU_XUI_USERNAME,
    RU_XUI_PASSWORD,
    RU_ACTIVE_INBOUND_IDS,
)
from bot_xui.utils import XUIClient

logger = logging.getLogger(__name__)

_ru_client_instance: XUIClient | None = None


def get_ru_xui_client() -> XUIClient | None:
    """Возвращает инициализированный клиент для работы с API RU 3x-ui."""
    global _ru_client_instance
    if not RU_XUI_HOST:
        return None
    if _ru_client_instance is None:
        _ru_client_instance = XUIClient(RU_XUI_HOST, RU_XUI_USERNAME, RU_XUI_PASSWORD)
    return _ru_client_instance


def sync_client_to_ru_node(
    email: str,
    expiry_ms: int | None = None,
    enable: bool | None = None,
    uuid_str: str | None = None,
    sub_id: str | None = None,
    tg_id: int = 0,
    limit_ip: int = 10,
    total_gb: int = 0,
    flow: str = "xtls-rprx-vision",
) -> bool:
    """
    Синхронизирует данные клиента с RU-нодой:
    1. Если клиент существует в RU 3x-ui — обновляет expiryTime, enable, limitIp.
    2. Если клиента нет — создаёт его в RU 3x-ui с сохранением оригинальных UUID/subId и привязкой к RU-инбаундам.
    """
    if not email or not RU_XUI_HOST:
        return False

    try:
        ru_xui = get_ru_xui_client()
        if not ru_xui:
            return False

        existing = ru_xui.get_client_by_email(email)

        if existing:
            client = existing.get("client", {})
            current_expiry = client.get("expiryTime", 0)
            new_expiry = expiry_ms if expiry_ms is not None else current_expiry
            new_enable = enable if enable is not None else client.get("enable", True)

            payload = {
                "email": email,
                "totalGB": total_gb if total_gb else client.get("totalGB", 0),
                "expiryTime": new_expiry,
                "tgId": tg_id or client.get("tgId", 0),
                "enable": bool(new_enable),
                "limitIp": limit_ip if limit_ip else client.get("limitIp", 0),
                "reset": client.get("reset", 0),
                "flow": client.get("flow") or flow,
            }

            url = f"{ru_xui.host}/panel/api/clients/update/{email}"
            resp = ru_xui._request("POST", url, json=payload, headers={"Content-Type": "application/json"})
            result = resp.json()
            if result.get("success"):
                logger.info(f"✅ [RU-Node] Successfully updated client {email} (expiry={new_expiry}, enable={new_enable})")
                return True
            else:
                logger.warning(f"⚠️ [RU-Node] Failed to update client {email}: {result}")
                return False
        else:
            # Клиент не найден на RU-ноде — создаём его
            logger.info(f"ℹ️ [RU-Node] Client {email} not found on RU node. Creating...")
            client_payload = {
                "email": email,
                "limitIp": limit_ip,
                "totalGB": total_gb,
                "expiryTime": expiry_ms if expiry_ms is not None else 0,
                "enable": True if enable is None else bool(enable),
                "tgId": tg_id,
                "reset": 0,
                "flow": flow,
            }
            if uuid_str:
                client_payload["id"] = str(uuid_str)
                client_payload["auth"] = str(uuid_str).replace("-", "")
            if sub_id:
                client_payload["subId"] = str(sub_id)

            payload = {
                "client": client_payload,
                "inboundIds": list(RU_ACTIVE_INBOUND_IDS),
            }

            url = f"{ru_xui.host}/panel/api/clients/add"
            resp = ru_xui._request("POST", url, json=payload, headers={"Content-Type": "application/json"})
            result = resp.json()
            if result.get("success"):
                logger.info(f"✅ [RU-Node] Successfully created client {email} on RU node")
                return True
            else:
                logger.warning(f"⚠️ [RU-Node] Failed to create client {email} on RU node: {result}")
                return False

    except Exception as e:
        logger.error(f"❌ [RU-Node] Exception syncing client {email} to RU node: {e}", exc_info=True)
        return False


def deactivate_ru_client(email: str) -> bool:
    """Деактивирует клиента (enable=False) на RU-ноде при возврате платежа или остановке подписки."""
    return sync_client_to_ru_node(email=email, enable=False)


def delete_ru_client(email: str) -> bool:
    """Удаляет клиента из RU 3x-ui."""
    if not email or not RU_XUI_HOST:
        return False
    try:
        ru_xui = get_ru_xui_client()
        if not ru_xui:
            return False
        return ru_xui.delete_client(inbound_id=0, client_email=email)
    except Exception as e:
        logger.error(f"❌ [RU-Node] Exception deleting client {email} from RU node: {e}")
        return False
