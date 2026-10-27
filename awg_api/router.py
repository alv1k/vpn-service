"""AWG WireGuard client management router."""
import logging
from datetime import datetime
from io import BytesIO

import qrcode
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import PlainTextResponse

from awg_api import db, awg_manager
from admin.auth import _require_admin_session

logger = logging.getLogger("awg_router")

router = APIRouter(prefix="/api/wireguard", dependencies=[Depends(_require_admin_session)])


def _client_to_json(c: dict) -> dict:
    """Convert DB client row to wg-easy compatible JSON format."""
    created = c.get("created_at")
    updated = c.get("updated_at")
    if isinstance(created, datetime):
        created = created.strftime("%Y-%m-%dT%H:%M:%S.000Z")
    if isinstance(updated, datetime):
        updated = updated.strftime("%Y-%m-%dT%H:%M:%S.000Z")

    return {
        "id": c["id"],
        "name": c["name"],
        "address": c["address"],
        "publicKey": c["public_key"],
        "enabled": bool(c.get("enabled", True)),
        "createdAt": created or "",
        "updatedAt": updated or "",
        "persistentKeepalive": str(awg_manager.KEEPALIVE),
    }


@router.post("/client")
async def create_client(request: Request):
    body = await request.json()
    name = body.get("name", "unnamed")

    priv, pub = awg_manager.generate_keypair()
    psk = awg_manager.generate_preshared_key()
    address = db.next_free_address()

    client = db.create_client(name, address, priv, pub, psk)

    # Regenerate server conf and reload
    awg_manager.write_server_conf()
    if awg_manager.is_interface_up():
        awg_manager.reload_interface()

    return _client_to_json(client)


@router.get("/client")
async def list_clients():
    clients = db.list_clients()
    return [_client_to_json(c) for c in clients]


@router.get("/client/{client_id}/configuration")
async def get_client_config(client_id: str):
    conf = awg_manager.generate_client_conf(client_id)
    if not conf:
        raise HTTPException(status_code=404, detail="Client not found")
    return PlainTextResponse(conf)


@router.get("/client/{client_id}/qrcode.svg")
async def get_client_qr(client_id: str):
    conf = awg_manager.generate_client_conf(client_id)
    if not conf:
        raise HTTPException(status_code=404, detail="Client not found")

    qr = qrcode.QRCode(version=1, box_size=10, border=4)
    qr.add_data(conf)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    bio = BytesIO()
    img.save(bio, "PNG")
    bio.seek(0)
    return Response(content=bio.getvalue(), media_type="image/png")


@router.delete("/client/{client_id}")
async def delete_client(client_id: str):
    if not db.delete_client(client_id):
        raise HTTPException(status_code=404, detail="Client not found")

    awg_manager.write_server_conf()
    if awg_manager.is_interface_up():
        awg_manager.reload_interface()

    return Response(status_code=204)


@router.post("/client/{client_id}/enable")
async def enable_client(client_id: str):
    if not db.update_client_enabled(client_id, True):
        raise HTTPException(status_code=404)

    awg_manager.write_server_conf()
    if awg_manager.is_interface_up():
        awg_manager.reload_interface()

    return Response(status_code=204)


@router.post("/client/{client_id}/disable")
async def disable_client(client_id: str):
    if not db.update_client_enabled(client_id, False):
        raise HTTPException(status_code=404)

    awg_manager.write_server_conf()
    if awg_manager.is_interface_up():
        awg_manager.reload_interface()

    return Response(status_code=204)


@router.put("/client/{client_id}/name")
async def update_name(client_id: str, request: Request):
    body = await request.json()
    if not db.update_client_name(client_id, body.get("name", "")):
        raise HTTPException(status_code=404)
    return Response(status_code=204)


@router.put("/client/{client_id}/address")
async def update_address(client_id: str, request: Request):
    body = await request.json()
    new_addr = body.get("address", "")
    if not db.update_client_address(client_id, new_addr):
        raise HTTPException(status_code=404)

    awg_manager.write_server_conf()
    if awg_manager.is_interface_up():
        awg_manager.reload_interface()

    return Response(status_code=204)
