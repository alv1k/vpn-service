#!/usr/bin/env python3
"""
Скрипт автоматического напоминания пользователям, оплатившим подписку,
но ни разу не подключившимся к VPN (traffic = 0, last_online = 0).

Запуск:
    python3 scripts/remind_inactive_paid_users.py [--dry-run] [--send]
"""
import sys
import os
import argparse
import asyncio
import logging
import sqlite3
from datetime import datetime, timedelta

# Add project root to sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv("/home/alvik/vpn-service/.env")

from api.db import execute_query
from bot_xui.messaging import send_link_safely

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# Minimum and maximum time since payment to be eligible for reminder
MIN_HOURS_SINCE_PAYMENT = 2
MAX_HOURS_SINCE_PAYMENT = 48

XUI_DB_PATH = "/etc/x-ui/x-ui.db"


def get_xui_client_traffic_map():
    """Сбор статистики трафика по всем клиентам из x-ui.db."""
    if not os.path.exists(XUI_DB_PATH):
        logger.warning(f"⚠️ {XUI_DB_PATH} not found")
        return {}

    try:
        conn = sqlite3.connect(XUI_DB_PATH)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("SELECT email, up, down, total, last_online, last_sub_fetch FROM client_traffics")
        rows = cur.fetchall()
        conn.close()

        traffic_map = {}
        for r in rows:
            email = r["email"]
            if not email:
                continue
            if email not in traffic_map:
                traffic_map[email] = {
                    "up": 0,
                    "down": 0,
                    "total": 0,
                    "last_online": 0,
                    "last_sub_fetch": 0,
                }
            traffic_map[email]["up"] += r["up"] or 0
            traffic_map[email]["down"] += r["down"] or 0
            traffic_map[email]["total"] += r["total"] or 0
            traffic_map[email]["last_online"] = max(traffic_map[email]["last_online"], r["last_online"] or 0)
            traffic_map[email]["last_sub_fetch"] = max(traffic_map[email]["last_sub_fetch"], r["last_sub_fetch"] or 0)
        return traffic_map
    except Exception as e:
        logger.error(f"❌ Error reading x-ui.db: {e}")
        return {}


def get_candidate_paid_users():
    """Поиск пользователей с успешной оплатой за последние MAX_HOURS_SINCE_PAYMENT часов."""
    min_time = datetime.now() - timedelta(hours=MAX_HOURS_SINCE_PAYMENT)
    max_time = datetime.now() - timedelta(hours=MIN_HOURS_SINCE_PAYMENT)

    query = """
        SELECT u.id as user_id, u.tg_id, u.first_name, u.old_first_name, u.web_token, u.subscription_until,
               p.id as payment_id, p.tariff, p.amount, p.created_at as paid_at
        FROM users u
        JOIN payments p ON u.tg_id = p.tg_id AND p.status = 'paid'
        WHERE p.created_at >= %s
          AND p.created_at <= %s
          AND u.subscription_until > NOW()
          AND u.bot_blocked = 0
          AND u.web_token IS NOT NULL
        ORDER BY p.created_at DESC
    """
    return execute_query(query, (min_time, max_time), fetch="all") or []


def is_already_reminded(tg_id: int) -> bool:
    """Проверка, отправлялось ли уже напоминание по этому сценарию."""
    row = execute_query(
        """SELECT id FROM message_log 
           WHERE tg_id = %s 
             AND scenario IN ('paid_inactive_reminder', 'happ_first_launch_reminder')
             AND status = 'sent'
           LIMIT 1""",
        (tg_id,),
        fetch="one",
    )
    return row is not None


def get_user_app_info(tg_id: int):
    """Определение выбранного приложения и платформы пользователя."""
    row = execute_query(
        """SELECT client_app, platform FROM user_platforms 
           WHERE tg_id = %s 
           ORDER BY id DESC LIMIT 1""",
        (tg_id,),
        fetch="one",
    )
    if row and row.get("client_app") and row["client_app"] not in ("unknown", "None", ""):
        return row["client_app"], row.get("platform")
    return None, None


