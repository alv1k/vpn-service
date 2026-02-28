from datetime import timedelta
from api.db import get_user_by_tg_id, upsert_user_subscription, get_payment_by_id
from bot_xui.tariffs import TARIFFS

def activate_subscription(payment_id: str):
    payment = get_payment_by_id(payment_id)

    if not payment:
        raise ValueError("Paid payment not found")

    tg_id = payment["tg_id"]
    tariff_id = payment["tariff"]

    tariff = TARIFFS.get(tariff_id)
    if not tariff:
        raise ValueError(f"Unknown tariff: {tariff_id}")

    duration = timedelta(days=tariff["days"])
    paid_at = payment["created_at"]

    user = get_user_by_tg_id(tg_id)

    # 🧠 если подписка ещё активна — продлеваем от текущего конца
    if user and user.get("subscription_until") and user["subscription_until"] > paid_at:
        new_until = user["subscription_until"] + duration
    else:
        new_until = paid_at + duration

    upsert_user_subscription(
        tg_id=tg_id,
        subscription_until=new_until
    )

    return new_until