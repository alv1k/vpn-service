import requests
import json
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
import subprocess
from urllib.parse import quote
import os
import logging
from config import XUI_HOST, XUI_USERNAME, XUI_PASSWORD, XUI_TOTP_SECRET, VLESS_DOMAIN, VLESS_PORT, VLESS_PATH, VLESS_SID, VLESS_SID_LIST, VLESS_PBK, VLESS_SNI

logger = logging.getLogger(__name__)

class XUIClient:
    def __init__(self, host, username, password):
        self.host = host.rstrip('/')  # Исправлено: host, не url
        self.username = username
        self.password = password
        self.session = requests.Session()
        self.session.verify = False  # Отключаем проверку SSL для локального подключения
        self.cookie_file = '/tmp/xui-cookie.txt'
        self._logged_in = False

    def _get_csrf_token(self) -> str:
        """Get a fresh CSRF token from the panel."""
        csrf_resp = self.session.get(f"{self.host}/csrf-token")
        return csrf_resp.json().get("obj", "")

    def login(self) -> bool:
        """Login to x-ui panel (v3.x with CSRF support)."""
        try:
            csrf_token = self._get_csrf_token()

            response = self.session.post(
                f"{self.host}/login",
                data={"username": self.username, "password": self.password},
                headers={"X-CSRF-Token": csrf_token},
            )

            if response.status_code == 200 and response.json().get("success"):
                logger.info("✅ XUI login successful")
                self._logged_in = True
                self._csrf_token = self._get_csrf_token()
                return True
            logger.warning(f"XUI login failed: {response.text}")
            return False
        except Exception as e:
            logger.error(f"Login error: {e}")
            return False

    def _request(self, method, url, **kwargs):
        """Выполняет запрос, при необходимости делает login/re-login."""
        kwargs.setdefault('timeout', 10)
        if not self._logged_in:
            self.login()

        if method.upper() == "POST":
            headers = kwargs.get("headers", {})
            if "X-CSRF-Token" not in headers:
                if not getattr(self, "_csrf_token", ""):
                    self._csrf_token = self._get_csrf_token()
                headers["X-CSRF-Token"] = self._csrf_token
                kwargs["headers"] = headers

        response = self.session.request(method, url, **kwargs)

        if response.status_code == 403 and method.upper() == "POST":
            logger.info("XUI CSRF expired, refreshing token and retrying")
            self._csrf_token = self._get_csrf_token()
            headers = kwargs.get("headers", {})
            headers["X-CSRF-Token"] = self._csrf_token
            kwargs["headers"] = headers
            response = self.session.request(method, url, **kwargs)

        content_type = response.headers.get('content-type', '')
        if response.status_code in (401, 404) or (
            'application/json' not in content_type and response.status_code == 200
        ):
            logger.info("XUI session expired, re-logging in")
            self._logged_in = False
            self.login()

            if method.upper() == "POST":
                headers = kwargs.get("headers", {})
                headers["X-CSRF-Token"] = getattr(self, "_csrf_token", "")
                kwargs["headers"] = headers

            response = self.session.request(method, url, **kwargs)

        return response

    def get_inbounds(self):  # Убран async
        """Получить список inbounds"""
        response = self._request("GET", f"{self.host}/panel/api/inbounds/list")
        data = response.json()
        if data.get('success'):
            return data.get('obj', [])
        return []

    def get_vless_reality_inbound_id(self, fallback_id: int = 1) -> int:
        """Find the first VLESS Reality inbound id dynamically."""
        for inbound in self.get_inbounds():
            protocol = inbound.get('protocol', '')
            stream = inbound.get('streamSettings', '{}')
            if protocol == 'vless' and 'reality' in str(stream).lower():
                return inbound['id']
        logger.warning(f"No VLESS Reality inbound found, using fallback={fallback_id}")
        return fallback_id

    def get_client_by_email(self, email):
        """Найти клиента по email через новый API."""
        try:
            response = self._request("GET", f"{self.host}/panel/api/clients/get/{email}")
            result = response.json()
            if result.get('success') and result.get('obj'):
                obj = result['obj']
                client = obj.get('client', {})
                inbound_ids = obj.get('inboundIds', [])
                return {
                    'inbound_id': inbound_ids[0] if inbound_ids else None,
                    'inboundIds': inbound_ids,
                    'client': client,
                }
        except Exception as e:
            logger.error(f"Error getting client by email: {e}")

        inbounds = self.get_inbounds()
        for inbound in inbounds:
            raw_settings = inbound.get('settings', '{}')
            if isinstance(raw_settings, str):
                try:
                    settings = json.loads(raw_settings)
                except (json.JSONDecodeError, TypeError):
                    settings = {}
            else:
                settings = raw_settings if isinstance(raw_settings, dict) else {}
            clients = settings.get('clients', [])
            for client in clients:
                if client.get('email') == email:
                    return {
                        'inbound_id': inbound['id'],
                        'inboundIds': [inbound['id']],
                        'client': client,
                        'inbound': inbound
                    }
        return None

    def get_client_by_tg_id(self, tg_id):
        """Найти клиента по tg_id среди всех inbound'ов"""
        try:
            response = self._request("GET", f"{self.host}/panel/api/inbounds/list")
            result = response.json()

            if not result.get('success'):
                return None

            for inbound in result.get('obj', []):
                raw_settings = inbound.get('settings', '{}')
                if isinstance(raw_settings, str):
                    try:
                        settings = json.loads(raw_settings)
                    except (json.JSONDecodeError, TypeError):
                        settings = {}
                else:
                    settings = raw_settings if isinstance(raw_settings, dict) else {}
                for client in settings.get('clients', []):
                    if str(client.get('tgId')) == str(tg_id) and not client.get('email', '').startswith('test-'):
                        return {
                            'client': client,
                            'inbound_id': inbound['id']
                        }
            return None
            
        except Exception as e:
            logger.error(f"Error searching client by tg_id: {e}")
            return None

    def extend_client_expiry(self, inbound_id, client, duration_ms):
        """Продлить срок действия клиента на duration_ms миллисекунд."""
        try:
            import time
            now_ms = int(time.time() * 1000)
            current_expiry = client.get('expiryTime', 0)

            max_reasonable = now_ms + 10 * 365 * 24 * 60 * 60 * 1000
            if current_expiry > max_reasonable:
                logger.warning(f"Suspicious expiryTime {current_expiry}, resetting to now")
                current_expiry = now_ms

            base = current_expiry if current_expiry > now_ms else now_ms
            new_expiry = base + duration_ms

            logger.info(f"extend_client_expiry: inbound_id={inbound_id}, duration_ms={duration_ms}, current_expiry={current_expiry}, new_expiry={new_expiry}")

            email = client.get('email')
            if not email:
                logger.error(f"extend_client_expiry: Client has no 'email' field, client keys: {list(client.keys())}")
                return False

            logger.info(f"extend_client_expiry: updating client email={email}")

            payload = {
                "email": email,
                "totalGB": client.get('totalGB', 0),
                "expiryTime": new_expiry,
                "tgId": client.get('tgId', 0),
                "enable": client.get('enable', True),
                "limitIp": client.get('limitIp', 0),
                "reset": client.get('reset', 0),
            }

            url = f"{self.host}/panel/api/clients/update/{email}"
            logger.info(f"extend_client_expiry: POST {url}")

            response = self._request("POST", url, json=payload, headers={"Content-Type": "application/json"})

            logger.info(f"extend_client_expiry: response status={response.status_code}")

            result = response.json()
            logger.info(f"extend_client_expiry: response body={result}")
            if result.get('success', False):
                return new_expiry
            logger.error(f"extend_client_expiry: XUI returned success=False: {result}")
            return False

        except Exception as e:
            logger.error(f"extend_client_expiry: exception: {e}", exc_info=True)
            return False

    def add_or_extend_client(self, inbound_id, email, tg_id, uuid, expiry_time=0, total_gb=0, limit_ip=10, extend_ms=None):
        """Добавить клиента или продлить срок"""
        import time
        existing = self.get_client_by_tg_id(tg_id)

        logger.debug(f"existing client: {existing}")
        if existing and not existing['client'].get('email', '').startswith('test-'):
            client_id = existing['client'].get('id')
            logger.info(f"Client with tg_id={tg_id} already exists, extending expiry (client_id={client_id})")
            if extend_ms is not None:
                duration_ms = extend_ms
            else:
                now_ms = int(time.time() * 1000)
                duration_ms = expiry_time - now_ms
            return self.extend_client_expiry(
                existing['inbound_id'],
                existing['client'],
                duration_ms
            )
        
        logger.info(f"Client with tg_id={tg_id} not found, creating new")
        return self.add_client(inbound_id, email, tg_id, uuid, expiry_time, total_gb, limit_ip)

    def add_client(self, inbound_id, email, tg_id, uuid, expiry_time=0, total_gb=0, limit_ip=10, sub_id=None):
        """Добавить клиента в inbound (legacy-обёртка для совместимости)."""
        return self.create_client(email=email, tg_id=tg_id, expiry_time=expiry_time, total_gb=total_gb, limit_ip=limit_ip, inbound_ids=[inbound_id])

    def create_client(self, email, tg_id, expiry_time=0, total_gb=0, limit_ip=10, inbound_ids=None):
        """
        Создать клиента через новый API 3x-ui (v3.x+).
        POST /panel/api/clients/add
        Body: {"client": {...}, "inboundIds": [id1, id2]}
        Сервер сам генерирует UUID/auth и subId.
        Возвращает dict с полями: success, subId, uuid, auth, msg
        """
        import uuid as uuid_lib

        if inbound_ids is None:
            inbound_ids = []

        payload = {
            "client": {
                "email": email,
                "limitIp": limit_ip,
                "totalGB": total_gb,
                "expiryTime": expiry_time,
                "enable": True,
                "tgId": tg_id,
                "reset": 0,
            },
            "inboundIds": inbound_ids,
        }

        try:
            logger.info(f"Sending createClient request to: {self.host}/panel/api/clients/add for email={email}, inboundIds={inbound_ids}")

            response = self._request(
                "POST",
                f"{self.host}/panel/api/clients/add",
                json=payload,
                headers={"Content-Type": "application/json"}
            )

            result = response.json()
            logger.info(f"createClient Response: {result}")

            if result.get('success', False):
                client_data = self.get_client_by_email(email)
                if client_data:
                    c = client_data['client']
                    return {
                        "success": True,
                        "subId": c.get('subId') or str(uuid_lib.uuid4()).replace('-', '')[:16],
                        "uuid": c.get('uuid') or c.get('id'),
                        "auth": c.get('auth'),
                        "inboundIds": client_data.get('inboundIds', inbound_ids),
                    }
                sub_id = str(uuid_lib.uuid4()).replace('-', '')[:16]
                return {"success": True, "subId": sub_id, "uuid": None, "auth": None}
            return {"success": False, "msg": result.get('msg', 'Unknown error')}

        except Exception as e:
            logger.error(f"Error creating client: {e}")
            return {"success": False, "msg": str(e)}

    def get_hysteria_inbound_id(self, fallback_id: int = 4) -> int:
        """Find the first Hysteria inbound id dynamically."""
        for inbound in self.get_inbounds():
            protocol = inbound.get('protocol', '')
            if protocol in ('hysteria', 'hysteria2'):
                return inbound['id']
        logger.warning(f"No Hysteria inbound found, using fallback={fallback_id}")
        return fallback_id

    def deactivate_client(self, inbound_id, client):
        """Отключить клиента (enable=false)"""
        email = client.get('email')
        if not email:
            return False
        payload = {
            "email": email,
            "totalGB": client.get('totalGB', 0),
            "expiryTime": client.get('expiryTime', 0),
            "tgId": client.get('tgId', 0),
            "enable": False,
            "limitIp": client.get('limitIp', 0),
            "reset": client.get('reset', 0),
        }
        try:
            response = self._request(
                "POST",
                f"{self.host}/panel/api/clients/update/{email}",
                json=payload,
                headers={"Content-Type": "application/json"}
            )
            return response.json().get('success', False)
        except Exception as e:
            logger.error(f"Error deactivating client: {e}")
            return False

    def delete_client(self, inbound_id, client_email):
        """Удалить клиента по email"""
        try:
            response = self._request(
                "POST",
                f"{self.host}/panel/api/clients/del/{client_email}",
                headers={"Content-Type": "application/json"}
            )
            return response.json().get('success', False)
        except Exception as e:
            logger.error(f"Error deleting client: {e}")
            return False

    def reset_client_traffic(self, inbound_id, client_email):
        """Сбросить трафик клиента"""
        try:
            response = self._request(
                "POST",
                f"{self.host}/panel/api/clients/resetTraffic/{client_email}",
                headers={"Content-Type": "application/json"}
            )
            return response.json().get('success', False)
        except Exception as e:
            logger.error(f"Error resetting client traffic: {e}")
            return False

    def get_client_subscription_url(self, tg_id):
        """Получить ссылку подписки клиента"""
        from config import XUI_SUB_PATH
        if not XUI_SUB_PATH:
            logger.warning("XUI_SUB_PATH not configured")
            return None
        try:
            import time
            now_ms = int(time.time() * 1000)
            response = self._request("GET", f"{self.host}/panel/api/inbounds/list")
            result = response.json()

            if not result.get('success'):
                return None

            best_sub_id = None
            best_expiry = -1
            for inbound in result.get('obj', []):
                raw_settings = inbound.get('settings', '{}')
                if isinstance(raw_settings, str):
                    try:
                        settings = json.loads(raw_settings)
                    except (json.JSONDecodeError, TypeError):
                        settings = {}
                else:
                    settings = raw_settings if isinstance(raw_settings, dict) else {}
                for client in settings.get('clients', []):
                    if str(client.get('tgId')) == str(tg_id):
                        sub_id = client.get('subId')
                        if not sub_id:
                            continue
                        expiry = client.get('expiryTime', 0)
                        if expiry == 0 or expiry > now_ms:
                            if expiry == 0 or expiry > best_expiry:
                                best_sub_id = sub_id
                                best_expiry = expiry
            if best_sub_id:
                return f"{XUI_SUB_PATH}/sub/{best_sub_id}"
            return None

        except Exception as e:
            logger.error(f"Error getting client subscription url: {e}")
            return None


