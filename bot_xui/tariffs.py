# bot/tariffs.py
from datetime import timedelta

TARIFFS = {    
    "test_24h": {
        "name": "Тестовый — 24 часа",
        "price": 0,
        "period": "24 часа",
        "yookassa_description": "Персональный цифровой доступ на 24 часа",
        "days": 0,
        "hours" : 24,
        "device_limit": 1,
        "is_test": True
    },
    "trial_1d": {
        "name": "Пробный — 1 день",
        "price": 10,
        "period": "1 день",
        "yookassa_description": "Персональный цифровой доступ на 1 день",
        "days": 1,
        "device_limit": 10,
        "is_test": False
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
}

