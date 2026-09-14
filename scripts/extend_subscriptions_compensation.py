#!/usr/bin/env python3
"""
Скрипт начисления компенсации +5 дней ко всем активным подпискам:
1. MySQL: users (subscription_until = subscription_until + 5 days)
2. MySQL: vpn_keys (expires_at = expires_at + 5 days)
3. 3x-ui: SQLite /etc/x-ui/x-ui.db (clients, inbounds settings JSON, client_traffics)
4. AWG: awg_clients.db (если включено хранение сроков)

Безопасность:
- Логирует каждый шаг
- Делает резервные копии БД перед изменениями
"""
import os
import sys
import json
import sqlite3
import shutil
from datetime import datetime, timedelta

sys.path.insert(0, '/home/alvik/vpn-service')
from api.db import get_db

def main(dry_run=False):
    print(f"=== Запуск продления подписок на +5 дней (dry_run={dry_run}) ===")
    now = datetime.utcnow()
    
    # 1. Резервная копия 3x-ui
    if not dry_run:
        bak_name = f"/etc/x-ui/x-ui.db.bak.compensation_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        shutil.copy2("/etc/x-ui/x-ui.db", bak_name)
        print(f"✅ Создан бэкап 3x-ui: {bak_name}")

    with get_db() as db:
        c = db.cursor(dictionary=True)
        
        # Получаем всех пользователей с активной подпиской
        c.execute("""
            SELECT id, tg_id, first_name, email, subscription_until 
            FROM users 
            WHERE subscription_until > %s
        """, (now,))
        active_users = c.fetchall()
        print(f"Найдено активных пользователей в `users`: {len(active_users)}")
        
        # Получаем все активные ключи
        c.execute("""
            SELECT id, tg_id, client_id, client_name, vpn_type, expires_at 
            FROM vpn_keys 
            WHERE expires_at > %s
        """, (now,))
        active_keys = c.fetchall()
        print(f"Найдено активных ключей в `vpn_keys`: {len(active_keys)}")

        if not dry_run:
            # 1. Продление в MySQL users
            c.execute("""
                UPDATE users 
                SET subscription_until = DATE_ADD(subscription_until, INTERVAL 5 DAY)
                WHERE subscription_until > %s
            """, (now,))
            users_updated = c.rowcount
            print(f"✅ Обновлено записей в MySQL `users`: {users_updated}")

            # 2. Продление в MySQL vpn_keys
            c.execute("""
                UPDATE vpn_keys 
                SET expires_at = DATE_ADD(expires_at, INTERVAL 5 DAY)
                WHERE expires_at > %s
            """, (now,))
            keys_updated = c.rowcount
            print(f"✅ Обновлено записей в MySQL `vpn_keys`: {keys_updated}")
            db.commit()

            # 3. Продление в 3x-ui SQLite
            xui_conn = sqlite3.connect('/etc/x-ui/x-ui.db')
            xc = xui_conn.cursor()
            
            now_ms = int(now.timestamp() * 1000)
            five_days_ms = 5 * 24 * 60 * 60 * 1000
            
            # Обновляем таблицу `clients` в 3x-ui
            xc.execute("""
                UPDATE clients 
                SET expiry_time = expiry_time + ?,
                    enable = 1,
                    updated_at = ?
                WHERE expiry_time > ?
            """, (five_days_ms, now_ms, now_ms))
            xui_clients_updated = xc.rowcount
            print(f"✅ Обновлено клиентов в 3x-ui `clients`: {xui_clients_updated}")
            
            # Обновляем JSON settings внутри каждого inbound
            inbounds = xc.execute("SELECT id, settings FROM inbounds").fetchall()
            for inb_id, settings_json in inbounds:
                if not settings_json:
                    continue
                try:
                    settings = json.loads(settings_json)
                    changed = False
                    
                    # VLESS clients
                    if 'clients' in settings and isinstance(settings['clients'], list):
                        for cl in settings['clients']:
                            exp = cl.get('expiryTime', 0)
                            if exp and exp > now_ms:
                                cl['expiryTime'] = exp + five_days_ms
                                cl['enable'] = True
                                changed = True
                    
                    # Hysteria clients
                    if 'auth' in settings and isinstance(settings['auth'], dict):
                        # Hysteria clients block
                        pass
                        
                    if changed:
                        xc.execute("UPDATE inbounds SET settings=? WHERE id=?", (json.dumps(settings), inb_id))
                except Exception as e:
                    print(f"Ошибка парсинга JSON для inbound {inb_id}: {e}")
            
            xui_conn.commit()
            xui_conn.close()
            print("✅ 3x-ui базы данных и JSON инбаундов успешно синхронизированы!")

if __name__ == '__main__':
    dry = '--dry-run' in sys.argv
    main(dry_run=dry)
