#!/usr/bin/env python3
"""
Скрипт добавления всех активных пользователей в Inbound 17 (AmneziaWG).
"""
import sys
import os
import sqlite3
import json
import logging
from datetime import datetime, timezone
import pymysql
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv('/home/alvik/vpn-service/.env')

from bot_xui.awg_manager import get_or_create_3xui_awg_client
from api.db import upsert_vpn_key

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

def main():
    logger.info("🚀 Starting AmneziaWG provisioning for active clients...")
    
    # 1. Connect MySQL to get active users
    conn_mysql = pymysql.connect(
        host=os.getenv('MYSQL_HOST', '127.0.0.1'),
        user=os.getenv('MYSQL_USER', 'alvik'),
        password=os.getenv('MYSQL_PASSWORD'),
        database=os.getenv('MYSQL_DATABASE', 'vpn'),
        cursorclass=pymysql.cursors.DictCursor
    )
    
    active_targets = {}  # tg_id -> {expiry_ms, expires_at}
    now = datetime.now()
    now_ms = int(time_now := datetime.now(timezone.utc).timestamp() * 1000)

    with conn_mysql.cursor() as cur:
        cur.execute("SELECT tg_id, subscription_until FROM users WHERE subscription_until > NOW() AND tg_id IS NOT NULL")
        for u in cur.fetchall():
            tg_id = int(u["tg_id"])
            sub_until = u["subscription_until"]
            exp_ms = int(sub_until.replace(tzinfo=timezone.utc).timestamp() * 1000)
            active_targets[tg_id] = {
                "tg_id": tg_id,
                "expiry_ms": exp_ms,
                "expires_at": sub_until
            }

    # 2. Also check 3x-ui other inbounds for active tiin_ clients
    with sqlite3.connect('/etc/x-ui/x-ui.db') as conn_sqlite:
        cur = conn_sqlite.cursor()
        cur.execute("SELECT settings FROM inbounds WHERE id != 17")
        for row in cur.fetchall():
            if not row[0]: continue
            s = json.loads(row[0])
            for cl in s.get('clients', []):
                email = cl.get('email', '')
                exp = cl.get('expiryTime', 0)
                en = cl.get('enable', True)
                if (exp == 0 or exp > now_ms) and en:
                    # extract tg_id
                    tg_id = cl.get('tgId')
                    if not tg_id or tg_id == 0:
                        parts = email.replace('tiin_', '').replace('vless_', '').split('_')
                        if parts[0].isdigit():
                            tg_id = int(parts[0])
                    if tg_id and tg_id > 0 and tg_id not in active_targets:
                        sub_dt = datetime.fromtimestamp(exp / 1000, tz=timezone.utc) if exp > 0 else datetime(2099, 1, 1, tzinfo=timezone.utc)
                        active_targets[tg_id] = {
                            "tg_id": tg_id,
                            "expiry_ms": exp,
                            "expires_at": sub_dt
                        }

    logger.info(f"Found {len(active_targets)} active users to provision in Inbound 17")
    
    count_added = 0
    for tg_id, info in active_targets.items():
        try:
            client_name = f"awg_{tg_id}"
            res = get_or_create_3xui_awg_client(tg_id=tg_id, expiry_ms=info["expiry_ms"], client_name=client_name)
            
            # Save into MySQL vpn_keys
            upsert_vpn_key(
                tg_id=tg_id,
                payment_id=None,
                client_id=res["client_id"],
                client_name=res["client_name"],
                client_ip=res["client_ip"],
                client_public_key=res.get("public_key"),
                vless_link=res["config"],
                expires_at=info["expires_at"],
                vpn_type="awg"
            )
            count_added += 1
            logger.info(f"✅ Provisioned AWG for tg_id={tg_id} -> IP {res['client_ip']}")
        except Exception as e:
            logger.error(f"❌ Failed to provision for tg_id={tg_id}: {e}")

    logger.info(f"🎉 Successfully provisioned {count_added} clients into Inbound 17!")

if __name__ == '__main__':
    main()
