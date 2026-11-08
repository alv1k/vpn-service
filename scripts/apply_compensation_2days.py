#!/usr/bin/env python3
"""
Скрипт начисления компенсации +2 дня и рассылки уведомления:
1. MySQL: users (subscription_until = subscription_until + 2 days)
2. MySQL: vpn_keys (expires_at = expires_at + 2 days)
3. 3x-ui: SQLite /etc/x-ui/x-ui.db (clients, inbounds JSON settings)
4. Синхронизация с RU-нодой (Selectel)
5. Персонализированная рассылка в Telegram через send_link_safely
"""
import os
import sys
import html
import json
import sqlite3
import shutil
import asyncio
import logging
from datetime import datetime

sys.path.insert(0, "/home/alvik/vpn-service")

from dotenv import load_dotenv
load_dotenv("/home/alvik/vpn-service/.env")

from bot_xui.messaging import send_link_safely
from api.db import get_db, get_web_token
from config import PORTAL_BASE_URL

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("compensation_2days")

def build_message(name: str | None) -> str:
    safe_name = html.escape(name.strip()) if name and name.strip() and name.strip() != "Пользователь" else None
    greeting = f"{safe_name}, привет!" if safe_name else "Друзья, привет!"

    return (
        f"🛠 <b>Важное обновление серверов и +2 дня к подписке!</b>\n\n"
        f"{greeting}\n\n"
        f"Во время недавнего обновления конфигурации серверов на некоторых устройствах и операторах связи "
        f"подписка могла временно работать с перебоями или не подключаться.\n\n"
        f"⚙️ <b>Мы всё исправили и полностью восстановили привычную скоростную маршрутизацию</b> через наши защищённые российские шлюзы.\n\n"
        f"🔄 <b>Что нужно сделать прямо сейчас:</b>\n"
        f"Пожалуйста, <b>обновите подписку</b> в вашем приложении:\n"
        f"• <b>v2rayNG / Streisand / FoXray</b>: меню (три точки) ➔ <i>«Обновить подписку»</i> (или свайп списка серверов вниз).\n"
        f"• <b>Happ / Hiddify / Karing</b>: значок 🔄 рядом с профилем подписки.\n\n"
        f"🎁 В знак благодарности за ваше понимание, терпение и лояльность мы автоматически добавили <b>+2 дня</b> к вашей действующей подписке!\n\n"
        f"Спасибо, что остаётесь с нами! ❤️\n"
        f"— <i>Команда TIIN VPN</i>"
    )

