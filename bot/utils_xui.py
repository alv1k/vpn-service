import requests
import json
import subprocess
from urllib.parse import quote
import os

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

def generate_vless_link(uuid, domain, port, path, email="User"):
    """Генерация VLESS ссылки для WebSocket + TLS"""
    params = {
        "type": "ws",
        "path": path,
        "security": "tls",
        "sni": domain,
        "host": domain,
        "encryption": "none",
        "alpn": "h2,http/1.1"
    }
    
    param_str = "&".join([f"{k}={quote(str(v))}" for k, v in params.items()])
    vless_link = f"vless://{uuid}@{domain}:{port}?{param_str}#{quote(email)}"
    
    return vless_link

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