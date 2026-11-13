"""
Automatic YooKassa payment reconciliation background worker.
Periodically audits pending payments to catch missing or delayed webhooks.

STRICT INVARIANTS:
1. If a webhook arrives naturally, the DB payment status becomes 'paid' or 'canceled'.
   The reconciler strictly queries `WHERE status = 'pending'`, ensuring YooKassa is NEVER polled
   for payments that already received a webhook.
2. A grace period (e.g. 45 seconds) allows incoming webhooks from YooKassa to arrive first.
3. All YooKassa queries go through `bot_xui/yookassa_client.py` (enforcing socket timeout + RU proxy).
4. Succeeded payments are posted locally to `http://127.0.0.1:8000/webhook`, ensuring standard
   atomic claiming, config generation, and Telegram notification delivery.
"""
import logging
import httpx
from typing import Optional

from api.db import (
    get_pending_payments_for_reconciliation,
    expire_stale_pending_payments,
    get_payment_status,
    update_payment_status,
)
from bot_xui.yookassa_client import async_payment_find

logger = logging.getLogger(__name__)

LOCAL_WEBHOOK_URL = "http://127.0.0.1:8000/webhook"


async def reconcile_pending_payments(bot=None):
    """
    Background job running every 1-2 minutes.
    Checks pending payments that have aged past the natural webhook grace period (45s).
    """
    try:
        # 1. Clean up stale abandoned pending payments older than 60 minutes
        expired_count = expire_stale_pending_payments(max_age_minutes=60)
        if expired_count > 0:
            logger.info(f"[RECONCILER] Expired {expired_count} stale pending payment(s) (>60m)")

        # 2. Select active pending payments between 45 seconds and 60 minutes old
        pending = get_pending_payments_for_reconciliation(
            grace_seconds=45,
            max_age_minutes=60,
            limit=10,
        )

        # Fast exit if nothing to reconcile (0 YooKassa API calls)
        if not pending:
            return

        logger.info(f"[RECONCILER] Found {len(pending)} pending payment(s) requiring verification")

        for item in pending:
            payment_id = item.get("payment_id")
            if not payment_id:
                continue

            # Invariant: Re-verify payment is still 'pending' in DB before polling
            current_status = get_payment_status(payment_id)
            if current_status != "pending":
                logger.info(f"[RECONCILER] Payment {payment_id} is already '{current_status}', skipping")
                continue

            is_test = bool(item.get("is_test", 0))

            try:
                yk_payment = await async_payment_find(payment_id, is_test=is_test)
            except Exception as e:
                err_str = str(e)
                # If payment does not exist in YooKassa (e.g. test artifact or 404), mark canceled
                if "not_found" in err_str.lower() or "not found" in err_str.lower() or "responseprocessingerror" in err_str.lower():
                    logger.warning(f"[RECONCILER] Payment {payment_id} not found in YooKassa, marking canceled: {e}")
                    update_payment_status(payment_id, "canceled")
                else:
                    logger.warning(f"[RECONCILER] Failed to fetch status for {payment_id} from YooKassa: {e}")
                continue

            yk_status = getattr(yk_payment, "status", None)
            yk_paid = getattr(yk_payment, "paid", False)

            logger.info(f"[RECONCILER] YooKassa response for {payment_id}: status={yk_status}, paid={yk_paid}")

            if yk_status == "succeeded" and yk_paid:
                amount_val = str(yk_payment.amount.value) if getattr(yk_payment, "amount", None) else str(item.get("amount", 0))
                currency = getattr(yk_payment.amount, "currency", "RUB") if getattr(yk_payment, "amount", None) else "RUB"

                pm = getattr(yk_payment, "payment_method", None)
                pm_dict = {}
                if pm:
                    pm_dict = {
                        "id": getattr(pm, "id", None),
                        "saved": getattr(pm, "saved", False),
                        "title": getattr(pm, "title", ""),
                        "type": getattr(pm, "type", ""),
                    }

                canc = getattr(yk_payment, "cancellation_details", None)
                canc_dict = {}
                if canc:
                    canc_dict = {
                        "party": getattr(canc, "party", ""),
                        "reason": getattr(canc, "reason", ""),
                    }

                payload = {
                    "type": "notification",
                    "event": "payment.succeeded",
                    "object": {
                        "id": yk_payment.id,
                        "status": "succeeded",
                        "paid": True,
                        "amount": {
                            "value": amount_val,
                            "currency": currency,
                        },
                        "metadata": dict(getattr(yk_payment, "metadata", None) or {}),
                        "payment_method": pm_dict,
                        "cancellation_details": canc_dict,
                    },
                }

                logger.info(f"[RECONCILER] Triggering local webhook for succeeded payment: {payment_id}")
                async with httpx.AsyncClient(timeout=15.0) as client:
                    resp = await client.post(LOCAL_WEBHOOK_URL, json=payload)
                    if resp.status_code == 200:
                        logger.info(f"✅ [RECONCILER] Succeeded payment {payment_id} reconciled and processed successfully")
                    else:
                        logger.error(f"❌ [RECONCILER] Webhook endpoint returned {resp.status_code} for {payment_id}: {resp.text}")

            elif yk_status in ("canceled", "failed"):
                logger.info(f"[RECONCILER] Payment {payment_id} is canceled in YooKassa, updating DB")
                canc = getattr(yk_payment, "cancellation_details", None)
                canc_dict = {}
                if canc:
                    canc_dict = {
                        "party": getattr(canc, "party", ""),
                        "reason": getattr(canc, "reason", ""),
                    }

                payload = {
                    "type": "notification",
                    "event": "payment.canceled",
                    "object": {
                        "id": yk_payment.id,
                        "status": "canceled",
                        "paid": False,
                        "amount": {
                            "value": str(getattr(yk_payment.amount, "value", item.get("amount", 0))),
                            "currency": "RUB",
                        },
                        "metadata": dict(getattr(yk_payment, "metadata", None) or {}),
                        "cancellation_details": canc_dict,
                    },
                }
                async with httpx.AsyncClient(timeout=10.0) as client:
                    await client.post(LOCAL_WEBHOOK_URL, json=payload)

            elif yk_status == "pending":
                logger.debug(f"[RECONCILER] Payment {payment_id} is still pending at YooKassa")

    except Exception as e:
        logger.exception(f"[RECONCILER] Unexpected error in reconcile_pending_payments: {e}")
