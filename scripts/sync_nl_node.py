#!/usr/bin/env python3
"""
Скрипт полной сверки и синхронизации клиентов с NL-нодой (3x-ui).
Синхронизирует ТОЛЬКО инбаунды id=8 (Reality Vision) и id=9 (xHTTP CDN).
Инбаунд id=7 (служебный YouTube/RU failover) НЕ затрагивается.

Запуск:
    python3 scripts/sync_nl_node.py [--dry-run]
"""
import sys
import os
import argparse
import logging
from datetime import datetime, timezone
import json as json_lib

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import (
    XUI_HOST, XUI_USERNAME, XUI_PASSWORD,
    NL_XUI_HOST, NL_XUI_USERNAME, NL_XUI_PASSWORD,
    NL_ACTIVE_INBOUND_IDS,
)
from bot_xui.utils import XUIClient
from bot_xui.multi_node import get_nl_xui_client

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

NL_INBOUND_FLOW = {8: "xtls-rprx-vision", 9: ""}


def sync_all_clients(dry_run: bool = False):
    logger.info("🚀 Starting full synchronization with NL Node...")
    main_xui = XUIClient(XUI_HOST, XUI_USERNAME, XUI_PASSWORD)
    nl_xui = get_nl_xui_client()

    if not main_xui.login():
        logger.error("❌ Failed to login to Main 3x-ui")
        return

    if not nl_xui:
        logger.error("❌ NL 3x-ui not configured (NL_XUI_HOST empty)")
        return
    if not nl_xui.login():
        logger.error("❌ Failed to login to NL 3x-ui")
        return

    main_inbounds = main_xui.get_inbounds()
    nl_inbounds = nl_xui.get_inbounds()

    logger.info(f"Main inbounds: {len(main_inbounds)}, NL inbounds: {len(nl_inbounds)}")

    # 1. Collect all canonical clients from Main 3x-ui
    main_clients = {}
    for inb in main_inbounds:
        if inb.get("protocol") == "amneziawg" or inb.get("id") == 17:
            continue
        raw_sett = inb.get("settings", "{}")
        sett = raw_sett if isinstance(raw_sett, dict) else json_lib.loads(raw_sett)
        for cl in sett.get("clients", []):
            email = cl.get("email")
            if not email:
                continue
            if email not in main_clients:
                main_clients[email] = dict(cl)
            else:
                if not main_clients[email].get("auth") and cl.get("auth"):
                    main_clients[email]["auth"] = cl.get("auth")
                if not main_clients[email].get("password") and cl.get("password"):
                    main_clients[email]["password"] = cl.get("password")
                if not main_clients[email].get("subId") and cl.get("subId"):
                    main_clients[email]["subId"] = cl.get("subId")

    logger.info(f"Found {len(main_clients)} unique clients in Main 3x-ui")

    # 2. Build map of existing NL clients across target inbounds (8, 9)
    nl_clients_global = {}
    nl_clients_by_inbound = {}

    for inb in nl_inbounds:
        ib_id = inb["id"]
        if ib_id not in NL_ACTIVE_INBOUND_IDS:
            continue
        raw_sett = inb.get("settings", "{}")
        sett = raw_sett if isinstance(raw_sett, dict) else json_lib.loads(raw_sett)
        inb_clients = sett.get("clients", [])
        nl_clients_by_inbound[ib_id] = {c.get("email"): c for c in inb_clients if c.get("email")}
        for c in inb_clients:
            em = c.get("email")
            if em and em not in nl_clients_global:
                nl_clients_global[em] = c

    updated_total = 0
    created_total = 0
    synced_total = 0

    # 3. Synchronize attributes for all existing clients (once globally)
    for em, mc in main_clients.items():
        if em in nl_clients_global:
            rc = nl_clients_global[em]
            m_exp = mc.get("expiryTime", 0)
            m_enable = mc.get("enable", True)
            m_uuid = mc.get("id") or mc.get("uuid")
            m_auth = mc.get("auth") or (m_uuid.replace("-", "") if m_uuid else None)
            m_sub_id = mc.get("subId")

            id_diff = bool(m_uuid and rc.get("id") != m_uuid)
            auth_diff = bool(m_auth and (rc.get("auth") != m_auth or rc.get("password") != m_auth))
            exp_diff = (rc.get("expiryTime") != m_exp)
            enable_diff = (rc.get("enable") != m_enable)
            sub_diff = bool(m_sub_id and rc.get("subId") != m_sub_id)

            if id_diff or auth_diff or exp_diff or enable_diff or sub_diff:
                logger.info(f"🔄 [DIFF] Client {em}: syncing UUID/subId/auth/status")
                if not dry_run:
                    update_payload = {
                        "email": em,
                        "id": m_uuid,
                        "uuid": m_uuid,
                        "subId": m_sub_id,
                        "sub_id": m_sub_id,
                        "expiryTime": m_exp,
                        "enable": bool(m_enable),
                        "totalGB": mc.get("totalGB", 0),
                        "limitIp": mc.get("limitIp", 10),
                        "reset": mc.get("reset", 0),
                        "auth": m_auth,
                        "password": m_auth,
                    }
                    try:
                        resp = nl_xui._request(
                            "POST",
                            f"{nl_xui.host}/panel/api/clients/update/{em}",
                            json=update_payload,
                            headers={"Content-Type": "application/json"}
                        )
                        if resp.status_code == 200 and resp.json().get("success"):
                            rc["id"] = m_uuid
                            rc["subId"] = m_sub_id
                            rc["auth"] = m_auth
                            rc["password"] = m_auth
                            rc["expiryTime"] = m_exp
                            rc["enable"] = m_enable
                    except Exception as e:
                        logger.warning(f"Failed to update client {em}: {e}")
                updated_total += 1
            else:
                synced_total += 1

    # 4. Synchronize inbound attachments on NL (only inbounds 8, 9)
    # Check each client across NL_ACTIVE_INBOUND_IDS
    for em, mc in main_clients.items():
        missing_inbounds = [
            ib_id for ib_id in NL_ACTIVE_INBOUND_IDS
            if em not in nl_clients_by_inbound.get(ib_id, {})
        ]

        if not missing_inbounds:
            continue

        m_uuid = mc.get("id") or mc.get("uuid")
        m_auth = mc.get("auth") or (m_uuid.replace("-", "") if m_uuid else None)
        m_sub_id = mc.get("subId")
        logger.info(f"➕ [MISSING] Client {em} missing in inbounds {missing_inbounds}. Adding...")

        if not dry_run:
            if em in nl_clients_global:
                # Client exists in global table, attach missing inbounds
                for ib_id in missing_inbounds:
                    try:
                        resp = nl_xui._request(
                            "POST",
                            f"{nl_xui.host}/panel/api/clients/{em}/attach",
                            json={"inboundIds": [ib_id]},
                            headers={"Content-Type": "application/json"}
                        )
                        if resp.status_code == 200 and resp.json().get("success"):
                            nl_clients_by_inbound.setdefault(ib_id, {})[em] = dict(mc)
                            # Fix flow_override in client_inbounds for inbound 8 vs 9
                            flow = NL_INBOUND_FLOW.get(ib_id, "")
                            nl_xui._request(
                                "POST",
                                f"{nl_xui.host}/panel/api/clients/{em}/inbound/{ib_id}/flow",
                                json={"flow": flow},
                                headers={"Content-Type": "application/json"}
                            )
                    except Exception as e:
                        logger.warning(f"Failed to attach client {em} to inbound {ib_id}: {e}")
            else:
                # Client not in NL at all -> create with all missing inbounds
                new_cl = {
                    "id": m_uuid,
                    "email": em,
                    "flow": "xtls-rprx-vision",
                    "limitIp": mc.get("limitIp", 10),
                    "totalGB": mc.get("totalGB", 0),
                    "expiryTime": mc.get("expiryTime", 0),
                    "enable": mc.get("enable", True),
                    "tgId": mc.get("tgId", 0),
                    "subId": m_sub_id or "",
                    "reset": mc.get("reset", 0),
                    "auth": m_auth,
                    "password": m_auth,
                }
                try:
                    resp = nl_xui._request(
                        "POST",
                        f"{nl_xui.host}/panel/api/clients/add",
                        json={"client": new_cl, "inboundIds": missing_inbounds},
                        headers={"Content-Type": "application/json"}
                    )
                    if resp.status_code == 200 and resp.json().get("success"):
                        nl_clients_global[em] = new_cl
                        for ib_id in missing_inbounds:
                            nl_clients_by_inbound.setdefault(ib_id, {})[em] = new_cl
                except Exception as e:
                    logger.warning(f"Failed to create client {em} on NL: {e}")
        created_total += len(missing_inbounds)

    # 5. Fix flow in client_inbounds for inbound 9 (xHTTP requires empty flow)
    if not dry_run and created_total > 0:
        import subprocess
        fix_cmd = """sudo python3 -c "import sqlite3; conn = sqlite3.connect('/etc/x-ui/x-ui.db'); conn.execute('UPDATE client_inbounds SET flow_override=\"\" WHERE inbound_id=9'); conn.commit(); conn.execute('PRAGMA wal_checkpoint(TRUNCATE)'); conn.close()" """
        try:
            subprocess.run(["ssh", "2.26.76.210", fix_cmd], check=True)
            logger.info("🔧 [NL-Node] Fixed flow_override='' for inbound 9 (xHTTP)")
        except Exception as e:
            logger.warning(f"Failed to fix flow_override on NL: {e}")

    if (created_total > 0 or updated_total > 0) and not dry_run:
        try:
            nl_xui._request("POST", f"{nl_xui.host}/panel/api/server/restartXrayService")
            logger.info("🔄 [NL-Node] Relaunched Xray on NL node to apply updated client tables")
        except Exception as e:
            logger.info(f"🔄 [NL-Node] Relaunched Xray on NL node (socket closed as expected: {e})")

    logger.info("=" * 50)
    logger.info(f"🎉 NL Sync finished. Results: Synced in harmony: {synced_total}, Created: {created_total}, Updated: {updated_total}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Sync 3x-ui clients to NL exit node")
    parser.add_argument("--dry-run", action="store_true", help="Only check differences without making changes")
    args = parser.parse_args()
    sync_all_clients(dry_run=args.dry_run)
