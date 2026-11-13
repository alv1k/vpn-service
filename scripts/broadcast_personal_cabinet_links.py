#!/usr/bin/env python3
"""
Скрипт рассылки ссылок на личный кабинет действующим клиентам TIIN VPN.
Использование:
    python3 broadcast_personal_cabinet_links.py --dry-run
    python3 broadcast_personal_cabinet_links.py --test-admin
    python3 broadcast_personal_cabinet_links.py --send
"""

import sys
import os
import asyncio
import logging
import argparse
from html import escape

# Добавляем корневую директорию проекта в sys.path
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from api.db import execute_query
from bot_xui.messaging import send_message_by_tg_id
from config import ADMIN_TG_ID

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("broadcast_lk")


def get_active_clients() -> list[dict]:
    """Возвращает всех действующих пользователей с активной подпиской или ключами."""
    rows = execute_query("""
        SELECT DISTINCT u.id, u.tg_id, u.first_name, u.web_token, u.subscription_until
        FROM users u
        LEFT JOIN vpn_keys k ON u.tg_id = k.tg_id
        WHERE (u.subscription_until > NOW() OR k.expires_at > NOW())
          AND u.tg_id IS NOT NULL AND u.tg_id > 0
          AND (u.bot_blocked IS NULL OR u.bot_blocked = 0)
        ORDER BY u.subscription_until DESC
    """, fetch="all")
    return rows or []


def build_message(first_name: str | None, web_token: str) -> tuple[str, InlineKeyboardMarkup]:
    """Формирует HTML-текст сообщения и инлайн-клавиатуру."""
    greeting = f"Здравствуйте, <b>{escape(first_name)}</b>! 👋" if first_name else "Здравствуйте! 👋"
    
    de_url = f"https://344988.snk.wtf/my/{web_token}"
    ru_url = f"https://ru-server.tiinservice.online/my/{web_token}"

    text = (
        f"{greeting}\n\n"
        "В нашем официальном канале опубликован пост об утренней ситуации с обновлением подписок и её решении.\n\n"
        "Как и обещали, отправляем прямые ссылки на ваш <b>Личный кабинет</b>, чтобы они всегда оставались у вас под рукой "
        "в переписке с ботом (даже если временно пропадёт доступ к зарубежным серверам):\n\n"
        f"🔹 <b>Способ 1 (Основной, Германия):</b>\n"
        f"{de_url}\n\n"
        f"🔹 <b>Способ 2 (Прямой шлюз, Selectel СПб):</b>\n"
        f"{ru_url}\n\n"
        "💡 <i>В личном кабинете можно в 1 клик скопировать обновлённую подписку для любого приложения, "
        "а также обратиться в поддержку, даже если у вас не работает Телеграм.</i>\n\n"
        "По любым вопросам пишите: @tiin_service_bot 🤝\n\n"
        "#личныйкабинет #профиль #помощь #лк #настройки #инструкция"
    )

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🌐 Открыть Личный кабинет", url=de_url)],
        [InlineKeyboardButton("📢 Наш Telegram-канал", url="https://t.me/tiin_service")]
    ])

    return text, keyboard


async def main():
    parser = argparse.ArgumentParser(description="Рассылка ссылок на ЛК активным клиентам")
    parser.add_argument("--dry-run", action="store_true", help="Показать список клиентов без отправки")
    parser.add_argument("--test-admin", action="store_true", help="Отправить тестовое сообщение только администратору")
    parser.add_argument("--send", action="store_true", help="Выполнить реальную отправку всем действующим клиентам")
    args = parser.parse_args()

    if not (args.dry_run or args.test_admin or args.send):
        parser.print_help()
        return

    clients = get_active_clients()
    logger.info(f"Найдено действующих клиентов: {len(clients)}")

    if args.dry_run:
        print("\n=== DRY RUN: СПИСОК КЛИЕНТОВ ===")
        for i, c in enumerate(clients, 1):
            name = c.get("first_name") or "—"
            token = c.get("web_token")
            print(f"{i:2d}. TG ID: {c['tg_id']} | Имя: {name:<15} | Токен: {token}")
        
        sample_text, _ = build_message(clients[0].get("first_name"), clients[0].get("web_token"))
        print("\n=== ПРИМЕР СООБЩЕНИЯ ===")
        print(sample_text)
        return

    if args.test_admin:
        logger.info(f"Отправка тестового сообщения администратору (TG ID: {ADMIN_TG_ID})...")
        admin_client = next((c for c in clients if c["tg_id"] == ADMIN_TG_ID), None)
        token = admin_client["web_token"] if admin_client else "NgveVGMzbl8nu-trBlTCcw"
        name = admin_client.get("first_name") if admin_client else "Admin"
        
        text, reply_markup = build_message(name, token)
        success = await send_message_by_tg_id(
            tg_id=ADMIN_TG_ID,
            text=text,
            parse_mode="HTML",
            reply_markup=reply_markup,
            source="admin_notify",
            scenario="lk_links_broadcast_test"
        )
        if success:
            logger.info("✅ Тестовое сообщение администратору успешно доставлено!")
        else:
            logger.error("❌ Не удалось доставить тестовое сообщение администратору.")
        return

    if args.send:
        logger.info(f"🚀 Запуск рассылки для {len(clients)} действующих клиентов...")
        sent_count = 0
        failed_count = 0

        for i, c in enumerate(clients, 1):
            tg_id = c["tg_id"]
            name = c.get("first_name")
            token = c.get("web_token")

            text, reply_markup = build_message(name, token)
            success = await send_message_by_tg_id(
                tg_id=tg_id,
                text=text,
                parse_mode="HTML",
                reply_markup=reply_markup,
                source="broadcast_script",
                scenario="lk_links_broadcast"
            )

            if success:
                sent_count += 1
                logger.info(f"[{i}/{len(clients)}] ✅ Отправлено пользователю {tg_id} ({name or '—'})")
            else:
                failed_count += 1
                logger.warning(f"[{i}/{len(clients)}] ❌ Ошибка отправки пользователю {tg_id} ({name or '—'})")

            # Антифлуд-задержка
            await asyncio.sleep(0.08)

        logger.info(f"\nРассылка завершена!\nВсего: {len(clients)} | Успешно: {sent_count} | Ошибок: {failed_count}")


if __name__ == "__main__":
    asyncio.run(main())
