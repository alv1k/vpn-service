from fastapi import FastAPI, Request, HTTPException, Response
import json
import sys
import httpx
from ipaddress import ip_address, ip_network
from datetime import datetime
import logging

# Импорты из вашего проекта
from api.subscriptions import activate_subscription
from api.db import (
    update_payment_status, 
    is_payment_processed, 
    get_payment_status, 
    get_payment_by_id, 
    get_or_create_user, 
    create_vpn_key, 
    get_subscription_until
)
from api.wireguard import AmneziaWGClient
from bot.tariffs import TARIFFS
from config import (
    TELEGRAM_BOT_TOKEN, 
    AMNEZIA_WG_API_URL, 
    AMNEZIA_WG_API_PASSWORD
)
from bot.bot import bot

from aiogram.types import BufferedInputFile

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

app = FastAPI()

TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"

# ===== Белые IP ЮKassa =====
YOO_IPS = [
    ip_network("185.71.76.0/27"),
    ip_network("185.71.77.0/27"),
    ip_network("77.75.153.0/25"),
    ip_network("77.75.154.128/25"),
]

logger.info("🔥 WEBHOOK APP STARTED")


def verify_yookassa_ip(request: Request):
    """Проверка IP адреса YooKassa"""
    if not request.client:
        raise HTTPException(status_code=403, detail="No client IP")

    ip = ip_address(request.client.host)
    if not any(ip in net for net in YOO_IPS):
        logger.warning(f"⚠️ Forbidden IP attempt: {request.client.host}")
        raise HTTPException(status_code=403, detail="Forbidden IP")


async def amnezia_login(client: httpx.AsyncClient):
    r = await client.post(
        f"{AMNEZIA_WG_API_URL}/api/session",
        json={"password": AMNEZIA_WG_API_PASSWORD},
        timeout=10
    )
    r.raise_for_status()

async def amnezia_create_client(client: httpx.AsyncClient, name: str):
    client_data = await wg_client.create_client(name="user_123456789")

    r = await client.post(
        f"{AMNEZIA_WG_API_URL}/api/wireguard/client",
        json={"name": name},
        timeout=10
    )
    r.raise_for_status()

async def amnezia_get_client_id(client: httpx.AsyncClient, name: str) -> str:
    r = await client.get(f"{AMNEZIA_WG_API_URL}/api/wireguard/client", timeout=10)
    r.raise_for_status()

    for c in r.json():
        if c.get("name") == name:
            return c["id"]

    raise RuntimeError("Client not found after creation")

async def amnezia_get_config(client: httpx.AsyncClient, client_id: str) -> str:
    r = await client.get(
        f"{AMNEZIA_WG_API_URL}/api/wireguard/client/{client_id}/configuration",
        timeout=10
    )
    r.raise_for_status()
    return r.text

