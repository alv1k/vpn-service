"""
Рантайм-управление тестовым режимом оплаты.
Тестовый режим доступен только для ADMIN_TG_ID.
Состояние хранится в файле, чтобы быть доступным между процессами (bot + api).
"""
import logging
import os

logger = logging.getLogger(__name__)

_FLAG_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".test_mode")


def is_test_mode() -> bool:
    return os.path.exists(_FLAG_FILE)


def toggle_test_mode() -> bool:
    """Переключает тестовый режим. Возвращает новое состояние."""
    if is_test_mode():
        os.remove(_FLAG_FILE)
        logger.info("YooKassa test mode: OFF")
        return False
    else:
        with open(_FLAG_FILE, "w") as f:
            f.write("1")
        logger.info("YooKassa test mode: ON")
        return True


def set_test_mode(enabled: bool):
    if enabled:
        with open(_FLAG_FILE, "w") as f:
            f.write("1")
    elif os.path.exists(_FLAG_FILE):
        os.remove(_FLAG_FILE)
    logger.info(f"YooKassa test mode set to: {'ON' if enabled else 'OFF'}")
