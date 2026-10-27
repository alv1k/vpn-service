#!/usr/bin/env python3
"""
Alex (QA Sentinel) — Master Test Runner & Assistant.
Executes multi-iteration end-to-end automated testing from the perspective of a real user.
"""
import os
import sys
import time
import asyncio
import argparse
import logging
import httpx
from datetime import datetime, timedelta

# Setup paths
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

import qa_agent.config as config
from qa_agent.client import AlexClient
from qa_agent.persona import PersonaEvaluator
from qa_agent.scenarios.tc1_onboarding import run_tc1_onboarding
from qa_agent.scenarios.tc2_connectivity import run_tc2_connectivity
from qa_agent.scenarios.tc1_ui_navigation import run_tc1_ui_navigation
from qa_agent.scenarios.tc2_platforms_and_network import run_tc2_platforms_and_network
from qa_agent.scenarios.tc3_billing_and_promos import run_tc3_billing_and_promos
from qa_agent.scenarios.tc4_surveys_and_winback import run_tc4_surveys_and_winback
from qa_agent.scenarios.tc5_lifecycle_and_chaos import run_tc5_lifecycle_and_chaos

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("alex_qa.runner")


def send_telegram_report(report_text: str):
    """Sends test report directly to the Admin in Telegram."""
    if not config.TELEGRAM_BOT_TOKEN or not config.ADMIN_TG_ID:
        logger.warning("[ALEX QA] No Bot Token or Admin ID configured for Telegram report.")
        return

    url = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        resp = httpx.post(url, json={
            "chat_id": config.ADMIN_TG_ID,
            "text": report_text,
            "parse_mode": "HTML"
        }, timeout=8.0)
        if resp.status_code == 200:
            logger.info("[ALEX QA] 📨 Report successfully sent to Admin Telegram.")
        else:
            logger.error(f"[ALEX QA] Failed to send report to TG: {resp.text}")
    except Exception as e:
        logger.error(f"[ALEX QA] Error sending Telegram report: {e}")


