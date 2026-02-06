import asyncio
from config import AMNEZIA_WG_API_URL, AMNEZIA_WG_API_PASSWORD
from api.wireguard import AmneziaWGClient

def generate_vpn_config(tg_id: int) -> str:
    """
    ⚠️ УСТАРЕЛО: Эта функция больше не используется напрямую
    Генерирует VPN конфиг для пользователя через AmneziaWG API
    """
    # Создаем клиента AmneziaWG
    wg_client = AmneziaWGClient(api_url=AMNEZIA_WG_API_URL, password=AMNEZIA_WG_API_PASSWORD)

    # Создаем имя клиента
    client_name = f"tg_{tg_id}_manual"

    # Создаем клиента в AmneziaWG
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    async def create_and_get_config():
        client_data = await wg_client.create_client(name=client_name)
        client_id = client_data['id']
        return await wg_client.get_client_config(client_id=client_id)

    config = loop.run_until_complete(create_and_get_config())
    loop.close()

    return config
