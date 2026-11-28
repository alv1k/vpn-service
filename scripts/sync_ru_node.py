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
import json as json_lib

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


def get_direct_or_proxied_ru_client() -> XUIClient | None:
    """Tries direct connection to RU 3x-ui first; falls back to proxied if needed."""
    try:
        direct_client = XUIClient(RU_XUI_HOST, RU_XUI_USERNAME, RU_XUI_PASSWORD, proxy=None)
        if direct_client.login():
            return direct_client
    except Exception as e:
        logger.warning(f"Direct connection to RU 3x-ui failed ({e}), falling back to default multi_node client")
    return get_ru_xui_client()


def sync_all_clients(dry_run: bool = False, restart_xray: bool = False):
    logger.info("🚀 Starting full synchronization with RU Node...")
    main_xui = XUIClient(XUI_HOST, XUI_USERNAME, XUI_PASSWORD)
    ru_xui = get_direct_or_proxied_ru_client()

    if not main_xui.login():
        logger.error("❌ Failed to login to Main 3x-ui")
        return

    if not ru_xui or not ru_xui.login():
        logger.error("❌ Failed to login to RU 3x-ui")
        return

    main_inbounds = main_xui.get_inbounds()
    ru_inbounds = [i for i in ru_xui.get_inbounds() if i.get("id") in RU_ACTIVE_INBOUND_IDS]

    logger.info(f"Main inbounds: {len(main_inbounds)}, RU inbounds: {len(ru_inbounds)}")

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

    # 2. Каноническое состояние клиентов RU. В 3x-ui 3.11 источник истины —
    # нормализованные таблицы clients/client_inbounds (JSON settings инбаундов
    # устаревает), поэтому читаем /panel/api/clients/list с полем inboundIds.
    resp = ru_xui._request("GET", f"{ru_xui.host}/panel/api/clients/list")
    if resp.status_code != 200 or not resp.json().get("success"):
        logger.error(f"❌ Failed to fetch RU clients list: {resp.status_code}")
        return
    ru_clients_global = {c["email"]: c for c in resp.json().get("obj", []) if c.get("email")}

    updated_total = 0
    created_total = 0
    synced_total = 0

    # 3. Synchronize attributes for all existing clients (once globally)
    for em, mc in main_clients.items():
        if em in ru_clients_global:
            rc = ru_clients_global[em]
            m_exp = mc.get("expiryTime", 0)
            m_enable = bool(mc.get("enable", True))
            m_uuid = mc.get("id") or mc.get("uuid")
            m_auth = mc.get("auth") or (m_uuid.replace("-", "") if m_uuid else None)
            m_sub_id = mc.get("subId")

            diffs = {}
            if m_uuid and rc.get("uuid") != m_uuid:
                diffs["uuid"] = (rc.get("uuid"), m_uuid)
            if m_auth and (rc.get("auth") != m_auth or rc.get("password") != m_auth):
                diffs["auth"] = (rc.get("auth"), m_auth)
            if rc.get("expiryTime") != m_exp:
                diffs["expiryTime"] = (rc.get("expiryTime"), m_exp)
            if bool(rc.get("enable")) != m_enable:
                diffs["enable"] = (rc.get("enable"), m_enable)
            if m_sub_id and rc.get("subId") != m_sub_id:
                diffs["subId"] = (rc.get("subId"), m_sub_id)

            if diffs:
                logger.info(f"🔄 [DIFF] Client {em}: " + ", ".join(
                    f"{k}: {old!r} -> {new!r}" for k, (old, new) in diffs.items()))
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
                        resp = ru_xui._request(
                            "POST",
                            f"{ru_xui.host}/panel/api/clients/update/{em}",
                            json=update_payload,
                            headers={"Content-Type": "application/json"}
                        )
                        if resp.status_code == 200 and resp.json().get("success"):
                            rc["uuid"] = m_uuid
                            rc["subId"] = m_sub_id
                            rc["auth"] = m_auth
                            rc["password"] = m_auth
                            rc["expiryTime"] = m_exp
                            rc["enable"] = m_enable
                        else:
                            logger.warning(f"Failed to update client {em}: {resp.status_code} {resp.text[:200]}")
                    except Exception as e:
                        logger.warning(f"Failed to update client {em}: {e}")
                updated_total += 1
            else:
                synced_total += 1

    # 4. Synchronize inbound attachments on RU
    for inb in ru_inbounds:
        ib_id = inb["id"]

        for em, mc in main_clients.items():
            rc = ru_clients_global.get(em)
            if rc and ib_id in (rc.get("inboundIds") or []):
                continue
            m_uuid = mc.get("id") or mc.get("uuid")
            m_auth = mc.get("auth") or (m_uuid.replace("-", "") if m_uuid else None)
            m_sub_id = mc.get("subId")
            logger.info(f"➕ [MISSING] Inbound {ib_id} Client {em} missing. Adding...")

            if not dry_run:
                if rc:
                    # Client exists in 3x-ui clients table, attach to inbound
                    try:
                        resp = ru_xui._request(
                            "POST",
                            f"{ru_xui.host}/panel/api/clients/{em}/attach",
                            json={"inboundIds": [ib_id]},
                            headers={"Content-Type": "application/json"}
                        )
                        if resp.status_code == 200 and resp.json().get("success"):
                            rc.setdefault("inboundIds", []).append(ib_id)
                        else:
                            logger.warning(f"Failed to attach client {em} to inbound {ib_id}: {resp.status_code} {resp.text[:200]}")
                    except Exception as e:
                        logger.warning(f"Failed to attach client {em} to inbound {ib_id}: {e}")
                else:
                    # Client does not exist globally, create via clients/add
                    new_cl = {
                        "id": m_uuid,
                        "email": em,
                        "flow": "",
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
                        resp = ru_xui._request(
                            "POST",
                            f"{ru_xui.host}/panel/api/clients/add",
                            json={"client": new_cl, "inboundIds": [ib_id]},
                            headers={"Content-Type": "application/json"}
                        )
                        if resp.status_code == 200 and resp.json().get("success"):
                            new_cl["uuid"] = new_cl.pop("id")
                            new_cl["inboundIds"] = [ib_id]
                            ru_clients_global[em] = new_cl
                        else:
                            logger.warning(f"Failed to create client {em} on inbound {ib_id}: {resp.status_code} {resp.text[:200]}")
                    except Exception as e:
                        logger.warning(f"Failed to create client {em} on inbound {ib_id}: {e}")
                created_total += 1

    if (created_total > 0 or updated_total > 0) and not dry_run:
        if restart_xray:
            try:
                ru_xui._request("POST", f"{ru_xui.host}/panel/api/server/restartXrayService")
                logger.info("🔄 [RU-Node] Relaunched Xray on RU node (--restart-xray)")
            except Exception as e:
                logger.info(f"🔄 [RU-Node] Relaunched Xray on RU node (socket closed as expected during Xray core reload: {e})")
        else:
            logger.info("ℹ️ [RU-Node] Changes applied via 3x-ui API (hot-apply), Xray restart skipped")

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
    parser.add_argument("--restart-xray", action="store_true",
                        help="Force Xray restart after changes (NOT needed for client add/update — 3.11 hot-applies via API; drops all live connections)")
    args = parser.parse_args()
    sync_all_clients(dry_run=args.dry_run, restart_xray=args.restart_xray)