def extend_subscriptions(dry_run=True):
    now = datetime.utcnow()
    two_days_ms = 2 * 24 * 60 * 60 * 1000
    now_ms = int(now.timestamp() * 1000)

    logger.info(f"=== Шаг 1: Продление подписок на +2 дня (dry_run={dry_run}) ===")

    if not dry_run:
        bak_name = f"/etc/x-ui/x-ui.db.bak.compensation_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        shutil.copy2("/etc/x-ui/x-ui.db", bak_name)
        logger.info(f"✅ Создан бэкап 3x-ui: {bak_name}")

    with get_db() as db:
        c = db.cursor(dictionary=True)
        c.execute("SELECT id, tg_id, first_name, email, subscription_until FROM users WHERE subscription_until > %s", (now,))
        active_users = c.fetchall()
        logger.info(f"Найдено активных пользователей в MySQL `users`: {len(active_users)}")

        c.execute("SELECT id, tg_id, client_id, client_name, expires_at FROM vpn_keys WHERE expires_at > %s", (now,))
        active_keys = c.fetchall()
        logger.info(f"Найдено активных ключей в MySQL `vpn_keys`: {len(active_keys)}")

        if not dry_run:
            c.execute("""
                UPDATE users 
                SET subscription_until = DATE_ADD(subscription_until, INTERVAL 2 DAY)
                WHERE subscription_until > %s
            """, (now,))
            users_updated = c.rowcount
            logger.info(f"✅ Обновлено записей в MySQL `users`: {users_updated}")

            c.execute("""
                UPDATE vpn_keys 
                SET expires_at = DATE_ADD(expires_at, INTERVAL 2 DAY)
                WHERE expires_at > %s
            """, (now,))
            keys_updated = c.rowcount
            logger.info(f"✅ Обновлено записей в MySQL `vpn_keys`: {keys_updated}")
            db.commit()

            # 3x-ui SQLite
            xui_conn = sqlite3.connect('/etc/x-ui/x-ui.db')
            xc = xui_conn.cursor()

            xc.execute("""
                UPDATE clients 
                SET expiry_time = expiry_time + ?,
                    enable = 1,
                    updated_at = ?
                WHERE expiry_time > ?
            """, (two_days_ms, now_ms, now_ms))
            xui_clients_updated = xc.rowcount
            logger.info(f"✅ Обновлено клиентов в 3x-ui `clients`: {xui_clients_updated}")

            xc.execute("""
                UPDATE client_traffics 
                SET expiry_time = expiry_time + ?,
                    enable = 1
                WHERE expiry_time > ?
            """, (two_days_ms, now_ms))
            xui_traffics_updated = xc.rowcount
            logger.info(f"✅ Обновлено записей в 3x-ui `client_traffics`: {xui_traffics_updated}")

            inbounds = xc.execute("SELECT id, settings FROM inbounds").fetchall()
            for inb_id, settings_json in inbounds:
                if not settings_json:
                    continue
                try:
                    settings = json.loads(settings_json)
                    changed = False
                    if 'clients' in settings and isinstance(settings['clients'], list):
                        for cl in settings['clients']:
                            exp = cl.get('expiryTime', 0)
                            if exp and exp > now_ms:
                                cl['expiryTime'] = exp + two_days_ms
                                cl['enable'] = True
                                changed = True
                    if changed:
                        xc.execute("UPDATE inbounds SET settings=? WHERE id=?", (json.dumps(settings), inb_id))
                except Exception as e:
                    logger.error(f"Ошибка обновления инбаунда {inb_id}: {e}")

            xui_conn.commit()
            xui_conn.close()
            logger.info("✅ 3x-ui база и JSON инбаундов успешно обновлены!")

async def run_broadcast(dry_run=True):
    logger.info(f"=== Шаг 2: Рассылка сообщений пользователям (dry_run={dry_run}) ===")
    with get_db() as db:
        c = db.cursor(dictionary=True)
        c.execute("""
            SELECT DISTINCT tg_id, first_name 
            FROM users 
            WHERE subscription_until > NOW() 
              AND tg_id IS NOT NULL 
              AND tg_id > 0
              AND bot_blocked = 0
        """)
        recipients = c.fetchall()

    logger.info(f"Получателей для рассылки: {len(recipients)}")

    success_count = 0
    fail_count = 0

    for u in recipients:
        tg_id = u["tg_id"]
        name = u.get("first_name")

        web_token = get_web_token(tg_id)
        buttons = []
        if web_token:
            buttons.append([{"text": "🔑 Моя подписка и настройки", "url": f"{PORTAL_BASE_URL}/my/{web_token}"}])
        buttons.append([{"text": "💬 Поддержка", "callback_data": "feedback"}])

        message_text = build_message(name)

        if dry_run:
            first_line = message_text.split('\n')[2]
            logger.info(f"[DRY-RUN] tg_id={tg_id} name={name} -> '{first_line}'")
            success_count += 1
        else:
            try:
                ok = await send_link_safely(
                    tg_id=tg_id,
                    text=message_text,
                    buttons=buttons,
                    parse_mode="HTML",
                    source="admin_send",
                    scenario="compensation_2days_oct2026",
                )
                if ok:
                    logger.info(f"✅ Отправлено {name or 'User'} (tg_id: {tg_id})")
                    success_count += 1
                else:
                    logger.warning(f"⚠️ Не удалось отправить {name or 'User'} (tg_id: {tg_id})")
                    fail_count += 1
            except Exception as e:
                logger.error(f"❌ Ошибка отправки {tg_id}: {e}")
                fail_count += 1

            await asyncio.sleep(0.2)

    logger.info(f"=== Итог рассылки: успешно {success_count}, ошибок {fail_count} ===")

async def main():
    dry = "--send" not in sys.argv
    extend_subscriptions(dry_run=dry)
    await run_broadcast(dry_run=dry)

if __name__ == "__main__":
    asyncio.run(main())
