#!/usr/bin/env python3
"""
Скрипт миграции 3x-UI: копирует клиентов из Inbound 1 (VLESS Reality) в Inbound 9 (VLESS WS MUX).
Сохраняет их оригинальные UUID, subId, expiryTime, tgId, enable status.
Также создает соответствующие записи в таблице client_traffics для inbound_id = 9.
"""
import sqlite3
import json
import logging

XUI_DB_PATH = "/etc/x-ui/x-ui.db"
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

def migrate():
    conn = sqlite3.connect(XUI_DB_PATH)
    cur = conn.cursor()

    # 1. Получаем настройки Inbound 1 и Inbound 9
    cur.execute("SELECT settings FROM inbounds WHERE id = 1;")
    ib1_row = cur.fetchone()
    if not ib1_row:
        logging.error("Inbound 1 not found!")
        return

    cur.execute("SELECT settings FROM inbounds WHERE id = 9;")
    ib9_row = cur.fetchone()
    if not ib9_row:
        logging.error("Inbound 9 not found!")
        return

    ib1_settings = json.loads(ib1_row[0])
    ib9_settings = json.loads(ib9_row[0])

    ib1_clients = ib1_settings.get("clients", [])
    ib9_clients = ib9_settings.get("clients", [])

    # Карты существующих e-mail в Inbound 9
    ib9_email_map = {c.get("email"): c for c in ib9_clients if c.get("email")}

    added_count = 0
    updated_count = 0

    for c1 in ib1_clients:
        email = c1.get("email")
        if not email:
            continue

        # Формируем объект клиента для Inbound 9
        c9_obj = {
            "comment": c1.get("comment", ""),
            "created_at": c1.get("created_at", 0),
            "email": email,
            "enable": c1.get("enable", True),
            "expiryTime": c1.get("expiryTime", 0),
            "flow": "",  # Для WS flow строго пустой!
            "id": c1.get("id"),
            "limitIp": c1.get("limitIp", 10),
            "reset": c1.get("reset", 0),
            "subId": c1.get("subId", ""),
            "tgId": c1.get("tgId", 0),
            "totalGB": c1.get("totalGB", 0),
            "updated_at": c1.get("updated_at", 0)
        }

        if email in ib9_email_map:
            # Обновляем существующего
            idx = ib9_clients.index(ib9_email_map[email])
            ib9_clients[idx] = c9_obj
            updated_count += 1
        else:
            # Добавляем нового
            ib9_clients.append(c9_obj)
            added_count += 1

        # Создаем/обновляем запись в client_traffics (unique on email)
        cur.execute("SELECT id FROM client_traffics WHERE email = ?", (email,))
        ct_row = cur.fetchone()
        if ct_row:
            cur.execute(
                "UPDATE client_traffics SET enable = ?, expiry_time = ? WHERE email = ?",
                (1 if c1.get("enable", True) else 0, c1.get("expiryTime", 0), email)
            )
        else:
            cur.execute(
                "INSERT INTO client_traffics (inbound_id, enable, email, up, down, expiry_time, total, reset) "
                "VALUES (9, ?, ?, 0, 0, ?, 0, 0)",
                (1 if c1.get("enable", True) else 0, email, c1.get("expiryTime", 0))
            )

    ib9_settings["clients"] = ib9_clients
    new_ib9_json = json.dumps(ib9_settings, indent=2)

    cur.execute("UPDATE inbounds SET settings = ? WHERE id = 9;", (new_ib9_json,))
    conn.commit()
    conn.close()

    logging.info(f"Migration completed! Added: {added_count}, Updated: {updated_count}. Total in Inbound 9: {len(ib9_clients)}")

if __name__ == "__main__":
    migrate()
