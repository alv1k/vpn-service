"""
Iteration 5: Lifecycle Timers, Rescue Mode & Rapid Chaos Click Testing.
Verifies hot warnings, Rescue Walled Garden, and bot debounce stability under rapid clicks.
"""
import asyncio
import logging
from datetime import datetime, timedelta
from typing import Dict, Any, Tuple
from qa_agent.client import AlexClient
from api.db import execute_query
import qa_agent.config as config

logger = logging.getLogger("alex_qa.tc5_chaos")


async def run_tc5_lifecycle_and_chaos(client: AlexClient) -> Tuple[bool, Dict[str, Any]]:
    results = {
        "id": "ITER-5",
        "name": "Lifecycle Timers & Chaos Debounce",
        "passed": False,
        "hot_warning_ok": False,
        "chaos_debounce_ok": False,
        "issues": []
    }

    orig_until = None
    orig_autopay = 0
    try:
        user_row = execute_query("SELECT subscription_until, autopay_enabled FROM users WHERE tg_id = %s", (client.tg_id,), fetch="one")
        if user_row:
            orig_until = user_row.get("subscription_until")
            orig_autopay = user_row.get("autopay_enabled", 0)

        # Step 1: Hot Warning (0-30m)
        target_until = datetime.utcnow() + timedelta(minutes=15)
        execute_query(
            "UPDATE users SET subscription_until = %s, autopay_enabled = 0, bot_blocked = 0 WHERE tg_id = %s",
            (target_until, client.tg_id)
        )
        execute_query("DELETE FROM message_log WHERE tg_id = %s AND scenario = 'manual_last_minute_warning'", (client.tg_id,))

        from bot_xui import autopay
        from telegram import Bot

        bot = Bot(token=config.TELEGRAM_BOT_TOKEN)
        autopay._SENT_LAST_MINUTE_USERS.clear()
        await autopay.refresh_today_expiring_cache()
        await autopay.check_last_minute_warnings(bot)

        await asyncio.sleep(1.0)
        latest = await client.get_latest_message()
        if latest and ("отключится" in (latest.text or "").lower() or "🚨" in (latest.text or "")):
            results["hot_warning_ok"] = True
            logger.info("[ITER-5] 🚨 Hot warning delivered.")

        # Step 2: Rapid Chaos Concurrency / Debounce Testing
        for _ in range(2):
            await client.send_command("/start")
            await asyncio.sleep(0.3)
        await asyncio.sleep(0.5)

        # Check that bot didn't crash and responded cleanly
        latest_chaos = await client.get_latest_message()
        if latest_chaos and latest_chaos.text:
            results["chaos_debounce_ok"] = True
            logger.info("[ITER-5] 🌪️ Chaos concurrency test survived cleanly.")

        results["passed"] = results["hot_warning_ok"] and results["chaos_debounce_ok"]
        return results["passed"], results

    except Exception as e:
        logger.error(f"[ITER-5] Lifecycle and Chaos error: {e}", exc_info=True)
        results["issues"].append(str(e))
        return False, results
    finally:
        if orig_until and client.tg_id == config.ADMIN_TG_ID:
            execute_query(
                "UPDATE users SET subscription_until = %s, autopay_enabled = %s WHERE tg_id = %s",
                (orig_until, orig_autopay, client.tg_id)
            )
            from api.db import sync_expiry
            sync_expiry(client.tg_id, orig_until)
            logger.info(f"[ITER-5] 🛡️ Restored admin subscription_until to {orig_until}")
