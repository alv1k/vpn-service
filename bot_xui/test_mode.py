"""
Рантайм-управление тестовым режимом оплаты.
Тестовый режим доступен только для ADMIN_TG_ID.
"""
import logging

logger = logging.getLogger(__name__)

_test_mode_enabled = False


def is_test_mode() -> bool:
    return _test_mode_enabled


def toggle_test_mode() -> bool:
    """Переключает тестовый режим. Возвращает новое состояние."""
    global _test_mode_enabled
    _test_mode_enabled = not _test_mode_enabled
    logger.info(f"YooKassa test mode: {'ON' if _test_mode_enabled else 'OFF'}")
    return _test_mode_enabled


def set_test_mode(enabled: bool):
    global _test_mode_enabled
    _test_mode_enabled = enabled
    logger.info(f"YooKassa test mode set to: {'ON' if enabled else 'OFF'}")
