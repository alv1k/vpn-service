#!/usr/bin/env python3
"""
Скрипт синхронизации expiry_time и восстановления ошибочно отключенных клиентов в 3x-ui:
1. Создает бэкап /etc/x-ui/x-ui.db
2. Синхронизирует client_traffics.expiry_time с clients.expiry_time
3. Восстанавливает enable=1 для всех пользователей с активной подпиской в MySQL
4. Обновляет inbounds.settings JSON
5. Перезапускает x-ui.service для применения новых настроек в Xray
"""
import os
import sys
import json
import sqlite3
import shutil
import subprocess
from datetime import datetime

sys.path.insert(0, "/home/alvik/vpn-service")

from dotenv import load_dotenv
load_dotenv("/home/alvik/vpn-service/.env")

from api.db import get_db

DB_PATH = "/etc/x-ui/x-ui.db"

def main():
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = f"{DB_PATH}.bak.sync_fix_{timestamp}"
    
    print(f"📦 1. Создание бэкапа {backup_path}...")
    shutil.copy2(DB_PATH, backup_path)
    print("✅ Бэкап создан успешно.")

    # Получаем активных пользователей из MySQL
    now_utc = datetime.utcnow()
    with get_db() as mdb:
        cur = mdb.cursor(dictionary=True)
        cur.execute("SELECT id, tg_id, subscription_until FROM users WHERE subscription_until > %s", (now_utc,))
        active_users = {u["tg_id"]: u for u in cur.fetchall() if u["tg_id"]}
    print(f"ℹ️ Найдено активных пользователей в MySQL: {len(active_users)}")

    xconn = sqlite3.connect(DB_PATH)
    xcur = xconn.cursor()

    # 2. Синхронизация client_traffics.expiry_time
    print("🔄 2. Синхронизация client_traffics.expiry_time...")
    xcur.execute("""
        UPDATE client_traffics
        SET expiry_time = (
            SELECT c.expiry_time
            FROM clients c
            WHERE c.email = client_traffics.email
        )
        WHERE EXISTS (
            SELECT 1 FROM clients c
            WHERE c.email = client_traffics.email
              AND c.expiry_time > client_traffics.expiry_time
              AND client_traffics.expiry_time > 0
        )
    """)
    traffics_updated = xcur.rowcount
    print(f"✅ Обновлено записей в client_traffics: {traffics_updated}")

    # 3. Восстановление пользователей с активной подпиской в MySQL, у которых enable = 0
    print("🔄 3. Восстановление enable=1 для активных пользователей...")
    xcur.execute("SELECT id, email, tg_id, enable, expiry_time FROM clients")
    all_clients = xcur.fetchall()
    
    restored_emails = []
    for cid, email, tg_id, enable, exp in all_clients:
        if tg_id in active_users and not enable:
            restored_emails.append(email)

    if restored_emails:
        print(f"Обнаружено отключенных клиентов с активной подпиской: {len(restored_emails)}")
        for email in restored_emails:
            print(f" - Восстанавливаем {email}")
            xcur.execute("UPDATE clients SET enable = 1 WHERE email = ?", (email,))
            xcur.execute("UPDATE client_traffics SET enable = 1 WHERE email = ?", (email,))
    else:
        print("Отключенных активных клиентов не обнаружено.")

    # 4. Обновление JSON-конфигураций inbounds
    print("🔄 4. Обновление inbounds.settings JSON...")
    # Создаем быстрый маппинг email -> (expiry_time, enable) из таблицы clients
    xcur.execute("SELECT email, expiry_time, enable FROM clients")
    client_state = {r[0]: (r[1], bool(r[2])) for r in xcur.fetchall()}

    inbounds = xcur.execute("SELECT id, settings FROM inbounds").fetchall()
    for inb_id, settings_json in inbounds:
        if not settings_json:
            continue
        try:
            settings = json.loads(settings_json)
            changed = False
            if "clients" in settings and isinstance(settings["clients"], list):
                for cl in settings["clients"]:
                    email = cl.get("email")
                    if email in client_state:
                        target_exp, target_enable = client_state[email]
                        if cl.get("expiryTime") != target_exp:
                            cl["expiryTime"] = target_exp
                            changed = True
                        if cl.get("enable") != target_enable:
                            cl["enable"] = target_enable
                            changed = True
            if changed:
                xcur.execute("UPDATE inbounds SET settings=? WHERE id=?", (json.dumps(settings), inb_id))
                print(f"✅ Инбаунд {inb_id} обновлен в JSON.")
        except Exception as e:
            print(f"❌ Ошибка при обновлении инбаунда {inb_id}: {e}")

    xconn.commit()
    xconn.close()
    print("✅ Изменения в базе SQLite успешно сохранены.")

    # 5. Перезапуск службы x-ui
    print("🚀 5. Перезапуск x-ui.service...")
    res = subprocess.run(["sudo", "systemctl", "restart", "x-ui.service"], capture_output=True, text=True)
    if res.returncode == 0:
        print("✅ x-ui.service успешно перезапущен!")
    else:
        print(f"⚠️ Ошибка перезапуска x-ui: {res.stderr}")

if __name__ == "__main__":
    main()
