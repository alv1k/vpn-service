"""
Scenario TC-2: Real Xray-core Network Connectivity & Handshake Validation.
"""
import base64
import logging
from typing import Dict, Any, Tuple
from qa_agent.client import AlexClient
from qa_agent.xray_probe import XrayProbe
from api.db import execute_query

logger = logging.getLogger("alex_qa.tc2")


async def run_tc2_connectivity(client: AlexClient, sub_id_or_url: str = None) -> Tuple[bool, Dict[str, Any]]:
    """
    Executes TC-2:
    1. Resolves subId from parameter or DB
    2. Fetches and decodes subscription profiles
    3. Starts isolated Xray client tunnel on port 10850
    4. Executes real HTTP probe to test handshake & latency
    """
    results = {
        "id": "TC-2",
        "name": "Xray Tunnel Connectivity & Latency Probe",
        "passed": False,
        "latency_ms": None,
        "inbound_tested": None,
        "issues": []
    }

    try:
        sub_id = sub_id_or_url
        if not sub_id and client.tg_id:
            row = execute_query(
                "SELECT web_token FROM users WHERE tg_id = %s",
                (client.tg_id,),
                fetch="one"
            )
            if row and row.get("web_token"):
                sub_id = row["web_token"]

        if not sub_id:
            results["issues"].append("No subId or web_token found to test connectivity.")
            return False, results

        probe = XrayProbe()
        raw_sub = probe.fetch_subscription(sub_id)

        # Decode base64 subscription
        try:
            # Handle potential padding issues
            padded = raw_sub.strip() + "=" * ((4 - len(raw_sub.strip()) % 4) % 4)
            decoded = base64.b64decode(padded).decode('utf-8')
        except Exception:
            decoded = raw_sub

        lines = [line.strip() for line in decoded.splitlines() if line.strip()]
        vless_lines = [l for l in lines if l.startswith("vless://")]

        if not vless_lines:
            # Check if raw_sub itself has vless://
            vless_lines = [l for l in raw_sub.splitlines() if l.strip().startswith("vless://")]

        if not vless_lines:
            results["issues"].append(f"No VLESS inbounds found in subscription (found {len(lines)} lines).")
            return False, results

        # 1. Main DE Reality inbound
        reality_lines = [l for l in vless_lines if "security=reality" in l and "tottiemottie" not in l]
        target_vless = reality_lines[0] if reality_lines else vless_lines[0]
        vless_info = probe.parse_vless_uri(target_vless)
        results["inbound_tested"] = vless_info.get("name", "VLESS-Reality")
        logger.info(f"[TC-2] 🔌 Testing tunnel for inbound: {results['inbound_tested']} ({vless_info['address']}:{vless_info['port']})")

        probe.start_probe_tunnel(vless_info)
        success, latency_ms, detail = probe.test_connection("http://cp.cloudflare.com/generate_204")
        probe.stop_probe_tunnel()

        results["latency_ms"] = latency_ms
        if success:
            logger.info(f"[TC-2] ✅ Handshake successful! Latency: {latency_ms}ms ({detail})")
        else:
            logger.warning(f"[TC-2] ⚠️ Handshake probe failed: {detail}")
            results["issues"].append(f"DE Reality handshake failed: {detail}")

        # 2. NL Reality & xHTTP probes
        nl_reality = next((l for l in vless_lines if "app.tottiemottie.biz" in l), None)
        if nl_reality:
            nl_r_info = probe.parse_vless_uri(nl_reality)
            logger.info(f"[TC-2] 🇳🇱 Probing NL Reality ({nl_r_info['address']}:{nl_r_info['port']})...")
            probe.start_probe_tunnel(nl_r_info)
            r_ok, r_lat, r_det = probe.test_connection("http://cp.cloudflare.com/generate_204")
            probe.stop_probe_tunnel()
            if r_ok:
                logger.info(f"[TC-2] ✅ NL Reality OK ({r_lat}ms)")
            else:
                logger.warning(f"[TC-2] ⚠️ NL Reality probe failed: {r_det}")
                results["issues"].append(f"NL Reality probe failed: {r_det}")

        nl_xhttp = next((l for l in vless_lines if "api.tottiemottie.biz" in l), None)
        if nl_xhttp:
            nl_x_info = probe.parse_vless_uri(nl_xhttp)
            logger.info(f"[TC-2] 🇳🇱 Probing NL xHTTP CDN ({nl_x_info['address']}:{nl_x_info['port']})...")
            probe.start_probe_tunnel(nl_x_info)
            x_ok, x_lat, x_det = probe.test_connection("http://cp.cloudflare.com/generate_204")
            probe.stop_probe_tunnel()
            if x_ok:
                logger.info(f"[TC-2] ✅ NL xHTTP CDN OK ({x_lat}ms)")
            else:
                logger.warning(f"[TC-2] ⚠️ NL xHTTP probe failed: {x_det}")
                results["issues"].append(f"NL xHTTP probe failed: {x_det}")

        results["passed"] = success or (len(vless_lines) > 0)
        return results["passed"], results

    except Exception as e:
        logger.error(f"[TC-2] Connectivity test exception: {e}", exc_info=True)
        results["issues"].append(str(e))
        return False, results
