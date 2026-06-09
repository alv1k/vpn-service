#!/usr/bin/env python3
"""Массовая рассылка сообщения всем пользователям с tg_id."""

import sys
import time
import logging
from pathlib import Path

import mysql.connector
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from config import (
    TELEGRAM_BOT_TOKEN,
    MYSQL_HOST,
    MYSQL_USER,
    MYSQL_PASSWORD,
    MYSQL_DATABASE,
)

# Файл для логирования отправленных сообщений (чтобы не дублировать)
SENT_LOG = Path(__file__).resolve().parent / "broadcast_sent.log"

# Файл для редактирования сообщения (можно создать broadcast_message.txt)
MESSAGE_FILE = Path(__file__).resolve().parent / "broadcast_message.txt"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Сообщение по умолчанию (если нет файла)
DEFAULT_MESSAGE = """
🎁 <b>Подарок от TIIN VPN!</b>

Мы благодарны вам за лояльность нашему сервису и <b>дарим неделю</b> дополнительно к вашей подписке!

📱 Подробнее можете посмотреть в боте, раздел <b>"Мои конфиги"</b>

⚠️ На данный момент ссылки подписки пока не доступны, мы работаем над проблемой. 
А пока можете воспользоваться обновленной vless-ссылкой, её можно найти так же в разделе <b>"Мои конфиги"</b>

🎉 <b>Пользователям, у которых закончилась тестовая подписка, спешим обрадовать — она продлена соответственно на неделю начиная с сегодняшнего дня!</b>

🚀 Приятного серфинга!
""".strip()


def load_message():
    """Загружает сообщение из файла или возвращает стандартное."""
    if MESSAGE_FILE.exists():
        with open(MESSAGE_FILE, "r", encoding="utf-8") as f:
            content = f.read().strip()
            # Проверяем, что файл не пустой и содержит хотя бы один символ
            if content and len(content) > 0:
                logger.info(f"📝 Сообщение загружено из {MESSAGE_FILE} ({len(content)} симв.)")
                return content
            else:
                logger.warning(f"⚠️ Файл {MESSAGE_FILE} пуст, используется стандартное сообщение")
    logger.info("📝 Используется стандартное сообщение")
    return DEFAULT_MESSAGE


MESSAGE = load_message()


def get_db_connection():
    return mysql.connector.connect(
        host=MYSQL_HOST,
        user=MYSQL_USER,
        password=MYSQL_PASSWORD,
        database=MYSQL_DATABASE,
        charset='utf8mb4'
    )

def get_all_users():
    """Получает всех пользователей с tg_id (не бот-заблокированных)."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        cursor.execute("SHOW TABLES LIKE 'users'")
        users_exists = cursor.fetchone() is not None

        if users_exists:
            cursor.execute("""
                SELECT DISTINCT u.tg_id, u.id, u.first_name, u.last_name
                FROM users u
                WHERE u.tg_id IS NOT NULL
                  AND u.tg_id != 0
                  AND u.bot_blocked = 0
            """)
        else:
            logger.error("❌ Таблица 'users' не найдена в БД")
            return []

        rows = cursor.fetchall()
        cursor.close()
        conn.close()

        logger.info(f"📊 Найдено пользователей для рассылки: {len(rows)}")
        return rows
        
    except mysql.connector.Error as e:
        logger.error(f"❌ Ошибка подключения к БД: {e}")
        return []

def already_sent(tg_id: int) -> bool:
    """Проверяет, отправляли ли уже сообщение этому пользователю."""
    if not SENT_LOG.exists():
        return False
    with open(SENT_LOG, "r") as f:
        return str(tg_id) in f.read()


def mark_sent(tg_id: int):
    """Отмечает в логе, что сообщение отправлено."""
    with open(SENT_LOG, "a") as f:
        f.write(f"{tg_id}\n")


def get_proxy_link() -> str:
    """Формирует tg://proxy ссылку с текущим адресером из конфига."""
    try:
        from config import MTPROTO_SERVER, MTPROTO_PORT, MTPROTO_SECRET
        return f"tg://proxy?server={MTPROTO_SERVER}&port={MTPROTO_PORT}&secret={MTPROTO_SECRET}"
    except Exception:
        return "tg://proxy?server=tiinservice.online&port=8443"