async def process_successful_payment(payment_id: str, payment_data: dict) -> bool:
    """
    ⭐ ОБРАБОТКА УСПЕШНОГО ПЛАТЕЖА ⭐
    
    Выполняет все необходимые действия при успешной оплате:
    1. Активирует подписку в БД
    2. Создает VPN клиента через AmneziaWG API
    3. Получает конфигурацию
    4. Сохраняет данные в БД
    5. Отправляет конфиг пользователю в Telegram
    
    Args:
        payment_id: ID платежа в YooKassa
        payment_data: Данные платежа из БД
        
    Returns:
        bool: True если обработка прошла успешно, False при ошибке
    """
    try:
        logger.info(f"💰 Processing successful payment: {payment_id}")

        tg_id: int = payment_data["tg_id"]
        tariff_key: str = payment_data["tariff"]

        # ===== 1. Активация подписки =====
        activate_subscription(payment_id)
        logger.info("✅ Subscription activated")

        # ===== 2. Получение / создание пользователя =====
        user_id = get_or_create_user(tg_id)
        logger.info(f"👤 User ID: {user_id} (tg_id={tg_id})")

        # ===== 3. Дата окончания подписки =====
        subscription_until = get_subscription_until(tg_id)
        logger.info(f"📅 Subscription until {subscription_until:%d.%m.%Y}")

        # ===== 4. Формирование имени VPN клиента =====
        client_name = f"tg_{tg_id}_{payment_id[:8]}"
        logger.info(f"🔑 VPN client name: {client_name}")

        logger.info(f"AMNEZIA_WG_API_URL: {AMNEZIA_WG_API_URL}")

        # ===== 5. Работа с AmneziaWG =====
        async with httpx.AsyncClient(timeout=15) as client:
            # 5.1 Login
            # r = await client.post(
            #     f"{AMNEZIA_WG_API_URL}/api/session",
            #     json={"password": AMNEZIA_WG_API_PASSWORD},
            # )
            # r.raise_for_status()

            # 5.2 Create client
            # r = await client.post(
            #     f"{AMNEZIA_WG_API_URL}/api/wireguard/client",
            #     # json={"name": client_name},
            #     json={"name": "test555"},
            # )
            # r.raise_for_status()

            
            wg_client = AmneziaWGClient(
                api_url="http://localhost:51821",
                password="your_amnezia_api_password_here"
            )

            # Создаем клиента
            client_data = await wg_client.create_client(name="user_123456789")

            logger.info(f"client_data: {client_data}")


            # 5.3 Получение client_id
            r = await client.get(f"{AMNEZIA_WG_API_URL}/api/wireguard/client")
            r.raise_for_status()

            client_id = None
            client_ip = None
            client_public_key = None

            for c in r.json():
                if c.get("name") == client_name:
                    client_id = c.get("id")
                    client_ip = c.get("address")
                    client_public_key = c.get("publicKey")
                    break

            if not client_id:
                raise RuntimeError("Client ID not found after creation")

            logger.info(f"✅ VPN client created: id={client_id}, ip={client_ip}")

            # 5.4 Получение конфигурации
            r = await client.get(
                f"{AMNEZIA_WG_API_URL}/api/wireguard/client/{client_id}/configuration"
            )
            r.raise_for_status()

            client_config = r.text
            if not client_config:
                raise RuntimeError("Empty client configuration")

        # ===== 6. Сохранение в БД =====
        create_vpn_key(
            user_id=user_id,
            payment_id=payment_id,
            client_id=client_id,
            client_name=client_name,
            client_ip=client_ip,
            client_public_key=client_public_key,
            config=client_config,
            expires_at=subscription_until,
        )

        logger.info("💾 VPN config saved to DB")

        # ===== 7. Отправка в Telegram =====
        tariff_info = TARIFFS.get(tariff_key, {})
        tariff_name = tariff_info.get("name", tariff_key)

        try:
            filename = f"vpn_{tg_id}_{payment_id[:8]}.conf"

            file = BufferedInputFile(
                client_config.encode(),
                filename=filename,
            )

            caption = (
                f"✅ Ваш VPN готов!\n\n"
                f"🔑 Тариф: {tariff_name}\n"
                f"🌐 IP: {client_ip}\n"
                f"📅 Активен до: {subscription_until:%d.%m.%Y}\n\n"
                f"📱 Инструкция:\n"
                f"1. Установите AmneziaVPN\n"
                f"2. Импортируйте файл\n"
                f"3. Подключитесь\n\n"
                f"💬 Поддержка: @your_support"
            )

            await bot.send_document(
                chat_id=tg_id,
                document=file,
                caption=caption,
            )

            logger.info("📤 Config sent to Telegram")

        except Exception:
            logger.exception("⚠️ Failed to send config to Telegram")

        # ===== 8. Идемпотентность =====
        mark_payment_processed(payment_id)
        logger.info(f"🎉 Payment {payment_id} fully processed")

        return True

    except Exception:
        logger.exception(f"❌ Critical error processing payment {payment_id}")
        return False

async def send_telegram_notification(tg_id: int, message: str):
    """
    Отправка уведомления в Telegram через HTTP API
    
    Args:
        tg_id: Telegram ID пользователя
        message: Текст сообщения
    """
    if not tg_id:
        return
    
    async with httpx.AsyncClient(timeout=5) as client:
        try:
            response = await client.post(
                TELEGRAM_API,
                data={"chat_id": tg_id, "text": message}
            )
            
            if response.status_code == 200:
                logger.info(f"📨 Notification sent to user: {tg_id}")
            else:
                logger.warning(f"⚠️ Telegram API returned {response.status_code}")
                
        except Exception as e:
            logger.error(f"❌ Failed to send Telegram notification: {e}")


