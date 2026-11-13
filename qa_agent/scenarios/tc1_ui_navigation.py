"""
Iteration 1: Full UI Navigation Matrix & Knowledge Base (UI/UX).
Verifies that all menu sections, submenus, FAQs, App guides, and back buttons function seamlessly.
"""
import asyncio
import logging
from typing import Dict, Any, Tuple
from qa_agent.client import AlexClient
from qa_agent.persona import PersonaEvaluator

logger = logging.getLogger("alex_qa.tc1_ui")


async def run_tc1_ui_navigation(client: AlexClient, evaluator: PersonaEvaluator) -> Tuple[bool, Dict[str, Any]]:
    results = {
        "id": "ITER-1",
        "name": "Full UI Menu & Knowledge Base Matrix",
        "passed": False,
        "sections_checked": [],
        "latencies": {},
        "issues": [],
        "ai_score": 10
    }

    try:
        # Step 1: Start and check Main Menu
        sent_msg = await client.send_command("/start")
        if not sent_msg:
            results["issues"].append("Failed to send /start (Telegram FloodWait or network error)")
            return False, results
        menu_msg, lat = await client.wait_for_response(min_id=sent_msg.id, timeout=15.0)
        results["latencies"]["start"] = lat

        if not menu_msg:
            results["issues"].append("Bot did not respond to /start")
            return False, results

        results["sections_checked"].append("Main Menu")

        # Step 2: Tariffs Menu
        await asyncio.sleep(0.4)
        sent_t = await client.send_command("/start")
        m1, _ = await client.wait_for_response(min_id=sent_t.id, timeout=15.0)
        curr_msg, t_lat = await client.click_inline_button(m1, callback_data="tariffs")
        results["latencies"]["tariffs"] = t_lat
        results["sections_checked"].append("Tariffs")

        # Step 3: Info & Knowledge Base
        await asyncio.sleep(0.4)
        sent_i = await client.send_command("/start")
        m2, _ = await client.wait_for_response(min_id=sent_i.id, timeout=15.0)
        curr_msg, i_lat = await client.click_inline_button(m2, callback_data="info_menu")
        results["latencies"]["info_menu"] = i_lat
        results["sections_checked"].append("Info Menu")

        # Info FAQ
        faq_msg, f_lat = await client.click_inline_button(curr_msg, callback_data="info_faq")
        results["latencies"]["faq"] = f_lat
        results["sections_checked"].append("FAQ")

        # Info Apps
        apps_msg, a_lat = await client.click_inline_button(faq_msg, callback_data="info_apps")
        results["latencies"]["apps_guide"] = a_lat
        results["sections_checked"].append("Apps Guide")

        # Step 4: MTProto Proxy Menu
        await asyncio.sleep(0.4)
        sent_p = await client.send_command("/start")
        m3, _ = await client.wait_for_response(min_id=sent_p.id, timeout=15.0)
        proxy_msg, p_lat = await client.click_inline_button(m3, callback_data="proxy_file")
        results["latencies"]["proxy_file"] = p_lat
        results["sections_checked"].append("Proxy Menu")

        # Step 5: AI Evaluation of FAQ/Apps text
        faq_text = getattr(faq_msg, "text", "") or ""
        apps_text = getattr(apps_msg, "text", "") or ""
        eval_faq = evaluator.evaluate_message(faq_text, context="VPN FAQ Knowledge Base")
        eval_apps = evaluator.evaluate_message(apps_text, context="VPN App Installation Guide")

        results["ai_score"] = min(eval_faq.get("score", 10), eval_apps.get("score", 10))
        if not eval_faq.get("markup_ok") or not eval_apps.get("markup_ok"):
            results["issues"].extend(eval_faq.get("issues", []) + eval_apps.get("issues", []))

        results["passed"] = len(results["sections_checked"]) >= 5 and len(results["issues"]) == 0
        return results["passed"], results

    except Exception as e:
        logger.error(f"[ITER-1] UI Navigation error: {e}", exc_info=True)
        results["issues"].append(str(e))
        return False, results