def send_message_to_user(tg_id: int) -> bool:
    """Отправляет сообщение пользователю через Telegram с кнопкой подключения прокси."""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    proxy_link = get_proxy_link()
    payload = {
        "chat_id": tg_id,
        "text": MESSAGE,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
        "reply_markup": {
            "inline_keyboard": [
                [{"text": "🔗 Подключить прокси", "url": proxy_link}]
            ]
        },
    }
    try:
        resp = requests.post(url, json=payload, timeout=10)
        if resp.status_code == 200:
            result = resp.json()
            if result.get("ok"):
                logger.info(f"✅ Отправлено tg_id={tg_id}")
                try:
                    sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
                    from api.db import log_message_sent
                    log_message_sent(tg_id=tg_id, source="broadcast_script", status='sent',
                                     message_text=MESSAGE[:500] if MESSAGE else None)
                except Exception:
                    pass
                return True
            error_desc = result.get("description", "")
            is_block = "blocked" in error_desc.lower() or "deactivated" in error_desc.lower()
            logger.warning(f"{'🚫' if is_block else '❌'} Ошибка для tg_id={tg_id}: {error_desc}")
            if is_block:
                try:
                    conn = get_db_connection()
                    cur = conn.cursor()
                    cur.execute("UPDATE users SET bot_blocked = 1 WHERE tg_id = %s", (tg_id,))
                    conn.commit()
                    cur.close()
                    conn.close()
                except Exception:
                    pass
            try:
                sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
                from api.db import log_message_sent
                log_message_sent(tg_id=tg_id, source="broadcast_script",
                                 status='blocked' if is_block else 'failed',
                                 error_text=error_desc[:255])
            except Exception:
                pass
            return False
        else:
            logger.warning(f"❌ HTTP {resp.status_code} для tg_id={tg_id}: {resp.text}")
            try:
                sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
                from api.db import log_message_sent
                log_message_sent(tg_id=tg_id, source="broadcast_script", status='failed',
                                 error_text=resp.text[:255])
            except Exception:
                pass
            return False
    except Exception as e:
        logger.error(f"❌ Исключение для tg_id={tg_id}: {e}")
        try:
            sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
            from api.db import log_message_sent
            log_message_sent(tg_id=tg_id, source="broadcast_script", status='failed',
                             error_text=str(e)[:255])
        except Exception:
            pass
        return False


def main():
    import sys as sys_module
    
    total = 0
    sent_count = 0
    skipped_count = 0
    
    # Проверяем, указан ли конкретный tg_id для теста
    test_tg_id = None
    for arg in sys_module.argv:
        if arg.startswith("--test="):
            test_tg_id = int(arg.split("=")[1])
            break
    
    # Тестовый режим: отправка только одному пользователю
    if test_tg_id:
        logger.info(f"🧪 Тестовый режим: отправка только tg_id={test_tg_id}")
        if send_message_to_user(test_tg_id):
            logger.info("✅ Тестовое сообщение отправлено")
        else:
            logger.error("❌ Не удалось отправить тестовое сообщение")
        return
    
    # Сухой запуск (показываем, кому бы отправили)
    dry_run = "--dry-run" in sys_module.argv
    if dry_run:
        logger.info("🔍 СУХОЙ ЗАПУСК: сообщения НЕ будут отправлены")
        users = get_all_users()
        if not users:
            logger.info("Нет пользователей с tg_id для рассылки")
            return
        logger.info(f"📋 Список пользователей для рассылки ({len(users)} чел.):")
        for user in users:
            username = user.get('first_name', '') or user.get('last_name', '') or '-'
            logger.info(f"  - tg_id={user['tg_id']} ({username})")
        return
    
    # Получаем всех пользователей
    users = get_all_users()
    if not users:
        logger.info("❌ Нет пользователей с tg_id для рассылки")
        return

    total = len(users)
    logger.info(f"🚀 Начинаем рассылку {total} пользователям...")

    for i, user in enumerate(users):
        tg_id = user["tg_id"]
        username = user.get('first_name', '') or user.get('last_name', '') or '-'
        
        # Пропускаем уже отправленных
        if already_sent(tg_id):
            logger.debug(f"⏭️ Пропускаем (уже отправлено): tg_id={tg_id} (@{username})")
            skipped_count += 1
            continue

        logger.info(f"📨 [{i+1}/{total}] Отправка tg_id={tg_id} (@{username})...")
        
        if send_message_to_user(tg_id):
            mark_sent(tg_id)
            sent_count += 1
        else:
            logger.error(f"❌ Не удалось отправить tg_id={tg_id}")

        time.sleep(0.1)  # задержка 100 мс между сообщениями

    # Итоговая статистика
    logger.info(f"📊 РАССЫЛКА ЗАВЕРШЕНА:")
    logger.info(f"   ✅ Отправлено: {sent_count}")
    logger.info(f"   ⏭️ Пропущено (уже были): {skipped_count}")
    logger.info(f"   📋 Всего в списке: {total}")
    logger.info(f"   ⏱️ Время выполнения: {time.time() - start_time:.1f} сек.")


if __name__ == "__main__":
    start_time = time.time()
    main()