def generate_vless_link(
    client_id: str,
    domain: str,
    port: int,
    path: str,
    client_name: str,
    pbk: str,      # public key от xray x25519
    sid: str,      # short id
    sni: str,      # например www.samsung.com
    fp: str = "chrome",
    spx: str = "/",
    remark: str | None = None,
) -> str:
    import random
    from urllib.parse import quote

    # Ротация short ID: если передан один из списка, выбираем случайный
    if VLESS_SID_LIST and sid in VLESS_SID_LIST:
        sid = random.choice(VLESS_SID_LIST)

    # Формируем параметры в том же порядке, что и в панели
    params = (
        f"type=tcp"
        f"&encryption=none"
        f"&security=reality"
        f"&pbk={pbk}"
        f"&fp={fp}"
        f"&sni={sni}"
        f"&sid={sid}"
        f"&spx={quote(spx, safe='')}"
        f"&flow=xtls-rprx-vision"
    )

    display_name = remark if remark else client_name
    return f"vless://{client_id}@{domain}:{port}?{params}#{quote(display_name)}"

def generate_hysteria2_link(
    auth: str, # Password
    domain: str,
    port: int,
    client_name: str,
    sni: str,
    insecure: int = 0,
) -> str:
    from urllib.parse import quote
    remark = client_name if client_name else "TIIN VPN"
    return f"hysteria2://{auth}@{domain}:{port}?sni={sni}&insecure={insecure}#{quote(remark)}"

def get_amneziawg_config(client_email):
    """Получить конфиг AmneziaWG (native AWG 2.0)"""
    try:
        conf_path = "/etc/amnezia/amneziawg/awg0.conf"
        with open(conf_path) as f:
            return f.read()
    except Exception as e:
        logger.error(f"Error getting AWG config: {e}")
    return None

def format_bytes(bytes_value):
    """Форматирование байтов в читаемый вид"""
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if bytes_value < 1024.0:
            return f"{bytes_value:.2f} {unit}"
        bytes_value /= 1024.0
    return f"{bytes_value:.2f} PB"


