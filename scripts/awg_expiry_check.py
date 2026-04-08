#!/usr/bin/env python3
"""
Автоотключение AWG клиентов с истекшей подпиской.
Включает обратно, если подписка продлена.

Запуск по cron каждые 30 минут:
  */30 * * * * /home/alvik/vpn-service/venv/bin/python3 /home/alvik/vpn-service/scripts/awg_expiry_check.py >> /home/alvik/vpn-service/logs/awg_expiry.log 2>&1
"""
import sys
import os
import logging
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("awg_expiry")

from api.db import execute_query
from awg_api import db as awg_db
from awg_api import awg_manager


def main():
    now = datetime.utcnow()
    changed = False

    # Все AWG ключи из vpn_keys
    awg_keys = execute_query(
        "SELECT client_id, client_name, expires_at FROM vpn_keys WHERE vpn_type = 'awg' AND client_id IS NOT NULL",
        fetch="all",
    ) or []

    # Все AWG клиенты из awg_clients
    awg_clients = {c["id"]: c for c in awg_db.list_clients()}

    for key in awg_keys:
        client_id = key["client_id"]
        client = awg_clients.get(client_id)
        if not client:
            continue

        expires_at = key["expires_at"]
        is_expired = expires_at and expires_at < now
        is_enabled = bool(client.get("enabled"))

        if is_expired and is_enabled:
            # Отключить — подписка истекла
            awg_db.update_client_enabled(client_id, False)
            log.info("DISABLED %s (%s) — expired %s", key["client_name"], client_id, expires_at)
            changed = True

        elif not is_expired and not is_enabled:
            # Включить обратно — подписка продлена
            awg_db.update_client_enabled(client_id, True)
            log.info("ENABLED %s (%s) — sub until %s", key["client_name"], client_id, expires_at)
            changed = True

    if changed:
        # Перегенерировать конфиг и перезагрузить интерфейс
        awg_manager.write_server_conf()
        if awg_manager.is_interface_up():
            awg_manager.reload_interface()
        log.info("Interface reloaded")
    else:
        log.info("No changes, %d keys checked", len(awg_keys))


if __name__ == "__main__":
    main()
