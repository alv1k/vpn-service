#!/usr/bin/env python3
"""
Рассылка loyalty-сообщения активным пользователям (subscription_until > NOW()).
Отправляет через Telegram Bot API с inline-кнопкой «Поддержка».

Запуск:
  python3 scripts/send_loyalty_broadcast.py --dry-run   # предпросмотр
  python3 scripts/send_loyalty_broadcast.py              # отправка
  python3 scripts/send_loyalty_broadcast.py --test=123   # тест на конкретном tg_id
"""
import sys
import os
import time
import logging
import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import TELEGRAM_BOT_TOKEN, MYSQL_HOST, MYSQL_USER, MYSQL_PASSWORD, MYSQL_DATABASE

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

MESSAGE = (
    "Привет! 👋\n\n"
    "Мы обновили конфигурацию инбаундов — теперь используем <b>ML-DSA-65 (Dilithium3)</b> 🔐 "
    "постквантовую криптографию, стандартизированную NIST 🛡️⚡\n\n"
    "Раньше бывали подтормаживания и сбои — теперь всё стабильно и летает 🚀💨\n\n"
    "И приятный бонус — <b>+7 дней к твоей подписке</b> 🎁🥳 "
    "Просто так, за то что ты с нами! 💛\n\n"
    "Если есть вопросы — пиши, всегда на связи 😊"
)

INLINE_KEYBOARD = {
    "inline_keyboard": [
        [{"text": "✉️ Поддержка", "callback_data": "feedback"}]
    ]
}


def get_active_users():
    """Получить активных пользователей с tg_id (subscription_until > NOW(), не заблокировали бота)."""
    import mysql.connector
    conn = mysql.connector.connect(
        host="127.0.0.1", port=3306,
        user=MYSQL_USER, password=MYSQL_PASSWORD,
        database=MYSQL_DATABASE, charset="utf8mb4",
    )
    cursor = conn.cursor(dictionary=True)
    cursor.execute("""
        SELECT DISTINCT u.tg_id, u.first_name, u.subscription_until
        FROM users u
        WHERE u.tg_id IS NOT NULL
          AND u.tg_id != 0
          AND u.bot_blocked = 0
          AND u.subscription_until > NOW()
        ORDER BY u.subscription_until
    """)
    rows = cursor.fetchall()
    cursor.close()
    conn.close()
    return rows


def send_message(tg_id):
    """Отправить сообщение пользователю через Telegram Bot API."""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": tg_id,
        "text": MESSAGE,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
        "reply_markup": INLINE_KEYBOARD,
    }
    try:
        resp = requests.post(url, json=payload, timeout=10)
        if resp.status_code == 200 and resp.json().get("ok"):
            log.info("✅ Отправлено tg_id=%s", tg_id)
            return True
        error = resp.json().get("description", resp.text)
        log.warning("❌ Ошибка tg_id=%s: %s", tg_id, error)
        return False
    except Exception as e:
        log.error("❌ Исключение tg_id=%s: %s", tg_id, e)
        return False


def main():
    args = sys.argv[1:]
    dry_run = "--dry-run" in args
    test_tg_id = None
    for arg in args:
        if arg.startswith("--test="):
            test_tg_id = int(arg.split("=")[1])

    if test_tg_id:
        log.info("🧪 Тест: отправка tg_id=%s", test_tg_id)
        send_message(test_tg_id)
        return

    users = get_active_users()
    log.info("Активных пользователей: %d", len(users))

    if dry_run:
        log.info("🔍 DRY RUN — сообщения НЕ отправляются")
        for u in users:
            log.info("  tg_id=%s | %s | до %s", u['tg_id'], u['first_name'], u['subscription_until'])
        return

    sent = 0
    failed = 0
    for i, u in enumerate(users):
        tg_id = u['tg_id']
        name = u['first_name'] or '-'
        log.info("[%d/%d] tg_id=%s (%s) ...", i + 1, len(users), tg_id, name)
        if send_message(tg_id):
            sent += 1
        else:
            failed += 1
        time.sleep(0.1)

    log.info("=" * 40)
    log.info("Готово: отправлено=%d, ошибок=%d", sent, failed)


if __name__ == "__main__":
    main()
