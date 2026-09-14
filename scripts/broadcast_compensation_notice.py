#!/usr/bin/env python3
"""
Скрипт персонализированной рассылки уведомления о компенсации +5 дней и обновлении узлов.
Отправляет персонализированное сообщение с указанием имени пользователя
и логирует каждое сообщение в таблицу `message_log`.
"""
import os
import sys
import html
import asyncio
import logging

sys.path.insert(0, "/home/alvik/vpn-service")

from dotenv import load_dotenv
load_dotenv("/home/alvik/vpn-service/.env")

from bot_xui.messaging import send_link_safely
from api.db import get_db, get_web_token

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("broadcast_compensation")

def build_message(name: str | None) -> str:
    safe_name = html.escape(name.strip()) if name and name.strip() and name.strip() != "Пользователь" else None
    greeting = f"{safe_name}, привет!" if safe_name else "Друзья, привет!"
    
    return (
        f"🛠 <b>Важное обновление и компенсация подписки</b>\n\n"
        f"{greeting}\n\n"
        f"Мы провели масштабное тестирование всех наших узлов и <b>полностью убрали нерабочие и нестабильные локации</b>. "
        f"В подписке остались только проверенные, скоростные и защищенные соединения.\n\n"
        f"🔄 <b>Что нужно сделать прямо сейчас:</b>\n"
        f"Пожалуйста, <b>обновите подписку</b> в вашем приложении (кнопка 🔄 <i>Обновить</i> / потянуть список серверов вниз).\n\n"
        f"🎁 <b>Компенсация +5 дней:</b>\n"
        f"Мы знаем, что последние 4 дня сервис работал с перебоями из-за волны масштабных блокировок. "
        f"В знак благодарности за ваше терпение и доверие мы <b>автоматически продлили вашу подписку на 5 дней</b>!\n\n"
        f"🚀 <b>Что дальше:</b>\n"
        f"Совсем скоро мы подключим дополнительный российский шлюз на базе новейших передовых решений технического сообщества, "
        f"благодаря чему соединение станет еще быстрее, устойчивее и стабильнее в любых мобильных сетях.\n\n"
        f"Спасибо, что верите в нас и остаетесь с нами! ❤️\n\n"
        f"— <i>Команда Tiin VPN</i>"
    )

async def run_broadcast(dry_run=True):
    with get_db() as db:
        c = db.cursor(dictionary=True)
        c.execute("SELECT DISTINCT tg_id, first_name FROM users WHERE subscription_until > NOW() AND tg_id IS NOT NULL AND tg_id > 0")
        recipients = c.fetchall()

    logger.info(f"Получателей для рассылки: {len(recipients)} (dry_run={dry_run})")

    success_count = 0
    fail_count = 0

    for u in recipients:
        tg_id = u["tg_id"]
        name = u.get("first_name")
        
        web_token = get_web_token(tg_id)
        buttons = []
        if web_token:
            buttons.append([{"text": "🔑 Моя подписка и настройки", "url": f"https://344988.snk.wtf/my/{web_token}"}])
        buttons.append([{"text": "💬 Поддержка", "callback_data": "feedback"}])

        message_text = build_message(name)

        if dry_run:
            first_line = message_text.split('\n')[2]
            logger.info(f"[DRY-RUN] {tg_id} -> '{first_line}'")
            success_count += 1
        else:
            try:
                ok = await send_link_safely(
                    tg_id=tg_id,
                    text=message_text,
                    buttons=buttons,
                    parse_mode="HTML",
                    source="admin_send",
                    scenario="compensation_and_nodes_update",
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
            
            await asyncio.sleep(0.15)

    logger.info(f"=== Итог рассылки: успешно {success_count}, ошибок {fail_count} ===")

if __name__ == "__main__":
    dry = "--send" not in sys.argv
    asyncio.run(run_broadcast(dry_run=dry))
