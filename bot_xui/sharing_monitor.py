"""
Очистка устаревших IP из inbound_client_ips в x-ui.
Запускается периодически через APScheduler (каждый час).

Зачем: x-ui limitIp считает уникальные IP, а не устройства.
Мобильные пользователи с динамическим IP забивают лимит за 2-3 дня.
Очистка IP старше 2 часов освобождает слоты для новых подключений.
"""
import json
import sqlite3
import time
import logging

logger = logging.getLogger(__name__)

# IP старше этого возраста удаляется (секунды)
IP_MAX_AGE = 2 * 3600  # 2 часа

XUI_DB_PATH = "/home/alvik/vpn-service/docker/x-ui-data/x-ui.db"


def cleanup_stale_ips():
    """Удаляет IP старше IP_MAX_AGE из inbound_client_ips в x-ui SQLite."""
    try:
        conn = sqlite3.connect(XUI_DB_PATH, timeout=5)
        conn.row_factory = sqlite3.Row
        cur = conn.execute("SELECT id, client_email, ips FROM inbound_client_ips")
        now = time.time()
        cleaned = 0

        for row in cur:
            try:
                ips = json.loads(row["ips"])
            except (json.JSONDecodeError, TypeError):
                continue
            if not ips:
                continue

            original_count = len(ips)
            fresh_ips = [entry for entry in ips if now - entry.get("timestamp", 0) < IP_MAX_AGE]

            if len(fresh_ips) < original_count:
                removed = original_count - len(fresh_ips)
                conn.execute(
                    "UPDATE inbound_client_ips SET ips = ? WHERE id = ?",
                    (json.dumps(fresh_ips), row["id"]),
                )
                cleaned += removed
                logger.info(
                    f"[ip_cleanup] {row['client_email']}: removed {removed} stale IPs "
                    f"({len(fresh_ips)} remaining)"
                )

        if cleaned > 0:
            conn.commit()
            logger.info(f"[ip_cleanup] Total removed: {cleaned} stale IPs")
        conn.close()
    except Exception as e:
        logger.error(f"[ip_cleanup] Error: {e}")
