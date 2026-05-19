"""
Очистка устаревших IP из inbound_client_ips в x-ui.
Запускается через systemd timer (xui-ip-cleanup.service) от root.

Зачем: x-ui limitIp считает уникальные IP, а не устройства.
Мобильные пользователи с динамическим IP забивают лимит за 2-3 дня.
Очистка IP старше 2 часов освобождает слоты для новых подключений.
"""
import json
import logging
import sqlite3
import time

logger = logging.getLogger(__name__)

XUI_DB_PATH = "/etc/x-ui/x-ui.db"
IP_MAX_AGE = 2 * 3600  # 2 hours


def cleanup_stale_ips():
    """Remove IPs older than IP_MAX_AGE from inbound_client_ips."""
    try:
        conn = sqlite3.connect(XUI_DB_PATH, timeout=10)
    except Exception as e:
        logger.warning(f"[ip_cleanup] Cannot open DB: {e}")
        return

    try:
        cur = conn.execute("SELECT id, client_email, ips FROM inbound_client_ips")
        now = time.time()
        cleaned = 0

        for row in cur:
            row_id, client_email, ips_raw = row
            try:
                ips = json.loads(ips_raw)
            except (json.JSONDecodeError, TypeError):
                continue
            if not ips:
                continue

            original_count = len(ips)
            fresh_ips = [e for e in ips if now - e.get("timestamp", 0) < IP_MAX_AGE]

            if len(fresh_ips) < original_count:
                removed = original_count - len(fresh_ips)
                conn.execute(
                    "UPDATE inbound_client_ips SET ips = ? WHERE id = ?",
                    (json.dumps(fresh_ips), row_id),
                )
                cleaned += removed
                logger.info(f"{client_email}: removed {removed} stale IPs ({len(fresh_ips)} remaining)")

        if cleaned > 0:
            conn.commit()
            logger.info(f"Total removed: {cleaned} stale IPs")
    except Exception as e:
        logger.error(f"[ip_cleanup] Failed: {e}")
    finally:
        conn.close()
