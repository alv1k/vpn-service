# test_xui.py
import asyncio
from bot_xui.utils import XUIClient
from config import XUI_HOST, XUI_USERNAME, XUI_PASSWORD

async def test():
    client = XUIClient(XUI_HOST, XUI_USERNAME, XUI_PASSWORD)
    
    print(f"Testing XUI at {XUI_HOST}")
    
    # Тест логина
    if client.login():
        print("✅ Login successful")
        
        # Тест get inbounds
        inbounds = client.get_inbounds()
        print(f"Inbounds: {inbounds}")
        
        # Тест добавления клиента (опционально)
        # result = await client.add_client(
        #     inbound_id=1,
        #     email=f"test_{int(asyncio.get_event_loop().time())}@test.com",
        #     uuid="test-uuid-123",
        #     expiry_time=0
        # )
        # print(f"Add client: {result}")
    else:
        print("❌ Login failed")

if __name__ == "__main__":
    asyncio.run(test())