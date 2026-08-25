# bot/tariffs.py
from datetime import timedelta

TARIFFS = {    
    "test_24h": {
        "name": "Тестовый — 3 дня",
        "price": 0,
        "period": "3 дня",
        "yookassa_description": "Персональный цифровой доступ на 3 дня",
        "days": 3,
        "hours" : 72,
        "device_limit": 10,
        "is_test": True
    },
    "weekly_7d": {
        "name": "Неделя — 7 дней",
        "price": 50,
        "period": "7 дней",
        "yookassa_description": "Персональный цифровой доступ на неделю",
        "days": 7,
        "device_limit": 10,
        "is_test": False
    },
    "monthly_30d": {
        "name": "Месяц — 30 дней",
        "price": 199,
        "period": "30 дней",
        "yookassa_description": "Персональный цифровой доступ на 30 дней (самый выгодный)",
        "days": 30,
        "device_limit": 10,
        "is_test": False
    },
    "standard_3m": {
        "name": "Стандарт — 3 месяца",
        "price": 499,
        "period": "90 дней",
        "yookassa_description": "Персональный цифровой доступ на 90 дней",
        "days": 90,
        "device_limit": 10,
        "is_test": False
    },
    "annual_365d": {
        "name": "Год — 365 дней",
        "price": 1490,
        "period": "365 дней",
        "yookassa_description": "Персональный цифровой доступ на 1 год",
        "days": 365,
        "device_limit": 10,
        "is_test": False
    },
}


def get_effective_tariff_price(tariff_key: str, tg_id: int = None, discount_percent: int = 0) -> int:
    """Calculate tariff price considering user's winback/active discount."""
    base = TARIFFS.get(tariff_key, {}).get("price", 0)
    if base <= 0:
        return base
    
    if not discount_percent and tg_id:
        try:
            from api.db import has_winback_discount
            from config import WINBACK_DISCOUNT_PERCENT
            if has_winback_discount(tg_id):
                discount_percent = WINBACK_DISCOUNT_PERCENT
        except Exception:
            pass

    if discount_percent > 0:
        discounted = int(round(base * (1 - discount_percent / 100.0)))
        return max(1, discounted)
    return base


