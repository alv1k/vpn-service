import requests
import json
import subprocess
from urllib.parse import quote
import os
import logging


# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

class XUIClient:
    def __init__(self, host, username, password):
        self.host = host
        self.username = username
        self.password = password
        self.session = requests.Session()
        self.cookie_file = '/tmp/xui-cookie.txt'
        self.login()
    
    def login(self):
        """Авторизация в 3x-ui"""
        response = self.session.post(
            f"{self.host}/login",
            data={
                "username": self.username,
                "password": self.password
            }
        )
        data = response.json()
        if not data.get('success'):
            raise Exception(f"Failed to login: {data.get('msg')}")
    
    def get_inbounds(self):
        """Получить список inbounds"""
        response = self.session.get(f"{self.host}/panel/api/inbounds/list")
        data = response.json()
        if data.get('success'):
            return data.get('obj', [])
        return []
    
    def get_client_by_email(self, email):
        """Найти клиента по email"""
        inbounds = self.get_inbounds()
        for inbound in inbounds:
            settings = json.loads(inbound.get('settings', '{}'))
            clients = settings.get('clients', [])
            for client in clients:
                if client.get('email') == email:
                    return {
                        'inbound_id': inbound['id'],
                        'client': client,
                        'inbound': inbound
                    }
        return None

    def add_client(self, inbound_id, email, tg_id, uuid, expiry_time=0, total_gb=0, limit_ip=2):
        """Добавить клиента в inbound"""
        import uuid as uuid_lib
        import time
        
        # Формируем данные клиента
        client_data = {
            "id": inbound_id,
            "settings": json.dumps({
                "clients": [{
                    "id": uuid,
                    "email": email,
                    "limitIp": limit_ip,
                    "totalGB": total_gb,
                    "expiryTime": expiry_time,
                    "enable": True,
                    "tgId": tg_id,
                    "subId": str(uuid_lib.uuid4()).replace('-', '')[:16],
                    "reset": 0
                }]
            })
        }
        
        try:

            # Логируем запрос
            logger.info(f"Sending addClient request to: {self.host}/panel/api/inbounds/addClient")
            
            response = self.session.post(
                f"{self.host}/panel/api/inbounds/addClient",
                json=client_data,
                headers={"Content-Type": "application/json"}
            )
            
            result = response.json()
    
            # Логируем полный ответ
            logger.info(f"API Response: {result}")
            return result.get('success', False)
            
        except Exception as e:
            print(f"Error adding client: {e}")
            return False

    def delete_client(self, inbound_id, email):
        """Удалить клиента"""
        try:
            response = self.session.post(
                f"{self.host}/panel/api/inbounds/{inbound_id}/delClient/{email}",
            )
            result = response.json()
            return result.get('success', False)
        except Exception as e:
            print(f"Error deleting client: {e}")
            return False

    def reset_client_traffic(self, inbound_id, email):
        """Сбросить трафик клиента"""
        try:
            response = self.session.post(
                f"{self.host}/panel/api/inbounds/{inbound_id}/resetClientTraffic/{email}",
            )
            result = response.json()
            return result.get('success', False)
        except Exception as e:
            print(f"Error resetting traffic: {e}")
            return False

def generate_vless_link(uuid, domain, port, path, email="User"):
    """Генерация VLESS ссылки для WebSocket + TLS"""
    # params = {
    #     "type": "ws",
    #     "path": path,
    #     "security": "tls",
    #     "sni": domain,
    #     "host": domain,
    #     "encryption": "none",
    #     "alpn": "h2,http/1.1"
    # }
    
    # param_str = "&".join([f"{k}={quote(str(v))}" for k, v in params.items()])
    # vless_link = f"vless://{uuid}@{domain}:{port}?{param_str}#{quote(email)}"
    
    # return vless_link
    """
    Генерация VLESS ссылки для клиента.
    """

    from urllib.parse import quote
    
    # Кодируем путь и remark
    encoded_path = quote(path, safe='')
    encoded_remark = quote(email, safe='')
    
    # Формируем полную ссылку с дополнительными параметрами
    vless_link = (
        f"vless://{uuid}@{domain}:{port}"
        f"?type=ws"
        f"&security=tls"
        f"&path={encoded_path}"
        f"&host={domain}"  # Важно для WebSocket
        f"&sni={domain}"   # Server Name Indication для TLS
        f"&fp=chrome"      # Fingerprint (или random/firefox)
        f"&alpn=h2,http/1.1"  # ALPN для TLS
        f"&encryption=none"
        f"#{encoded_remark}"
    )

    return f"vless://{uuid}@{domain}:{port}?type=ws&security=tls&path={path}&encryption=none#{email}"

def get_amneziawg_config(client_email):
    """Получить конфиг AmneziaWG из контейнера"""
    try:
        # Найти конфиг клиента в контейнере
        result = subprocess.run(
            ['docker', 'exec', os.getenv('AMNEZIA_CONTAINER', 'amneziawg'),
             'cat', f'/etc/amnezia/amneziawg/wg0.conf'],
            capture_output=True,
            text=True
        )
        
        if result.returncode == 0:
            # Парсим конфиг и находим нужного клиента
            # Это упрощенная версия - нужно будет доработать
            return result.stdout
        
    except Exception as e:
        print(f"Error getting AWG config: {e}")
    
    return None

def format_bytes(bytes_value):
    """Форматирование байтов в читаемый вид"""
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if bytes_value < 1024.0:
            return f"{bytes_value:.2f} {unit}"
        bytes_value /= 1024.0
    return f"{bytes_value:.2f} PB"


