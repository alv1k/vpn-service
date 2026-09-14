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
    main_clients = {}
    for inb in main_inbounds:
        if inb.get("protocol") == "amneziawg" or inb.get("id") == 17:
            continue
        raw_sett = inb.get("settings", "{}")
        import json as json_lib
        sett = json_lib.loads(raw_sett) if isinstance(raw_sett, str) else raw_sett
        for cl in sett.get("clients", []):
            email = cl.get("email")
            if email:
                main_clients[email] = cl
        # Fallback to clientStats if not in settings
        for cl in inb.get("clientStats", []):
            email = cl.get("email")
            if email and email not in main_clients:
                main_clients[email] = cl

    logger.info(f"Found {len(main_clients)} unique clients in Main 3x-ui")

    # Collect all clients from RU 3x-ui
    ru_clients = {}
    for inb in ru_inbounds:
        for cl in inb.get("clientStats", []) or inb.get("settings", {}).get("clients", []):
            email = cl.get("email")
            if email and email not in ru_clients:
                ru_clients[email] = cl

    logger.info(f"Found {len(ru_clients)} unique clients in RU 3x-ui")

    created = 0
    updated = 0
    synced = 0

    for email, m_cl in main_clients.items():
        m_exp = m_cl.get("expiryTime", 0)
        m_enable = m_cl.get("enable", True)
        m_uuid = m_cl.get("id") or m_cl.get("uuid")
        m_sub_id = m_cl.get("subId")
        m_tg_id = m_cl.get("tgId", 0)
        m_limit_ip = m_cl.get("limitIp", 10)

        ru_cl = ru_clients.get(email)

        if not ru_cl:
            logger.info(f"➕ [MISSING] Client {email} missing on RU node. Needs creation (expiry={m_exp}, enable={m_enable})")
            if not dry_run:
                ok = sync_client_to_ru_node(
                    email=email,
                    expiry_ms=m_exp,
                    enable=m_enable,
                    uuid_str=m_uuid,
                    sub_id=m_sub_id,
                    tg_id=m_tg_id,
                    limit_ip=m_limit_ip
                )
                if ok:
                    created += 1
        else:
            ru_exp = ru_cl.get("expiryTime", 0)
            ru_enable = ru_cl.get("enable", True)

            # Check if sync needed
            needs_update = (ru_exp != m_exp) or (ru_enable != m_enable)
            if needs_update:
                logger.info(f"🔄 [DIFF] Client {email}: Main(exp={m_exp}, en={m_enable}) vs RU(exp={ru_exp}, en={ru_enable})")
                if not dry_run:
                    ok = sync_client_to_ru_node(
                        email=email,
                        expiry_ms=m_exp,
                        enable=m_enable,
                        tg_id=m_tg_id,
                        limit_ip=m_limit_ip
                    )
                    if ok:
                        updated += 1
            else:
                synced += 1

    logger.info("=" * 50)
    logger.info(f"🎉 Sync finished. Results: Synced in harmony: {synced}, Created: {created}, Updated: {updated}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Sync 3x-ui clients to RU secondary node")
    parser.add_argument("--dry-run", action="store_true", help="Only check differences without making changes")
    args = parser.parse_args()
    sync_all_clients(dry_run=args.dry_run)
