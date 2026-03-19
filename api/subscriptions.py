from datetime import timedelta, timezone, time, datetime
from api.db import get_user_by_tg_id, upsert_user_subscription, get_payment_by_id
from bot_xui.tariffs import TARIFFS

TZ_TOKYO = timezone(timedelta(hours=9))

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

    # Округляем до 23:59:59 по Tokyo (+9)
    if new_until.tzinfo is None:
        new_until = new_until.replace(tzinfo=timezone.utc)
    new_until_tokyo = new_until.astimezone(TZ_TOKYO)
    new_until_eod = new_until_tokyo.replace(hour=23, minute=59, second=59, microsecond=0)
    new_until = new_until_eod.astimezone(timezone.utc).replace(tzinfo=None)

    upsert_user_subscription(
        tg_id=tg_id,
        subscription_until=new_until
    )

    return new_until