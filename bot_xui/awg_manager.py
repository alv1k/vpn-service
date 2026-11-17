"""
Модуль управления клиентами AmneziaWG в 3x-ui (Inbound 17).
Обеспечивает генерацию x25519 ключей, выделение IP и сборку .conf файлов.
"""
import os
import json
import uuid
import time
import base64
import sqlite3
import logging
from cryptography.hazmat.primitives.asymmetric import x25519
from cryptography.hazmat.primitives import serialization

from config import (
    AWG_INBOUND_ID, AWG_PORT, AWG_ENDPOINT,
    AWG_MTU, AWG_ALLOWED_IPS
)

logger = logging.getLogger(__name__)
DB_PATH = '/etc/x-ui/x-ui.db'


def generate_keypair() -> tuple[str, str]:
    """Генерирует пару ключей x25519 (base64) для AmneziaWG."""
    priv_key = x25519.X25519PrivateKey.generate()
    priv_raw = priv_key.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption()
    )
    pub_raw = priv_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw
    )
    priv_b64 = base64.b64encode(priv_raw).decode()
    pub_b64 = base64.b64encode(pub_raw).decode()
    return priv_b64, pub_b64


def build_awg_conf(client: dict, server: dict, port: int) -> str:
    """Формирует текстовый .conf файл для клиента с MTU=1280 и AllowedIPs (29 CIDR)."""
    jc = server.get('jc', 5)
    jmin = server.get('jmin', 50)
    jmax = server.get('jmax', 1000)
    s1 = server.get('s1', 50)
    s2 = server.get('s2', 124)
    h1 = server.get('h1', '536125')
    h2 = server.get('h2', '7458971')
    h3 = server.get('h3', '69317959')
    h4 = server.get('h4', '672102228')
    server_pub = server.get('publicKey', '')
    client_ip = client.get('allowedIPs', ['10.8.1.2/32'])[0]
    client_priv = client.get('privateKey', '')

    endpoint = AWG_ENDPOINT if ":" in AWG_ENDPOINT else f"{AWG_ENDPOINT}:{port}"

    conf = f"""[Interface]
Address = {client_ip}
PrivateKey = {client_priv}
DNS = 8.8.8.8, 1.1.1.1
MTU = {AWG_MTU}
Jc = {jc}
Jmin = {jmin}
Jmax = {jmax}
S1 = {s1}
S2 = {s2}
H1 = {h1}
H2 = {h2}
H3 = {h3}
H4 = {h4}

[Peer]
PublicKey = {server_pub}
Endpoint = {endpoint}
AllowedIPs = {AWG_ALLOWED_IPS}
PersistentKeepalive = 25"""
    return conf


