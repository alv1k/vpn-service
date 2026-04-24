#!/usr/bin/env python3
"""Массовая рассылка сообщения всем пользователям с tg_id."""

import sys
import time
import logging
from pathlib import Path

import mysql.connector
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
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
    """Получает пользователей с tg_id, у которых есть активная vless_link."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        
        # Проверяем существование таблиц
        cursor.execute("SHOW TABLES LIKE 'users'")
        users_exists = cursor.fetchone() is not None
        
        cursor.execute("SHOW TABLES LIKE 'vpn_keys'")
        keys_exists = cursor.fetchone() is not None
        
        if not keys_exists:
            logger.error("❌ Таблица 'vpn_keys' не найдена в БД")
            return []
        
        # Если есть таблица users, используем JOIN
        if users_exists:
            cursor.execute("""
                SELECT DISTINCT u.tg_id, u.id, u.username, v.vless_link
                FROM users u
                INNER JOIN vpn_keys v ON (u.id = v.user_id OR u.tg_id = v.tg_id)
                WHERE u.tg_id IS NOT NULL 
                  AND u.tg_id != 0
                  AND v.vpn_type = 'vless'
                  AND v.vless_link IS NOT NULL
                  AND v.vless_link != ''
            """)
        else:
            # Если нет таблицы users, берем tg_id直接从 vpn_keys
            cursor.execute("""
                SELECT DISTINCT tg_id, NULL as id, NULL as username, vless_link
                FROM vpn_keys 
                WHERE tg_id IS NOT NULL 
                  AND tg_id != 0
                  AND vpn_type = 'vless'
                  AND vless_link IS NOT NULL
                  AND vless_link != ''
            """)
        
        rows = cursor.fetchall()
        cursor.close()
        conn.close()
        
        logger.info(f"📊 Найдено пользователей с подпиской: {len(rows)}")
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


def send_message_to_user(tg_id: int) -> bool:
    """Отправляет сообщение пользователю через Telegram."""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": tg_id,
        "text": MESSAGE,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    try:
        resp = requests.post(url, json=payload, timeout=10)
        if resp.status_code == 200:
            logger.info(f"✅ Отправлено tg_id={tg_id}")
            return True
        else:
            logger.warning(f"❌ Ошибка {resp.status_code} для tg_id={tg_id}: {resp.text}")
            return False
    except Exception as e:
        logger.error(f"❌ Исключение для tg_id={tg_id}: {e}")
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
            username = user.get('username', '-')
            logger.info(f"  - tg_id={user['tg_id']} (@{username})")
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
        username = user.get('username', '')
        
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