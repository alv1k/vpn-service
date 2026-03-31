import aiohttp
import json
import logging
from typing import Optional, Dict

logger = logging.getLogger(__name__)

class AmneziaWGClient:
    def __init__(self, api_url: str, password: str):
        self.api_url = api_url.rstrip('/')
        self.password = password
        self.session_cookie = None
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            cookies = {}
            if self.session_cookie:
                cookies['connect.sid'] = self.session_cookie.value
            self._session = aiohttp.ClientSession(
                cookies=cookies,
                timeout=aiohttp.ClientTimeout(total=10),
            )
        return self._session

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

    async def _ensure_logged_in(self):
        if not self.session_cookie:
            await self.login()

    async def login(self) -> bool:
        try:
            # Login needs a fresh session (no old cookies)
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.api_url}/api/session",
                    json={"password": self.password},
                    timeout=aiohttp.ClientTimeout(total=10)
                ) as response:
                    if response.status == 200:
                        self.session_cookie = response.cookies.get('connect.sid')
                        # Reset shared session so it picks up new cookie
                        await self.close()
                        logger.info("Successfully logged in to AmneziaWG Web UI")
                        return True
                    else:
                        logger.error(f"Login failed: HTTP {response.status}")
                        return False
        except Exception as e:
            logger.error(f"Login error: {e}")
            return False

    async def _request(self, method: str, path: str, **kwargs):
        await self._ensure_logged_in()
        session = await self._get_session()
        async with session.request(method, f"{self.api_url}{path}", **kwargs) as response:
            return response.status, await response.read(), response

    async def create_client(self, name: str) -> Optional[Dict]:
        await self._ensure_logged_in()
        session = await self._get_session()

        try:
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
        await self._ensure_logged_in()
        session = await self._get_session()

        try:
            async with session.get(
                f"{self.api_url}/api/wireguard/client/{client_id}/configuration",
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
        await self._ensure_logged_in()
        session = await self._get_session()

        try:
            async with session.get(
                f"{self.api_url}/api/wireguard/client/{client_id}/qrcode.svg",
            ) as response:
                if response.status == 200:
                    return await response.read()
                return None
        except Exception as e:
            logger.error(f"Error getting QR code: {e}")
            return None

    async def list_clients(self) -> Optional[list]:
        await self._ensure_logged_in()
        session = await self._get_session()

        try:
            async with session.get(
                f"{self.api_url}/api/wireguard/client",
            ) as response:
                if response.status == 200:
                    return await response.json()
                return None
        except Exception as e:
            logger.error(f"Error listing clients: {e}")
            return None

    async def delete_client(self, client_id: str) -> bool:
        await self._ensure_logged_in()
        session = await self._get_session()

        try:
            async with session.delete(
                f"{self.api_url}/api/wireguard/client/{client_id}",
            ) as response:
                success = response.status == 204
                if success:
                    logger.info(f"Client deleted: {client_id}")
                return success
        except Exception as e:
            logger.error(f"Error deleting client: {e}")
            return False

    async def enable_client(self, client_id: str) -> bool:
        await self._ensure_logged_in()
        session = await self._get_session()

        try:
            async with session.post(
                f"{self.api_url}/api/wireguard/client/{client_id}/enable",
            ) as response:
                success = response.status == 204
                if success:
                    logger.info(f"Client enabled: {client_id}")
                return success
        except Exception as e:
            logger.error(f"Error enabling client: {e}")
            return False

    async def disable_client(self, client_id: str) -> bool:
        await self._ensure_logged_in()
        session = await self._get_session()

        try:
            async with session.post(
                f"{self.api_url}/api/wireguard/client/{client_id}/disable",
            ) as response:
                success = response.status == 204
                if success:
                    logger.info(f"Client disabled: {client_id}")
                return success
        except Exception as e:
            logger.error(f"Error disabling client: {e}")
            return False

    async def update_client_name(self, client_id: str, new_name: str) -> bool:
        await self._ensure_logged_in()
        session = await self._get_session()

        try:
            async with session.put(
                f"{self.api_url}/api/wireguard/client/{client_id}/name",
                json={"name": new_name},
            ) as response:
                success = response.status == 204
                if success:
                    logger.info(f"Client renamed: {client_id} -> {new_name}")
                return success
        except Exception as e:
            logger.error(f"Error updating client name: {e}")
            return False

    async def update_client_address(self, client_id: str, new_address: str) -> bool:
        await self._ensure_logged_in()
        session = await self._get_session()

        try:
            async with session.put(
                f"{self.api_url}/api/wireguard/client/{client_id}/address",
                json={"address": new_address},
            ) as response:
                success = response.status == 204
                if success:
                    logger.info(f"Client address updated: {client_id} -> {new_address}")
                return success
        except Exception as e:
            logger.error(f"Error updating client address: {e}")
            return False
