"""
Scenario TC-3: Hot Expiry Warning (0–30m Before Cutoff).
"""
import asyncio
import logging
from datetime import datetime, timedelta
from typing import Dict, Any, Tuple
from qa_agent.client import AlexClient
from api.db import execute_query
import qa_agent.config as config

logger = logging.getLogger("alex_qa.tc3")


async def run_tc3_hot_warning(client: AlexClient) -> Tuple[bool, Dict[str, Any]]:
    """
    Executes TC-3:
    1. Sets subscription_until to NOW() + 15 minutes in DB (non-autopay user)
    2. Runs check_last_minute_warnings() worker
    3. Waits for Telegram alert with quick renewal action
    """
    results = {
        "id": "TC-3",
        "name": "Hot Expiry Warning (0-30 min)",
        "passed": False,
        "warning_received": False,
        "issues": []
    }

    orig_until = None
    orig_autopay = 0
    try:
        if not client.tg_id:
            results["issues"].append("No tg_id available.")
            return False, results

        user_row = execute_query("SELECT subscription_until, autopay_enabled FROM users WHERE tg_id = %s", (client.tg_id,), fetch="one")
        if user_row:
            orig_until = user_row.get("subscription_until")
            orig_autopay = user_row.get("autopay_enabled", 0)

        target_until = datetime.utcnow() + timedelta(minutes=15)

        # 1. Update user state in MySQL
        execute_query(
            """
            UPDATE users 
            SET subscription_until = %s,
                autopay_enabled = 0,
                bot_blocked = 0
            WHERE tg_id = %s
            """,
            (target_until, client.tg_id)
        )
        execute_query("DELETE FROM message_log WHERE tg_id = %s AND scenario = 'manual_last_minute_warning'", (client.tg_id,))

        logger.info(f"[TC-3] ⏱️ Set subscription_until to {target_until} (in 15m) for tg_id={client.tg_id}")

        # 2. Trigger worker in bot_xui.autopay
        from bot_xui import autopay
        from telegram import Bot

        bot = Bot(token=config.TELEGRAM_BOT_TOKEN)
        autopay._SENT_LAST_MINUTE_USERS.clear()
        await autopay.refresh_today_expiring_cache()
        await autopay.check_last_minute_warnings(bot)

        # 3. Wait for warning message in chat
        await asyncio.sleep(1.0)
        latest = await client.get_latest_message()

        if latest and latest.text and ("отключится" in latest.text.lower() or "🚨" in latest.text or "минут" in latest.text.lower()):
            logger.info(f"[TC-3] 🚨 Hot warning successfully received: {latest.text[:80]}...")
            results["warning_received"] = True
            results["passed"] = True
        else:
            # Check message_log as secondary verification
            row = execute_query(
                "SELECT id FROM message_log WHERE tg_id = %s AND scenario = 'manual_last_minute_warning' ORDER BY id DESC LIMIT 1",
                (client.tg_id,),
                fetch="one"
            )
            if row:
                logger.info("[TC-3] ✅ Warning logged to DB message_log successfully.")
                results["warning_received"] = True
                results["passed"] = True
            else:
                results["issues"].append("Warning message was not triggered or received.")

        return results["passed"], results

    except Exception as e:
        logger.error(f"[TC-3] Hot warning scenario exception: {e}", exc_info=True)
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
            logger.info(f"[TC-3] 🛡️ Restored admin subscription_until to {orig_until}")
