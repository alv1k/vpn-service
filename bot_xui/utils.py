import requests
import json
import subprocess
from urllib.parse import quote
import os
import logging
import httpx
from config import XUI_HOST, XUI_USERNAME, XUI_PASSWORD, VLESS_DOMAIN, VLESS_PORT, VLESS_PATH, TELEGRAM_BOT_TOKEN, VLESS_SID, VLESS_PBK, VLESS_SNI


TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"

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

    def get_client_by_tg_id(self, tg_id):
        """Найти клиента по tg_id среди всех inbound'ов"""
        try:
            response = self.session.get(f"{self.host}/panel/api/inbounds/list")
            result = response.json()
            
            if not result.get('success'):
                return None
            
            for inbound in result.get('obj', []):
                settings = json.loads(inbound.get('settings', '{}'))
                for client in settings.get('clients', []):
                    if str(client.get('tgId')) == str(tg_id) and 'tg_' in client.get('email', ''):
                        return {
                            'client': client,
                            'inbound_id': inbound['id']
                        }
            return None
            
        except Exception as e:
            logger.error(f"Error searching client by tg_id: {e}")
            return None


    def extend_client_expiry(self, inbound_id, client, extra_ms):
        """Продлить срок действия клиента"""
        try:
            import time
            now_ms = int(time.time() * 1000)
            current_expiry = client.get('expiryTime', 0)

            # Защита от испорченных значений (больше 10 лет от сейчас — явный мусор)
            max_reasonable = now_ms + 10 * 365 * 24 * 60 * 60 * 1000
            if current_expiry > max_reasonable:
                logger.warning(f"Suspicious expiryTime {current_expiry}, resetting to now")
                current_expiry = now_ms

            base = current_expiry if current_expiry > now_ms else now_ms
            duration = extra_ms - now_ms
            new_expiry = base + duration

            logger.info(f"duration: {duration}, new_expiry: {new_expiry}")

            updated_client = {**client, 'expiryTime': new_expiry}

            payload = {
                "id": inbound_id,
                "settings": json.dumps({"clients": [updated_client]})
            }

            response = self.session.post(
                f"{self.host}/panel/api/inbounds/updateClient/{client['id']}",
                json=payload,
                headers={"Content-Type": "application/json"}
            )

            result = response.json()
            logger.info(f"Extend expiry response: {result}")
            return result.get('success', False)

        except Exception as e:
            logger.error(f"Error extending client expiry: {e}", exc_info=True)
            return False


    def add_or_extend_client(self, inbound_id, email, tg_id, uuid, expiry_time=0, total_gb=0, limit_ip=10, extend_ms=None):
        """
        Добавить клиента или продлить срок, если клиент с tg_id уже существует.
        extend_ms — на сколько миллисекунд продлить (по умолчанию = expiry_time)
        """
        existing = self.get_client_by_tg_id(tg_id)
        
        print('🔔🔔🔔 existing', existing)
        if existing and 'tg_' in existing['client'].get('email', ''):
            logger.info(f"Client with tg_id={tg_id} already exists, extending expiry")
            duration = extend_ms if extend_ms is not None else expiry_time
            return self.extend_client_expiry(
                existing['inbound_id'],
                existing['client'],
                duration
            )
        
        logger.info(f"Client with tg_id={tg_id} not found, creating new")
        return self.add_client(inbound_id, email, tg_id, uuid, expiry_time, total_gb, limit_ip)

    def add_client(self, inbound_id, email, tg_id, uuid, expiry_time=0, total_gb=0, limit_ip=10):
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

    def get_client_subscription_url(self, tg_id):
        """Получить ссылку подписки клиента из панели"""
        try:
            # Сначала находим клиента чтобы получить subId
            response = self.session.get(f"{self.host}/panel/api/inbounds/list")
            result = response.json()
            
            if not result.get('success'):
                return None
                
            for inbound in result.get('obj', []):
                settings = json.loads(inbound.get('settings', '{}'))
                for client in settings.get('clients', []):
                    if client.get('tgId') == tg_id:
                        sub_id = client.get('subId')
                        print('📤📤', client)
                        # Ссылка подписки
                        sub_url = f"{self.host}/sub/{sub_id}"
                        return sub_url
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
    sni: str,      # например www.yandex.ru
    fp: str = "chrome",
    spx: str = "/"
) -> str:
    from urllib.parse import quote
    
    params = (
        f"type=tcp"
        f"&security=reality"
        f"&pbk={pbk}"
        f"&fp={fp}"
        f"&sni={sni}"
        f"&sid={sid}"
        f"&spx={quote(spx, safe='')}"
    )
    
    return f"vless://{client_id}@{domain}:{port}?{params}#{quote(client_name)}"

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


async def send_telegram_notification(tg_id: int, message: str, buttons: list = None):
    """
    Отправка уведомления в Telegram через HTTP API
    
    Args:
        tg_id: Telegram ID пользователя
        message: Текст сообщения
        buttons: Список кнопок (опционально)
    """
    if not tg_id:
        return

    data = {
        "chat_id": tg_id,
        "text": message,
        "parse_mode": "HTML"
    }
    
    # Добавляем кнопки если они есть
    if buttons:
        keyboard = {
            "inline_keyboard": buttons
        }
        data["reply_markup"] = json.dumps(keyboard)

    async with httpx.AsyncClient(timeout=5) as client:
        try:
            response = await client.post(TELEGRAM_API, data=data)
            
            if response.status_code == 200:
                logger.info(f"📨 Notification sent to user: {tg_id}")
            else:
                logger.warning(f"⚠️ Telegram API returned {response.status_code}")
                
        except Exception as e:
            logger.error(f"❌ Failed to send Telegram notification: {e}")