def build_message_and_buttons(name: str, app_name: str | None, web_token: str):
    """Формирование персонализированного текста сообщения и инлайн-кнопок."""
    name_display = f"{name}, з" if name else "З"

    if app_name:
        text = (
            f"{name_display}дравствуйте! 🐿\n\n"
            f"Спасибо за оплату подписки! Мы заметили, что вы уже добавили настройки в приложение <b>{app_name}</b>, "
            f"но само VPN-подключение ещё не запускали.\n\n"
            f"<b>Чтобы интернет заработал через VPN:</b>\n"
            f"1. Откройте приложение <b>{app_name}</b> на телефоне.\n"
            f"2. Нажмите кнопку подключения по центру экрана.\n"
            f"3. Если появится системный запрос <i>«Запрос на подключение / Доверие приложению»</i> — нажмите <b>ОК / Разрешить</b>.\n\n"
            f"Если что-то не открывается или нужна помощь — просто напишите нам, мы поможем настроить! ✨"
        )
    else:
        text = (
            f"{name_display}дравствуйте! 🐿\n\n"
            f"Спасибо за оплату подписки! Мы заметили, что вы ещё не настроили подключение к VPN.\n\n"
            f"Настройка занимает меньше 1 минуты:\n"
            f"1. Откройте инструкцию в личном кабинете по кнопке ниже.\n"
            f"2. Выберите ваше устройство и нажмите «Подключить VPN».\n\n"
            f"Если возникнут любые вопросы — наша поддержка всегда на связи! ✨"
        )

    buttons = [
        [
            {"text": "📖 Открыть инструкцию", "url": f"https://344988.snk.wtf/my/{web_token}"},
            {"text": "💬 Написать в поддержку", "url": "https://t.me/tiinsupport"},
        ]
    ]

    return text, buttons


async def process_reminders(send_messages: bool = False):
    traffic_map = get_xui_client_traffic_map()
    candidates = get_candidate_paid_users()

    logger.info(f"Найдено пользователей с недавней оплатой: {len(candidates)}")

    seen_tg = set()
    total_eligible = 0
    total_sent = 0

    for user in candidates:
        tg_id = user["tg_id"]
        if tg_id in seen_tg:
            continue
        seen_tg.add(tg_id)

        name = user.get("first_name") or user.get("old_first_name") or ""
        web_token = user.get("web_token")
        paid_at = user.get("paid_at")

        # Check traffic in x-ui
        email = f"tiin_{tg_id}"
        tr = traffic_map.get(email, {"up": 0, "down": 0, "total": 0, "last_online": 0, "last_sub_fetch": 0})
        total_traffic = tr["up"] + tr["down"]

        if total_traffic > 0 or tr["last_online"] > 0:
            logger.info(f"⏩ {name} ({tg_id}): есть трафик ({total_traffic} байт, online={tr['last_online']}) — пропускаем")
            continue

        if is_already_reminded(tg_id):
            logger.info(f"⏩ {name} ({tg_id}): напоминание уже отправлялось ранее — пропускаем")
            continue

        total_eligible += 1
        app_name, platform = get_user_app_info(tg_id)
        text, buttons = build_message_and_buttons(name, app_name, web_token)

        logger.info(
            f"🎯 [Кандидат] TG ID: {tg_id} | Имя: {name} | Оплата: {paid_at} | Приложение: {app_name or 'не выбрано'}"
        )

        if send_messages:
            ok = await send_link_safely(
                tg_id=tg_id,
                text=text,
                buttons=buttons,
                parse_mode="HTML",
                source="cron_autopay",
                scenario="paid_inactive_reminder",
            )
            if ok:
                total_sent += 1
                logger.info(f"✅ Напоминание успешно отправлено пользователю {tg_id}")
            else:
                logger.error(f"❌ Не удалось отправить напоминание пользователю {tg_id}")
        else:
            logger.info(f"🔎 [DRY-RUN] Сообщение для {tg_id} не отправлено (запустите с флагом --send)")

    logger.info("=" * 50)
    logger.info(
        f"Итог: Найдено неактивных оплативших пользователей: {total_eligible}, Отправлено: {total_sent}"
    )


def main():
    parser = argparse.ArgumentParser(description="Remind paid users who have not yet connected to VPN")
    parser.add_argument("--send", action="store_true", help="Send messages to eligible users")
    parser.add_argument("--dry-run", action="store_true", help="Dry run without sending messages (default)")
    args = parser.parse_args()

    send = args.send and not args.dry_run
    asyncio.run(process_reminders(send_messages=send))


if __name__ == "__main__":
    main()