def get_or_create_3xui_awg_client(tg_id: int, expiry_ms: int = 0, client_name: str = None) -> dict:
    """
    Находит или создаёт клиента AmneziaWG в инбаунде 17 в базе 3x-ui.
    Возвращает dict с полями:
        client_name, client_id, client_ip, config, private_key, public_key
    """
    if not os.path.exists(DB_PATH):
        raise RuntimeError(f"Database {DB_PATH} not found")

    email = f"tiin_{tg_id}"
    if client_name is None:
        client_name = f"awg_{tg_id}"

    with sqlite3.connect(DB_PATH, timeout=10) as conn:
        c = conn.cursor()
        c.execute("SELECT settings, port FROM inbounds WHERE id = ?", (AWG_INBOUND_ID,))
        row = c.fetchone()
        if not row:
            raise RuntimeError(f"Inbound {AWG_INBOUND_ID} not found in {DB_PATH}")

        raw_settings, port = row
        settings = json.loads(raw_settings) if isinstance(raw_settings, str) else raw_settings
        server = settings.get("server", {})
        clients = settings.get("clients", [])

        # Проверяем, есть ли уже клиент
        existing_client = None
        used_ips = set()
        for cl in clients:
            for ip_cidr in cl.get("allowedIPs", []):
                used_ips.add(ip_cidr.split('/')[0])
            if str(cl.get("tgId")) == str(tg_id) or cl.get("email") == email:
                existing_client = cl

        now_ms = int(time.time() * 1000)

        if existing_client:
            # Обновляем срок действия, если передан
            if expiry_ms > 0:
                existing_client["expiryTime"] = expiry_ms
                existing_client["enable"] = True
                c.execute("UPDATE inbounds SET settings = ? WHERE id = ?", (json.dumps(settings), AWG_INBOUND_ID))

            # Синхронизируем с таблицей clients и client_inbounds (3x-ui v3 requirement)
            c.execute("SELECT id, wg_private_key, wg_public_key, wg_allowed_ips FROM clients WHERE email = ?", (email,))
            crow = c.fetchone()
            if crow:
                cid, cur_priv, cur_pub, cur_ips = crow
                upd_fields = ["enable = 1", "updated_at = ?"]
                params = [now_ms]
                if expiry_ms > 0:
                    upd_fields.append("expiry_time = ?")
                    params.append(expiry_ms)
                if not cur_priv and existing_client.get("privateKey"):
                    upd_fields.append("wg_private_key = ?")
                    params.append(existing_client.get("privateKey"))
                if not cur_pub and existing_client.get("publicKey"):
                    upd_fields.append("wg_public_key = ?")
                    params.append(existing_client.get("publicKey"))
                if not cur_ips and existing_client.get("allowedIPs"):
                    upd_fields.append("wg_allowed_ips = ?")
                    params.append(existing_client.get("allowedIPs")[0])
                params.append(cid)
                c.execute(f"UPDATE clients SET {', '.join(upd_fields)} WHERE id = ?", tuple(params))
                client_db_id = cid
            else:
                client_uuid = existing_client.get("id") or str(uuid.uuid4())
                c.execute(
                    """INSERT INTO clients (
                        email, sub_id, uuid, password, auth, flow, limit_ip, total_gb,
                        expiry_time, enable, tg_id, comment, reset, created_at, updated_at,
                        wg_private_key, wg_public_key, wg_allowed_ips
                    ) VALUES (?, ?, ?, ?, ?, '', 0, 0, ?, 1, ?, '', 0, ?, ?, ?, ?, ?)""",
                    (
                        email, str(uuid.uuid4()), client_uuid, client_uuid, client_uuid,
                        expiry_ms or existing_client.get("expiryTime", 0), tg_id, now_ms, now_ms,
                        existing_client.get("privateKey", ""), existing_client.get("publicKey", ""),
                        existing_client.get("allowedIPs", [""])[0]
                    )
                )
                client_db_id = c.lastrowid

            c.execute(
                "INSERT OR IGNORE INTO client_inbounds (client_id, inbound_id, flow_override, created_at) VALUES (?, ?, '', ?)",
                (client_db_id, AWG_INBOUND_ID, now_ms)
            )

            conn.commit()

            conf_text = build_awg_conf(existing_client, server, port)
            return {
                "client_name": client_name,
                "client_id": existing_client.get("id") or existing_client.get("email"),
                "client_ip": existing_client.get("allowedIPs", [""])[0],
                "config": conf_text,
                "private_key": existing_client.get("privateKey"),
                "public_key": existing_client.get("publicKey"),
            }

        # Выделяем новый IP в подсети 10.8.1.0/24
        new_ip = None
        for i in range(2, 255):
            cand = f"10.8.1.{i}"
            if cand not in used_ips:
                new_ip = f"{cand}/32"
                break

        if not new_ip:
            raise RuntimeError("AmneziaWG subnet 10.8.1.0/24 is full")

        priv_b64, pub_b64 = generate_keypair()
        client_uuid = str(uuid.uuid4())

        new_client = {
            "id": client_uuid,
            "email": email,
            "enable": True,
            "tgId": tg_id,
            "allowedIPs": [new_ip],
            "privateKey": priv_b64,
            "publicKey": pub_b64,
            "totalGB": 0,
            "expiryTime": expiry_ms,
            "comment": "",
            "created_at": now_ms,
            "updated_at": now_ms,
        }

        clients.append(new_client)
        settings["clients"] = clients

        c.execute("UPDATE inbounds SET settings = ? WHERE id = ?", (json.dumps(settings), AWG_INBOUND_ID))

        # Добавляем в client_traffics, если нет
        c.execute("SELECT id FROM client_traffics WHERE email = ?", (email,))
        if not c.fetchone():
            c.execute(
                "INSERT INTO client_traffics (inbound_id, enable, email, up, down, expiry_time, total, reset) "
                "VALUES (?, 1, ?, 0, 0, ?, 0, 0)",
                (AWG_INBOUND_ID, email, expiry_ms)
            )

        # Синхронизируем с таблицей clients и client_inbounds (3x-ui v3 requirement)
        c.execute("SELECT id FROM clients WHERE email = ?", (email,))
        crow = c.fetchone()
        if crow:
            client_db_id = crow[0]
            c.execute(
                """UPDATE clients SET
                    wg_private_key = ?, wg_public_key = ?, wg_allowed_ips = ?,
                    enable = 1, expiry_time = ?, updated_at = ?
                WHERE id = ?""",
                (priv_b64, pub_b64, new_ip, expiry_ms, now_ms, client_db_id)
            )
        else:
            c.execute(
                """INSERT INTO clients (
                    email, sub_id, uuid, password, auth, flow, limit_ip, total_gb,
                    expiry_time, enable, tg_id, comment, reset, created_at, updated_at,
                    wg_private_key, wg_public_key, wg_allowed_ips
                ) VALUES (?, ?, ?, ?, ?, '', 0, 0, ?, 1, ?, '', 0, ?, ?, ?, ?, ?)""",
                (
                    email, str(uuid.uuid4()), client_uuid, client_uuid, client_uuid,
                    expiry_ms, tg_id, now_ms, now_ms,
                    priv_b64, pub_b64, new_ip
                )
            )
            client_db_id = c.lastrowid

        c.execute(
            "INSERT OR IGNORE INTO client_inbounds (client_id, inbound_id, flow_override, created_at) VALUES (?, ?, '', ?)",
            (client_db_id, AWG_INBOUND_ID, now_ms)
        )

        conn.commit()

        conf_text = build_awg_conf(new_client, server, port)
        logger.info(f"✅ Created 3x-ui AWG client {email} (IP: {new_ip})")

        return {
            "client_name": client_name,
            "client_id": client_uuid,
            "client_ip": new_ip,
            "config": conf_text,
            "private_key": priv_b64,
            "public_key": pub_b64,
        }