async def main():
    parser = argparse.ArgumentParser(description="Alex QA Sentinel E2E Runner")
    parser.add_argument("--smoke", action="store_true", help="Run quick smoke test (Onboarding + Xray Probe)")
    parser.add_argument("--full", action="store_true", help="Run all 5 comprehensive test iterations")
    parser.add_argument("--iteration", type=int, choices=[1, 2, 3, 4, 5], help="Run a specific iteration (1-5)")
    parser.add_argument("--report", action="store_true", help="Send results to Telegram admin")
    args = parser.parse_args()

    # Default to smoke if nothing specified
    if not args.smoke and not args.full and not args.iteration:
        args.smoke = True

    start_time = time.monotonic()
    client = AlexClient()
    evaluator = PersonaEvaluator()
    scenario_results = []
    all_passed = True
    admin_saved_until = None
    admin_saved_autopay = 0

    logger.info("==================================================")
    logger.info("🚀 Starting Alex QA Sentinel E2E Test Suite")
    logger.info("==================================================")

    try:
        await client.start()
        if client.tg_id == config.ADMIN_TG_ID:
            from api.db import execute_query as _eq, sync_expiry as _se
            adm_row = _eq("SELECT subscription_until, autopay_enabled FROM users WHERE tg_id = %s", (client.tg_id,), fetch="one")
            if adm_row and adm_row.get("subscription_until"):
                admin_saved_until = adm_row["subscription_until"]
                admin_saved_autopay = adm_row.get("autopay_enabled", 0)
                if admin_saved_until <= datetime.utcnow() + timedelta(days=1):
                    admin_saved_until = datetime.utcnow() + timedelta(days=365)
                    _se(client.tg_id, admin_saved_until)
                    logger.info(f"[ALEX QA] 🛡️ Auto-healed expired Admin subscription to {admin_saved_until}")
                else:
                    logger.info(f"[ALEX QA] 🛡️ Guarded Admin initial subscription: {admin_saved_until}")

        if args.smoke:
            logger.info("\n--- [Running Smoke: Onboarding] ---")
            p1, r1 = await run_tc1_onboarding(client, evaluator)
            scenario_results.append(r1)
            if not p1: all_passed = False

            logger.info("\n--- [Running Smoke: Connectivity Probe] ---")
            p2, r2 = await run_tc2_connectivity(client, sub_id_or_url=r1.get("sub_id"))
            scenario_results.append(r2)
            if not p2: all_passed = False

        elif args.iteration:
            iter_map = {
                1: lambda: run_tc1_ui_navigation(client, evaluator),
                2: lambda: run_tc2_platforms_and_network(client),
                3: lambda: run_tc3_billing_and_promos(client),
                4: lambda: run_tc4_surveys_and_winback(client),
                5: lambda: run_tc5_lifecycle_and_chaos(client),
            }
            p, r = await iter_map[args.iteration]()
            scenario_results.append(r)
            if not p: all_passed = False

        elif args.full:
            logger.info("\n--- [Iteration 1: Full UI Navigation & Knowledge Base] ---")
            p1, r1 = await run_tc1_ui_navigation(client, evaluator)
            scenario_results.append(r1)
            if not p1: all_passed = False
            await asyncio.sleep(1.2)

            logger.info("\n--- [Iteration 2: Configs, Platforms & Live Network Probes] ---")
            p2, r2 = await run_tc2_platforms_and_network(client)
            scenario_results.append(r2)
            if not p2: all_passed = False
            await asyncio.sleep(1.2)

            logger.info("\n--- [Iteration 3: Billing, Payments & Referral QR] ---")
            p3, r3 = await run_tc3_billing_and_promos(client)
            scenario_results.append(r3)
            if not p3: all_passed = False
            await asyncio.sleep(1.2)

            logger.info("\n--- [Iteration 4: Surveys, Win-back 20% & Loyalty Gift] ---")
            p4, r4 = await run_tc4_surveys_and_winback(client)
            scenario_results.append(r4)
            if not p4: all_passed = False
            await asyncio.sleep(1.2)

            logger.info("\n--- [Iteration 5: Lifecycle Timers, Rescue Mode & Chaos] ---")
            p5, r5 = await run_tc5_lifecycle_and_chaos(client)
            scenario_results.append(r5)
            if not p5: all_passed = False

    except Exception as e:
        logger.error(f"[ALEX QA] Unexpected fatal runner error: {e}", exc_info=True)
        all_passed = False
    finally:
        if admin_saved_until and client.tg_id == config.ADMIN_TG_ID:
            from api.db import execute_query as _eq, sync_expiry as _se
            _eq(
                "UPDATE users SET subscription_until = %s, autopay_enabled = %s, in_rescue = 0 WHERE tg_id = %s",
                (admin_saved_until, admin_saved_autopay, client.tg_id)
            )
            _se(client.tg_id, admin_saved_until)
            logger.info(f"[ALEX QA] 🛡️ Final restore of Admin subscription_until to {admin_saved_until}")
        await client.stop()

    total_duration = round(time.monotonic() - start_time, 2)
    passed_count = sum(1 for r in scenario_results if r.get("passed", False))
    total_count = len(scenario_results)

    logger.info("==================================================")
    logger.info(f"📊 Alex QA Summary: {passed_count}/{total_count} PASSED in {total_duration}s")
    logger.info("==================================================")

    # Format Telegram HTML report
    status_icon = "✅" if all_passed else "❌"
    mode_name = "Full Comprehensive Matrix (5 Iterations)" if args.full else (f"Iteration {args.iteration}" if args.iteration else "Smoke Test")
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    report_lines = [
        f"{status_icon} <b>[Alex QA Sentinel] {mode_name}</b>",
        f"🕒 <code>{timestamp}</code> | ⏱️ <b>{total_duration}s</b>",
        f"📊 Результат: <b>{passed_count}/{total_count} пройдено</b>\n"
    ]

    for res in scenario_results:
        s_icon = "✅" if res.get("passed") else "❌"
        r_name = res.get("name", res.get("id"))
        report_lines.append(f"{s_icon} <b>{res.get('id')}</b>: {r_name}")
        if res.get("latency_ms"):
            report_lines.append(f"   ↳ <i>Xray Latency: {res['latency_ms']}ms</i>")
        if res.get("latencies_ms"):
            lat_str = ", ".join([f"{k}: {v}ms" for k, v in list(res["latencies_ms"].items())[:2]])
            report_lines.append(f"   ↳ <i>Inbounds: {lat_str}</i>")
        if res.get("ai_score"):
            report_lines.append(f"   ↳ <i>AI UX Score: {res['ai_score']}/10</i>")
        if res.get("issues"):
            issues_str = "; ".join(res["issues"][:2])
            report_lines.append(f"   ↳ ⚠️ <code>{issues_str}</code>")

    report_text = "\n".join(report_lines)

    if args.report or not all_passed:
        send_telegram_report(report_text)

    sys.exit(0 if all_passed else 1)


if __name__ == "__main__":
    asyncio.run(main())
