# bot/tariffs.py
from datetime import timedelta

TARIFFS = {
    "basic_1m": {
        "name": "Базовый — 1 месяц",
        "price": 199,
        "period": "30 дней",
        "yookassa_description": "Персональный цифровой доступ на 30 дней",
        "days": 30,
    },
    "standard_3m": {
        "name": "Стандарт — 3 месяца",
        "price": 499,
        "period": "90 дней",
        "yookassa_description": "Персональный цифровой доступ на 90 дней",
        "days": 90,
    },
    "premium_12m": {
        "name": "Премиум — 12 месяцев",
        "price": 1999,
        "period": "365 дней",
        "yookassa_description": "Персональный цифровой доступ на 12 месяцев",
        "days": 365,
    },
}

