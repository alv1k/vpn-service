# bot/tariffs.py
from datetime import timedelta

TARIFFS = {
    "test_1h": {
        "name": "Тестовый — 1 час",
        "price": 0,
        "period": "1 час",
        "yookassa_description": "Персональный цифровой доступ на 1 час",
        "days": 0,
        "hours" : 1,
        "device_limit": 1,
        "is_test": True
    },
    "basic_1m": {
        "name": "Базовый — 1 месяц",
        "price": 199,
        "period": "30 дней",
        "yookassa_description": "Персональный цифровой доступ на 30 дней",
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
    "premium_12m": {
        "name": "Премиум — 12 месяцев",
        "price": 1999,
        "period": "365 дней",
        "yookassa_description": "Персональный цифровой доступ на 12 месяцев",
        "days": 365,
        "device_limit": 10,
        "is_test": False
    },
    "admin_test": {
        "name": "для тестирования платежа",
        "price": 5,
        "period": "0 дней",
        "yookassa_description": "Персональный цифровой доступ на 12 месяцев",
        "days": 365,
        "device_limit": 10,
        "is_test": False
    },
}

