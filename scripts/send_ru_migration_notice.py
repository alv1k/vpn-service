#!/usr/bin/env python3
"""Скрипт уведомления активных пользователей RU-ноды о переходе на DE Reality Vision."""
import asyncio
import logging
import sys

sys.path.insert(0, "/home/alvik/vpn-service")

from dotenv import load_dotenv
load_dotenv("/home/alvik/vpn-service/.env")

from bot_xui.messaging import send_link_safely
from api.db import get_db

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

TARGET_TG_IDS = [
    364224373,
    667624374,
    261343964,
    7891650475,
    284509567,
    911120750,
    50613379,
    813312646,
    138315162,
    619358991,
    393060304,
    6593467197,
]

async def main():
    conn = get_db()
    cur = conn.cursor(dictionary=True)
    users_data = {}
    for tid in TARGET_TG_IDS:
        cur.execute("SELECT tg_id, first_name, web_token FROM users WHERE tg_id = %s", (tid,))
        row = cur.fetchone()
        if row:
            users_data[tid] = row
    conn.close()

    success_count = 0
    fail_count = 0

    for tid in TARGET_TG_IDS:
        user = users_data.get(tid, {})
        first_name = user.get("first_name") or "друг"
        web_token = user.get("web_token") or ""

        portal_url = f"https://344988.snk.wtf/my/{web_token}" if web_token else "https://344988.snk.wtf"

        text = (
            f"⚡️ <b>Важное обновление узлов подключения</b>\n\n"
            f"Здравствуйте, {first_name}! Мы оптимизировали сетевую инфраструктуру и перевели "
            f"основные подключения на высокоскоростной европейский узел <b>Germany Reality Vision</b> "
            f"для максимальной скорости и стабильности.\n\n"
            f"🔄 <b>Что нужно сделать прямо сейчас:</b>\n"
            f"1. Откройте ваше приложение для VPN (v2rayNG / Hiddify / Happ / Streisand / Karing).\n"
            f"2. Нажмите кнопку <b>«Обновить подписку»</b> (значок круговых стрелочек 🔄).\n"
            f"3. Выберите сервер <b>🇩🇪 Germany Reality Vision</b> и подключитесь.\n\n"
            f"<i>Если подключение не обновляется автоматически — перейдите в Личный кабинет по кнопке ниже и скопируйте свежую ссылку.</i>"
        )

        buttons = [
            [{"text": "🪄 Личный кабинет", "url": portal_url}],
            [{"text": "💬 Поддержка", "callback_data": "feedback"}],
        ]

        logger.info(f"Sending notice to tg_id={tid} ({first_name})...")
        try:
            ok = await send_link_safely(
                tg_id=tid,
                text=text,
                buttons=buttons,
                parse_mode="HTML",
                source="admin_send",
                scenario="ru_migration_notice",
            )
            if ok:
                success_count += 1
                logger.info(f"✅ Successfully sent to tg_id={tid}")
            else:
                fail_count += 1
                logger.warning(f"❌ Failed to send to tg_id={tid}")
        except Exception as e:
            fail_count += 1
            logger.error(f"Error sending to tg_id={tid}: {e}")

        await asyncio.sleep(0.3)

    logger.info(f"Done. Sent: {success_count}, Failed: {fail_count}")

if __name__ == "__main__":
    asyncio.run(main())
