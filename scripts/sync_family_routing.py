#!/usr/bin/env python3
"""
Скрипт синхронизации списка доверенных пользователей (VIP/Family) с RU-нодой (Selectel).
Выбирает из MySQL:
1. Пользователей с relatives > 0 (родственники)
2. Пользователей со стажем более 5 месяцев и активной подпиской
3. Админские аккаунты
И обновляет правило routing.rules для 'home-portal' на RU-ноде.
"""
import os
import sys
import json
import sqlite3
import logging
import pymysql

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import (
    MYSQL_HOST, MYSQL_PORT, MYSQL_USER, MYSQL_PASSWORD, MYSQL_DATABASE,
    RU_XUI_HOST
)
from bot_xui.multi_node import get_ru_xui_client

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

ADMIN_TG_IDS = [364224373]


def get_trusted_user_identifiers() -> list[str]:
    """Возвращает список 3x-ui client email идентификаторов для доверенных пользователей."""
    conn = pymysql.connect(
        host=MYSQL_HOST or "127.0.0.1",
        port=MYSQL_PORT or 3306,
        user=MYSQL_USER or "alvik",
        password=MYSQL_PASSWORD,
        database=MYSQL_DATABASE or "vpn",
        cursorclass=pymysql.cursors.DictCursor
    )

    trusted = set(["tiin_web_32"])
    for aid in ADMIN_TG_IDS:
        trusted.add(f"tiin_{aid}")

    try:
        with conn.cursor() as cur:
            # 1. Родственники
            cur.execute("""
                SELECT id, tg_id, web_token 
                FROM users 
                WHERE relatives IS NOT NULL AND relatives > 0;
            """)
            for row in cur.fetchall():
                if row.get("tg_id"):
                    trusted.add(f"tiin_{row['tg_id']}")
                if row.get("id"):
                    trusted.add(f"tiin_web_{row['id']}")

            # 2. Стаж > 5 месяцев + активная подписка
            cur.execute("""
                SELECT id, tg_id, web_token 
                FROM users 
                WHERE created_at <= DATE_SUB(NOW(), INTERVAL 5 MONTH) 
                  AND subscription_until > NOW();
            """)
            for row in cur.fetchall():
                if row.get("tg_id"):
                    trusted.add(f"tiin_{row['tg_id']}")
                if row.get("id"):
                    trusted.add(f"tiin_web_{row['id']}")

    finally:
        conn.close()

    return sorted(list(trusted))


def sync_ru_portal_routing():
    """Обеспечивает отсутствие устаревшего правила home-portal в xrayTemplateConfig на RU-ноде."""
    import subprocess
    import base64

    remote_code = """
import json, sqlite3, subprocess

conn = sqlite3.connect('/etc/x-ui/x-ui.db')
c = conn.cursor()
c.execute('SELECT value FROM settings WHERE key = "xrayTemplateConfig";')
row = c.fetchone()
if not row:
    print('No template found')
    exit(1)

tmpl = json.loads(row[0])
rules = tmpl.get('routing', {}).get('rules', [])
filtered_rules = [r for r in rules if r.get('outboundTag') != 'home-portal']

if len(filtered_rules) != len(rules):
    tmpl['routing']['rules'] = filtered_rules
    c.execute('UPDATE settings SET value = ? WHERE key = "xrayTemplateConfig";', (json.dumps(tmpl),))
    conn.commit()
    subprocess.run(['systemctl', 'restart', 'x-ui'], check=True)
    print('REMOVED_AND_RESTARTED')
else:
    print('CLEAN')
conn.close()
"""
    b64_code = base64.b64encode(remote_code.encode()).decode()
    cmd = f"echo {b64_code} | base64 -d | python3"

    res = subprocess.run(
        ["ssh", "-o", "ConnectTimeout=5", "root@10.50.0.2", cmd],
        capture_output=True, text=True
    )
    if res.returncode == 0 and ("REMOVED_AND_RESTARTED" in res.stdout or "CLEAN" in res.stdout):
        logger.info(f"✅ Статус правила home-portal на RU-ноде: {res.stdout.strip()}")
    else:
        logger.error(f"❌ Ошибка проверки правил на RU-ноде: {res.stderr or res.stdout}")


if __name__ == "__main__":
    sync_ru_portal_routing()
