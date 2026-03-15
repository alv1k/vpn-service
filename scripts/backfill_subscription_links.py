"""
Одноразовый скрипт: заполняет subscription_link в vpn_keys
для всех существующих VLESS клиентов из данных 3x-ui панели.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
import logging
from config import XUI_HOST, XUI_USERNAME, XUI_PASSWORD, XUI_SUB_HOST, XUI_SUB_PATH
from bot_xui.utils import XUIClient
from api.db import execute_query

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def main():
    xui = XUIClient(XUI_HOST, XUI_USERNAME, XUI_PASSWORD)

    # Получаем все VLESS ключи без subscription_link
    rows = execute_query(
        "SELECT tg_id, client_name FROM vpn_keys WHERE vpn_type = 'vless' AND subscription_link IS NULL",
        fetch='all'
    )
    logger.info(f"Found {len(rows)} VLESS keys without subscription_link")

    if not rows:
        return

    # Загружаем всех клиентов из 3x-ui за один запрос
    response = xui._request("GET", f"{xui.host}/panel/api/inbounds/list")
    result = response.json()
    if not result.get('success'):
        logger.error("Failed to get inbounds from 3x-ui")
        return

    if not XUI_SUB_HOST or not XUI_SUB_PATH:
        logger.error("XUI_SUB_HOST or XUI_SUB_PATH not set in .env")
        return

    # Строим маппинг tg_id -> sub_url
    tg_id_to_sub = {}
    for inbound in result.get('obj', []):
        settings = json.loads(inbound.get('settings', '{}'))
        for client in settings.get('clients', []):
            tg_id = client.get('tgId')
            sub_id = client.get('subId')
            if tg_id and sub_id:
                tg_id_to_sub[tg_id] = f"{XUI_SUB_HOST}/sub/{XUI_SUB_PATH}/{sub_id}"

    updated = 0
    for row in rows:
        tg_id = row['tg_id']
        sub_url = tg_id_to_sub.get(tg_id)
        if sub_url:
            execute_query(
                "UPDATE vpn_keys SET subscription_link = %s WHERE tg_id = %s AND vpn_type = 'vless'",
                (sub_url, tg_id)
            )
            logger.info(f"Updated tg_id={tg_id}: {sub_url}")
            updated += 1
        else:
            logger.warning(f"No sub_id found in 3x-ui for tg_id={tg_id}")

    logger.info(f"Done. Updated {updated}/{len(rows)} keys.")


if __name__ == "__main__":
    main()
