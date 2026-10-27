from datetime import timedelta, timezone, time, datetime
from api.db import get_user_by_tg_id, upsert_user_subscription, get_payment_by_id, get_user_by_id, update_user_subscription_by_id
from bot_xui.tariffs import TARIFFS

TZ_TOKYO = timezone(timedelta(hours=9))

def activate_subscription(payment_id: str, user_id: int | None = None):
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

    # For web-only users (tg_id=0), look up by user_id instead
    if tg_id and tg_id != 0:
        user = get_user_by_tg_id(tg_id)
    elif user_id:
        user = get_user_by_id(user_id)
    else:
        user = None

    # Calculate new subscription end date:
    # If user has active subscription (subscription_until > paid_at), extend from subscription_until,
    # otherwise extend from payment date paid_at.
    if user and user.get("subscription_until") and user["subscription_until"] > paid_at:
        base_date = user["subscription_until"]
    else:
        base_date = paid_at

    # Exact timestamp calculation: preserves exact hour, minute, second
    new_until = base_date + duration

    # Update subscription: by tg_id if available, otherwise by user_id
    if tg_id and tg_id != 0:
        upsert_user_subscription(tg_id=tg_id, subscription_until=new_until)
    elif user and user.get('id'):
        update_user_subscription_by_id(user['id'], new_until)

    # If this user was referred by someone and referral reward is pending, confirm it now
    if user and user.get('id') and user.get('referred_by') and not user.get('referral_rewarded'):
        try:
            from api.db import confirm_referral_reward
            confirm_referral_reward(user['id'])
        except Exception:
            pass

    return new_until