"""
Scenario TC-1: New User Onboarding & Trial Activation (Clean Slate).
"""
import re
import logging
from typing import Dict, Any, Tuple
from qa_agent.client import AlexClient
from qa_agent.persona import PersonaEvaluator

logger = logging.getLogger("alex_qa.tc1")


async def run_tc1_onboarding(client: AlexClient, evaluator: PersonaEvaluator) -> Tuple[bool, Dict[str, Any]]:
    """
    Executes TC-1:
    1. Clean slate in DB for test user
    2. Sends /start
    3. Clicks through onboarding (trial / platform selection)
    4. Extracts subscription URL/ID
    5. Evaluates UX via AI
    """
    results = {
        "id": "TC-1",
        "name": "New User Onboarding & Trial",
        "passed": False,
        "sub_url": None,
        "sub_id": None,
        "latencies": {},
        "issues": [],
        "ai_score": 0
    }

    try:
        # Step 1: Clean Slate
        client.clean_slate_db()

        # Step 2: Send /start
        sent_msg = await client.send_command("/start")
        resp_msg, latency = await client.wait_for_response(min_id=sent_msg.id, timeout=8.0)
        results["latencies"]["start_cmd"] = latency

        if not resp_msg:
            results["issues"].append("Bot did not respond to /start command within timeout.")
            return False, results

        logger.info(f"[TC-1] Welcome text preview: {resp_msg.text[:100]}...")

        # Step 3: Check buttons & click Trial or Configs
        curr_msg = resp_msg
        if curr_msg.buttons:
            flat_btns = [b.text for row in curr_msg.buttons for b in row]
            logger.info(f"[TC-1] Available buttons on start: {flat_btns}")

            # Match trial button or configs button
            match_btn = None
            for b in flat_btns:
                b_low = b.lower()
                if "тест" in b_low or "попробовать" in b_low or "активировать" in b_low:
                    match_btn = b
                    break
                elif "мои конфиги" in b_low or "подключ" in b_low:
                    match_btn = b
                    break

            if match_btn:
                curr_msg, btn_lat = await client.click_inline_button(curr_msg, button_text_match=match_btn)
                results["latencies"]["action_click"] = btn_lat

        # If platform selection appears (iOS / Android / etc.)
        if curr_msg and curr_msg.buttons:
            flat_btns = [b.text for row in curr_msg.buttons for b in row]
            logger.info(f"[TC-1] Available buttons after click: {flat_btns}")
            for b in flat_btns:
                b_low = b.lower()
                if "ios" in b_low or "iphone" in b_low or "android" in b_low or "vless" in b_low or "hiddify" in b_low:
                    curr_msg, os_lat = await client.click_inline_button(curr_msg, button_text_match=b)
                    results["latencies"]["os_click"] = os_lat
                    break

        # Step 4: Extract sub URL / key from chat
        latest = await client.get_latest_message()
        full_text = latest.text or ""

        # Search for sub URL pattern (e.g. /sub/..., snk.wtf/sub/..., tiinservice...)
        sub_match = re.search(r'(https?://[^\s]+/sub/([a-zA-Z0-9_-]+))', full_text)
        if sub_match:
            results["sub_url"] = sub_match.group(1)
            results["sub_id"] = sub_match.group(2)
            logger.info(f"[TC-1] 🔑 Successfully extracted sub_url: {results['sub_url']}")
        else:
            # Fallback to web_token in DB
            from api.db import execute_query
            row = execute_query("SELECT web_token FROM users WHERE tg_id = %s", (client.tg_id,), fetch="one")
            if row and row.get("web_token"):
                results["sub_id"] = row["web_token"]
                results["sub_url"] = f"https://344988.snk.wtf/sub/{row['web_token']}"
                logger.info(f"[TC-1] 🔑 Found web_token in DB: {results['sub_id']}")

        # Step 5: AI Persona Evaluation
        eval_res = evaluator.evaluate_message(full_text, context="new user onboarding and key delivery")
        results["ai_score"] = eval_res.get("score", 10)
        if not eval_res.get("markup_ok", True):
            results["issues"].extend(eval_res.get("issues", []))

        # Pass condition: bot answered, issued key/sub or trial message without errors
        results["passed"] = (results["sub_id"] is not None or "подключен" in full_text.lower() or "инструкция" in full_text.lower())
        return results["passed"], results

    except Exception as e:
        logger.error(f"[TC-1] Error during onboarding test: {e}", exc_info=True)
        results["issues"].append(str(e))
        return False, results