@app.post("/webhook")
async def yookassa_webhook(request: Request):
    """
    Обработчик webhook от YooKassa
    Вызывается при изменении статуса платежа
    """
    logger.info("🔔 YooKassa webhook received")
    
    # ===== 1. Проверка IP =====
    verify_yookassa_ip(request)
    
    # ===== 2. Парсинг данных =====
    try:
        body = await request.body()
        payload = json.loads(body)
    except json.JSONDecodeError:
        logger.error("❌ Invalid JSON in webhook body")
        return Response(status_code=400)
    
    # ===== 3. Извлечение данных =====
    event = payload.get("event")
    obj = payload.get("object", {})
    
    payment_id = obj.get("id")
    status_raw = obj.get("status")
    metadata = obj.get("metadata", {})
    
    tg_id = metadata.get("tg_id")
    tariff = metadata.get("tariff", "default")
    
    if not payment_id:
        logger.warning("⚠️ No payment_id in webhook")
        return Response(status_code=200)
    
    logger.info(f"📋 Payment ID: {payment_id}, Status: {status_raw}, Event: {event}")
    
    # ===== 4. Проверка существования платежа =====
    current_status = get_payment_status(payment_id)
    if not current_status:
        logger.warning(f"⚠️ Unknown payment_id: {payment_id}")
        return {"status": "ignored"}
    
    # ===== 5. Проверка на дубликат =====
    if current_status in ("paid", "canceled"):
        logger.info(f"🔁 Duplicate webhook ignored: {payment_id} ({current_status})")
        return {"status": "duplicate"}
    
    # ===== 6. Нормализация статуса =====
    if status_raw == "succeeded":
        new_status = "paid"
    elif status_raw in ("canceled", "failed"):
        new_status = "canceled"
    else:
        new_status = "pending"
    
    # ===== 7. Проверка изменения статуса =====
    if new_status == current_status:
        logger.info(f"ℹ️ Status unchanged: {payment_id} ({new_status})")
        return {"status": "no_change"}
    
    # ===== 8. Получение данных платежа =====
    payment_data = get_payment_by_id(payment_id)
    if not payment_data:
        logger.error(f"❌ Payment data not found: {payment_id}")
        return Response(status_code=404)
    
    # ===== 9. ⭐ ОБРАБОТКА УСПЕШНОГО ПЛАТЕЖА ⭐ =====
    if current_status == "pending" and new_status == "paid":
        success = await process_successful_payment(payment_id, payment_data)
        
        if not success:
            logger.error(f"❌ Failed to process payment {payment_id}")
            # Не обновляем статус в БД, чтобы можно было обработать вручную
            # Отправляем уведомление об ошибке
            if tg_id:
                await send_telegram_notification(
                    tg_id,
                    f"⚠️ Возникла ошибка при создании VPN конфига.\n"
                    f"Платёж ID: {payment_id}\n\n"
                    f"Обратитесь в поддержку: @your_support"
                )
            return Response(status_code=200)  # Все равно возвращаем 200
    
    # ===== 10. Проверка идемпотентности =====
    if is_payment_processed(payment_id):
        logger.info(f"🔁 Payment already marked as processed: {payment_id}")
        return Response(status_code=200)
    
    # ===== 11. Обновление статуса в БД =====
    update_payment_status(payment_id, new_status)
    logger.info(f"💾 Payment status updated: {payment_id} -> {new_status}")
    
    # ===== 12. Уведомление пользователя о статусе =====
    if tg_id:
        tariff_info = TARIFFS.get(tariff, {})
        tariff_name = tariff_info.get("name", tariff)
        tariff_desc = tariff_info.get("yookassa_description", "")
        
        if new_status == "paid":
            # Основное уведомление уже отправлено в process_successful_payment
            # Дополнительное уведомление не нужно
            pass
        elif new_status == "canceled":
            message = (
                f"❌ Платёж не прошёл\n\n"
                f"💳 ID платежа: {payment_id}\n"
                f"📦 Тариф: {tariff_name}\n\n"
                f"Попробуйте ещё раз или обратитесь в поддержку."
            )
            await send_telegram_notification(tg_id, message)
        else:
            message = f"⏳ Платёж {payment_id} в обработке ({new_status})"
            await send_telegram_notification(tg_id, message)
    
    logger.info(
        f"✅ Webhook processed | Payment: {payment_id} | "
        f"TG: {tg_id} | Status: {new_status}"
    )
    
    # ===== 13. ВАЖНО: всегда возвращаем 200 =====
    return Response(status_code=200)


@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "service": "vpn-webhook",
        "timestamp": datetime.now().isoformat(),
        "wg_api": AMNEZIA_WG_API_URL
    }


@app.get("/")
async def root():
    """Информация о сервисе"""
    return {
        "service": "VPN Service Webhook",
        "version": "2.0",
        "endpoints": {
            "webhook": "POST /webhook - YooKassa webhook handler",
            "health": "GET /health - Health check",
            "root": "GET / - Service info"
        }
    }


@app.post("/test/payment/{payment_id}")
async def test_payment_processing(payment_id: str):
    """
    🧪 Тестовый endpoint для проверки обработки платежа
    НЕ ИСПОЛЬЗОВАТЬ В ПРОДАКШЕНЕ!
    
    Usage: POST /test/payment/your_payment_id
    """
    logger.warning(f"⚠️ TEST endpoint called for payment: {payment_id}")
    
    payment_data = get_payment_by_id(payment_id)
    if not payment_data:
        return {"error": "Payment not found"}
    
    success = await process_successful_payment(payment_id, payment_data)
    
    return {
        "payment_id": payment_id,
        "success": success,
        "message": "Payment processed" if success else "Processing failed"
    }