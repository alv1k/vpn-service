#!/usr/bin/env python3
"""
Скрипт полной сверки и синхронизации клиентов с RU-нодой (3x-ui).
Запуск:
    python3 scripts/sync_ru_node.py [--dry-run]
"""
import sys
import os
import argparse
import logging
from datetime import datetime, timezone

# Add project root to sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import (
    XUI_HOST, XUI_USERNAME, XUI_PASSWORD,
    RU_XUI_HOST, RU_XUI_USERNAME, RU_XUI_PASSWORD,
    RU_ACTIVE_INBOUND_IDS
)
from bot_xui.utils import XUIClient
from bot_xui.multi_node import get_ru_xui_client, sync_client_to_ru_node

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def sync_all_clients(dry_run: bool = False):
    logger.info("🚀 Starting full synchronization with RU Node...")
    main_xui = XUIClient(XUI_HOST, XUI_USERNAME, XUI_PASSWORD)
    ru_xui = get_ru_xui_client()

    if not main_xui.login():
        logger.error("❌ Failed to login to Main 3x-ui")
        return

    if not ru_xui or not ru_xui.login():
        logger.error("❌ Failed to login to RU 3x-ui")
        return

    main_inbounds = main_xui.get_inbounds()
    ru_inbounds = ru_xui.get_inbounds()

    logger.info(f"Main inbounds: {len(main_inbounds)}, RU inbounds: {len(ru_inbounds)}")

    # Collect all clients from Main 3x-ui (prioritize settings.clients where 'id' is UUID)
    import json as json_lib
    main_clients = {}
    for inb in main_inbounds:
        if inb.get("protocol") == "amneziawg" or inb.get("id") == 17:
            continue
        raw_sett = inb.get("settings", "{}")
        sett = json_lib.loads(raw_sett) if isinstance(raw_sett, str) else raw_sett
        for cl in sett.get("clients", []):
            email = cl.get("email")
            if email:
                if email not in main_clients:
                    main_clients[email] = dict(cl)
                else:
                    if not main_clients[email].get("auth") and cl.get("auth"):
                        main_clients[email]["auth"] = cl.get("auth")

    logger.info(f"Found {len(main_clients)} unique clients in Main 3x-ui")

    # Sync each RU inbound directly for 100% parity
    updated_total = 0
    created_total = 0
    synced_total = 0

    for inb in ru_inbounds:
        ib_id = inb["id"]
        protocol = inb.get("protocol", "")
        raw_sett = inb.get("settings", "{}")
        sett = json_lib.loads(raw_sett) if isinstance(raw_sett, str) else raw_sett
        clients = sett.get("clients", [])
        clients_by_email = {c.get("email"): c for c in clients if c.get("email")}

        inb_updated = False

        # 1. Update existing clients on this inbound
        for c in clients:
            em = c.get("email")
            if em in main_clients:
                mc = main_clients[em]
                m_exp = mc.get("expiryTime", 0)
                m_enable = mc.get("enable", True)
                m_uuid = mc.get("id") or mc.get("uuid")
                m_auth = mc.get("auth") or (m_uuid.replace("-", "") if m_uuid else None)
                m_sub_id = mc.get("subId")

                needs_change = (
                    (m_uuid and c.get("id") != m_uuid) or
                    (m_auth and (c.get("auth") != m_auth or c.get("password") != m_auth)) or
                    (c.get("expiryTime") != m_exp) or
                    (c.get("enable") != m_enable) or
                    (m_sub_id and c.get("subId") != m_sub_id)
                )

                if needs_change:
                    logger.info(f"🔄 [DIFF] Inbound {ib_id} Client {em}: syncing UUID/auth/status")
                    if not dry_run:
                        if m_uuid: c["id"] = m_uuid
                        if m_auth:
                            c["auth"] = m_auth
                            c["password"] = m_auth
                        c["expiryTime"] = m_exp
                        c["enable"] = m_enable
                        if m_sub_id: c["subId"] = m_sub_id
                        inb_updated = True
                    updated_total += 1
                else:
                    synced_total += 1

        # 2. Add missing clients from DE
        for em, mc in main_clients.items():
            if em not in clients_by_email:
                m_uuid = mc.get("id") or mc.get("uuid")
                m_auth = mc.get("auth") or (m_uuid.replace("-", "") if m_uuid else None)
                logger.info(f"➕ [MISSING] Inbound {ib_id} Client {em} missing. Adding...")
                if not dry_run:
                    new_cl = {
                        "id": m_uuid,
                        "email": em,
                        "flow": "xtls-rprx-vision" if protocol == "vless" else "",
                        "limitIp": mc.get("limitIp", 10),
                        "totalGB": mc.get("totalGB", 0),
                        "expiryTime": mc.get("expiryTime", 0),
                        "enable": mc.get("enable", True),
                        "tgId": mc.get("tgId", 0),
                        "subId": mc.get("subId") or "",
                        "reset": mc.get("reset", 0),
                        "auth": m_auth,
                        "password": m_auth,
                    }
                    clients.append(new_cl)
                    inb_updated = True
                created_total += 1

        if inb_updated and not dry_run:
            sett["clients"] = clients
            inb["settings"] = json_lib.dumps(sett)
            url = f"{ru_xui.host}/panel/api/inbounds/update/{ib_id}"
            resp = ru_xui._request("POST", url, json=inb)
            logger.info(f"✅ Inbound {ib_id} applied: {resp.json().get('success')}")

    if (created_total > 0 or updated_total > 0) and not dry_run:
        ru_xui._request("POST", f"{ru_xui.host}/panel/api/server/restartXrayService")
        logger.info("🔄 [RU-Node] Relaunched Xray on RU node to apply updated client tables")

    try:
        from scripts.sync_family_routing import sync_ru_portal_routing
        if not dry_run:
            sync_ru_portal_routing()
    except Exception as e:
        logger.warning(f"Failed to sync family routing: {e}")

    logger.info("=" * 50)
    logger.info(f"🎉 Sync finished. Results: Synced in harmony: {synced_total}, Created: {created_total}, Updated: {updated_total}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Sync 3x-ui clients to RU secondary node")
    parser.add_argument("--dry-run", action="store_true", help="Only check differences without making changes")
    args = parser.parse_args()
    sync_all_clients(dry_run=args.dry_run)
