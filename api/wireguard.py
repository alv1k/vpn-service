import aiohttp
import json
import logging
from typing import Optional, Dict

logger = logging.getLogger(__name__)

class AmneziaWGClient:
    def __init__(self, api_url: str, password: str):
        """
        Инициализация клиента для AmneziaWG Web UI API
        
        Args:
            api_url: URL веб-интерфейса (http://localhost:51821)
            password: Пароль из WG_UI_PASSWORD
        """
        self.api_url = api_url.rstrip('/')
        self.password = password
        self.session_cookie = None
    
    async def _ensure_logged_in(self):
        """Проверка авторизации и логин при необходимости"""
        if not self.session_cookie:
            await self.login()
    
    async def login(self) -> bool:
        """
        Авторизация в Web UI
        
        Returns:
            bool: True если успешно
        """
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.api_url}/api/session",
                    json={"password": self.password},
                    timeout=aiohttp.ClientTimeout(total=10)
                ) as response:
                    if response.status == 200:
                        self.session_cookie = response.cookies.get('connect.sid')
                        logger.info("Successfully logged in to AmneziaWG Web UI")
                        return True
                    else:
                        logger.error(f"Login failed: HTTP {response.status}")
                        return False
        except Exception as e:
            logger.error(f"Login error: {e}")
            return False
    
    async def create_client(self, name: str) -> Optional[Dict]:
        """
        ⭐ ЗАМЕНА для add_peer_via_wg_set()
        Создает нового VPN клиента через Web UI API
        
        Args:
            name: Имя клиента (например, "user_123456789_20240204")
        
        Returns:
            Dict с данными клиента:
            {
                'id': 'client_uuid',
                'name': 'user_123456789',
                'enabled': True,
                'address': '10.10.0.2',
                'publicKey': 'base64_public_key',
                'createdAt': '2024-02-04T...',
                'updatedAt': '2024-02-04T...',
                'persistentKeepalive': '25'
            }
        """
        await self._ensure_logged_in()
        
        cookies = {'connect.sid': self.session_cookie.value}
        
        try:
            async with aiohttp.ClientSession(cookies=cookies) as session:
                async with session.post(
                    f"{self.api_url}/api/wireguard/client",
                    json={"name": name},
                    timeout=aiohttp.ClientTimeout(total=30)
                ) as response:
                    if response.status == 200:
                        data = await response.json()
                        logger.info(f"Client created: {name} (IP: {data.get('address')})")
                        return data
                    elif response.status == 500:
                        error_text = await response.text()
                        if "Maximum number of clients reached" in error_text:
                            raise RuntimeError("Maximum number of clients reached. Delete old clients or increase limit.")
                        raise RuntimeError(f"Server error: {error_text}")
                    else:
                        logger.error(f"Failed to create client: HTTP {response.status}")
                        return None
        except aiohttp.ClientError as e:
            logger.error(f"Network error creating client: {e}")
            return None
        except Exception as e:
            logger.error(f"Error creating client: {e}")
            raise
    
    async def get_client_config(self, client_id: str) -> Optional[str]:
        """
        Получает полный конфигурационный файл клиента
        
        Args:
            client_id: ID клиента из create_client()
        
        Returns:
            str: Содержимое .conf файла для импорта в AmneziaVPN
        """
        await self._ensure_logged_in()
        
        cookies = {'connect.sid': self.session_cookie.value}
        
        try:
            async with aiohttp.ClientSession(cookies=cookies) as session:
                async with session.get(
                    f"{self.api_url}/api/wireguard/client/{client_id}/configuration",
                    timeout=aiohttp.ClientTimeout(total=10)
                ) as response:
                    if response.status == 200:
                        config = await response.text()
                        logger.info(f"Config retrieved for client: {client_id}")
                        return config
                    else:
                        logger.error(f"Failed to get config: HTTP {response.status}")
                        return None
        except Exception as e:
            logger.error(f"Error getting config: {e}")
            return None
    
    async def get_client_qr_code(self, client_id: str) -> Optional[bytes]:
        """
        Получает QR-код конфигурации (SVG)
        
        Args:
            client_id: ID клиента
        
        Returns:
            bytes: SVG изображение QR-кода
        """
        await self._ensure_logged_in()
        
        cookies = {'connect.sid': self.session_cookie.value}
        
        try:
            async with aiohttp.ClientSession(cookies=cookies) as session:
                async with session.get(
                    f"{self.api_url}/api/wireguard/client/{client_id}/qrcode.svg",
                    timeout=aiohttp.ClientTimeout(total=10)
                ) as response:
                    if response.status == 200:
                        return await response.read()
                    return None
        except Exception as e:
            logger.error(f"Error getting QR code: {e}")
            return None
    
    async def list_clients(self) -> Optional[list]:
        """
        Получает список всех клиентов
        
        Returns:
            List[Dict]: Список всех VPN клиентов
        """
        await self._ensure_logged_in()
        
        cookies = {'connect.sid': self.session_cookie.value}
        
        try:
            async with aiohttp.ClientSession(cookies=cookies) as session:
                async with session.get(
                    f"{self.api_url}/api/wireguard/client",
                    timeout=aiohttp.ClientTimeout(total=10)
                ) as response:
                    if response.status == 200:
                        return await response.json()
                    return None
        except Exception as e:
            logger.error(f"Error listing clients: {e}")
            return None
    
    async def delete_client(self, client_id: str) -> bool:
        """
        Удаляет клиента
        
        Args:
            client_id: ID клиента
        
        Returns:
            bool: True если успешно удален
        """
        await self._ensure_logged_in()
        
        cookies = {'connect.sid': self.session_cookie.value}
        
        try:
            async with aiohttp.ClientSession(cookies=cookies) as session:
                async with session.delete(
                    f"{self.api_url}/api/wireguard/client/{client_id}",
                    timeout=aiohttp.ClientTimeout(total=10)
                ) as response:
                    success = response.status == 204
                    if success:
                        logger.info(f"Client deleted: {client_id}")
                    return success
        except Exception as e:
            logger.error(f"Error deleting client: {e}")
            return False
    
    async def enable_client(self, client_id: str) -> bool:
        """
        Включает клиента (разрешает подключение)
        
        Args:
            client_id: ID клиента
        
        Returns:
            bool: True если успешно
        """
        await self._ensure_logged_in()
        
        cookies = {'connect.sid': self.session_cookie.value}
        
        try:
            async with aiohttp.ClientSession(cookies=cookies) as session:
                async with session.post(
                    f"{self.api_url}/api/wireguard/client/{client_id}/enable",
                    timeout=aiohttp.ClientTimeout(total=10)
                ) as response:
                    success = response.status == 204
                    if success:
                        logger.info(f"Client enabled: {client_id}")
                    return success
        except Exception as e:
            logger.error(f"Error enabling client: {e}")
            return False
    
    async def disable_client(self, client_id: str) -> bool:
        """
        Отключает клиента (блокирует подключение)
        
        Args:
            client_id: ID клиента
        
        Returns:
            bool: True если успешно
        """
        await self._ensure_logged_in()
        
        cookies = {'connect.sid': self.session_cookie.value}
        
        try:
            async with aiohttp.ClientSession(cookies=cookies) as session:
                async with session.post(
                    f"{self.api_url}/api/wireguard/client/{client_id}/disable",
                    timeout=aiohttp.ClientTimeout(total=10)
                ) as response:
                    success = response.status == 204
                    if success:
                        logger.info(f"Client disabled: {client_id}")
                    return success
        except Exception as e:
            logger.error(f"Error disabling client: {e}")
            return False
    
    async def update_client_name(self, client_id: str, new_name: str) -> bool:
        """
        Обновляет имя клиента
        
        Args:
            client_id: ID клиента
            new_name: Новое имя
        
        Returns:
            bool: True если успешно
        """
        await self._ensure_logged_in()
        
        cookies = {'connect.sid': self.session_cookie.value}
        
        try:
            async with aiohttp.ClientSession(cookies=cookies) as session:
                async with session.put(
                    f"{self.api_url}/api/wireguard/client/{client_id}/name",
                    json={"name": new_name},
                    timeout=aiohttp.ClientTimeout(total=10)
                ) as response:
                    success = response.status == 204
                    if success:
                        logger.info(f"Client renamed: {client_id} -> {new_name}")
                    return success
        except Exception as e:
            logger.error(f"Error updating client name: {e}")
            return False
    
    async def update_client_address(self, client_id: str, new_address: str) -> bool:
        """
        Обновляет IP адрес клиента
        
        Args:
            client_id: ID клиента
            new_address: Новый IP (например, "10.10.0.5")
        
        Returns:
            bool: True если успешно
        """
        await self._ensure_logged_in()
        
        cookies = {'connect.sid': self.session_cookie.value}
        
        try:
            async with aiohttp.ClientSession(cookies=cookies) as session:
                async with session.put(
                    f"{self.api_url}/api/wireguard/client/{client_id}/address",
                    json={"address": new_address},
                    timeout=aiohttp.ClientTimeout(total=10)
                ) as response:
                    success = response.status == 204
                    if success:
                        logger.info(f"Client address updated: {client_id} -> {new_address}")
                    return success
        except Exception as e:
            logger.error(f"Error updating client address: {e}")
            return False


# Пример использования (старый код vs новый)
"""
СТАРЫЙ КОД:
-----------
add_peer_via_wg_set(
    interface="wg0",
    client_public_key="base64_key_here",
    client_ip="10.10.0.2"
)


НОВЫЙ КОД:
----------
wg_client = AmneziaWGClient(
    api_url="http://localhost:51821",
    password="vtnfvjhajp03"
)

# Создаем клиента
client_data = await wg_client.create_client(name="user_123456789")

# Получаем конфиг
config = await wg_client.get_client_config(client_data['id'])

# client_data содержит:
# {
#     'id': 'uuid',
#     'address': '10.10.0.2',  # Автоматически назначается
#     'publicKey': 'base64_key',  # Автоматически генерируется
#     ...
# }
"""