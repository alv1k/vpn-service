"""
Iteration 3: Billing, Payments, Referral QR & Promocodes.
Verifies tariff selection, referral QR generation, and simulated payment activation.
"""
import asyncio
import logging
from datetime import datetime, timedelta
from typing import Dict, Any, Tuple
from qa_agent.client import AlexClient
from api.db import execute_query

logger = logging.getLogger("alex_qa.tc3_billing")


async def run_tc3_billing_and_promos(client: AlexClient) -> Tuple[bool, Dict[str, Any]]:
    results = {
        "id": "ITER-3",
        "name": "Billing, Payments & Referral QR Matrix",
        "passed": False,
        "referral_qr_ok": False,
        "payment_sim_ok": False,
        "tariffs_selectable": False,
        "issues": []
    }

    try:
        # Step 1: Test Referral System via /refer
        sent_ref = await client.send_command("/refer")
        if not sent_ref:
            results["issues"].append("Failed to send /refer (Telegram FloodWait or network error)")
            return False, results
        ref_msg, _ = await client.wait_for_response(min_id=sent_ref.id, timeout=15.0)
        if ref_msg and (ref_msg.photo or "ref_" in (ref_msg.text or "") or "приглаш" in (ref_msg.text or "").lower() or ref_msg.caption):
            results["referral_qr_ok"] = True
            logger.info("[ITER-3] 👥 Referral QR and link generated successfully.")

        # Step 2: Open Tariffs and click 1-Month Plan
        await asyncio.sleep(1.0)
        sent_msg = await client.send_command("/start")
        if not sent_msg:
            results["issues"].append("Failed to send /start (Telegram FloodWait or network error)")
            return False, results
        menu_msg, _ = await client.wait_for_response(min_id=sent_msg.id, timeout=25.0)
        if menu_msg and getattr(menu_msg, "buttons", None):
            tariffs_msg, _ = await client.click_inline_button(menu_msg, callback_data="tariffs", timeout=25.0)
            if tariffs_msg and getattr(tariffs_msg, "buttons", None):
                # Click 1 month buy
                buy_msg, _ = await client.click_inline_button(tariffs_msg, callback_data="buy_tariff_monthly_30d", timeout=25.0)
                if buy_msg and ("оплат" in (buy_msg.text or "").lower() or "руб" in (buy_msg.text or "").lower() or getattr(buy_msg, "buttons", None)):
                    results["tariffs_selectable"] = True
                    logger.info("[ITER-3] 💎 Tariff checkout view opened.")

        # Step 3: Simulate Payment Webhook and Extension
        initial_sub = None
        row = execute_query("SELECT subscription_until FROM users WHERE tg_id = %s", (client.tg_id,), fetch="one")
        if row and row.get("subscription_until"):
            initial_sub = row["subscription_until"]
        else:
            initial_sub = datetime.utcnow()

        # Insert simulated payment
        payment_id = f"qa_test_pay_{int(datetime.utcnow().timestamp())}"
        execute_query(
            """
            INSERT INTO payments (payment_id, tg_id, amount, tariff, status, created_at)
            VALUES (%s, %s, %s, %s, %s, NOW())
            """,
            (payment_id, client.tg_id, 150, "monthly_30d", "paid")
        )

        from api.subscriptions import activate_subscription
        new_until = activate_subscription(payment_id)

        if new_until and new_until > initial_sub:
            results["payment_sim_ok"] = True
            logger.info(f"[ITER-3] 💳 Simulated payment successful! Sub extended to {new_until}")

        # Clean up test payment
        execute_query("DELETE FROM payments WHERE payment_id = %s", (payment_id,))

        results["passed"] = results["referral_qr_ok"] and results["tariffs_selectable"] and results["payment_sim_ok"]
        return results["passed"], results

    except Exception as e:
        logger.error(f"[ITER-3] Billing scenario error: {e}", exc_info=True)
        results["issues"].append(str(e))
        return False, results
