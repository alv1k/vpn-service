from fastapi import FastAPI, Request, HTTPException, Response
import json
import sys
import httpx
from ipaddress import ip_address, ip_network
from datetime import datetime

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
from api.wireguard import AmneziaWGClient  # ⭐ Обновленный клиент
from bot.tariffs import TARIFFS
from config import (
    TELEGRAM_BOT_TOKEN, 
    AMNEZIA_WG_API_URL, 
    AMNEZIA_WG_API_PASSWORD
)
from bot.bot import bot

from aiogram.types import BufferedInputFile

app = FastAPI()

TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"

# ===== Белые IP ЮKassa =====
YOO_IPS = [
    ip_network("185.71.76.0/27"),
    ip_network("185.71.77.0/27"),
    ip_network("77.75.153.0/25"),
    ip_network("77.75.154.128/25"),
]

# ⭐ Инициализация AmneziaWG клиента (один раз при старте)
wg_client = AmneziaWGClient(
    api_url=AMNEZIA_WG_API_URL, 
    password=AMNEZIA_WG_API_PASSWORD
)

print("🔥 WEBHOOK APP STARTED", flush=True)


def verify_yookassa_ip(request: Request):
    """Проверка IP адреса YooKassa"""
    if not request.client:
        raise HTTPException(status_code=403, detail="No client IP")

    ip = ip_address(request.client.host)
    if not any(ip in net for net in YOO_IPS):
        raise HTTPException(status_code=403, detail="Forbidden IP")


@app.post("/webhook")
async def yookassa_webhook(request: Request):
    """
    Обработчик webhook от YooKassa
    Вызывается когда платеж меняет статус
    """
    print("🔥 YooKassa webhook received", flush=True)

    # ===== Проверка IP =====
    verify_yookassa_ip(request)

    # ===== Чтение тела запроса =====
    body = await request.body()

    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        print("❌ Invalid JSON", file=sys.stderr, flush=True)
        return Response(status_code=400)

    # ===== Парсинг данных =====
    event = payload.get("event")
    obj = payload.get("object", {})

    payment_id = obj.get("id")
    status_raw = obj.get("status")
    metadata = obj.get("metadata", {})

    tg_id = metadata.get("tg_id")
    tariff = metadata.get("tariff", "default")

    if not payment_id:
        print("⚠️ No payment_id in webhook", file=sys.stderr, flush=True)
        return Response(status_code=200)

    # ===== 1️⃣ Проверяем существование платежа =====
    current_status = get_payment_status(payment_id)
    if not current_status:
        print(f"⚠️ Unknown payment_id {payment_id}", file=sys.stderr, flush=True)
        return {"status": "ignored"}

    # ===== 2️⃣ Проверка на дубликат (уже обработан) =====
    if current_status in ("paid", "canceled"):
        print(
            f"🔁 Duplicate webhook ignored: {payment_id} ({current_status})",
            file=sys.stderr,
            flush=True
        )
        return {"status": "duplicate"}

    # ===== 3️⃣ Нормализация статуса =====
    if status_raw == "succeeded":
        new_status = "paid"
    elif status_raw in ("canceled", "failed"):
        new_status = "canceled"
    else:
        new_status = "pending"

    # ===== 4️⃣ Проверка изменения статуса =====
    if new_status == current_status:
        print(f"ℹ️ Status unchanged: {payment_id} ({new_status})", flush=True)
        return {"status": "no_change"}

    # ===== 5️⃣ Получаем данные платежа =====
    payment = get_payment_by_id(payment_id)
    if not payment:
        print(f"❌ Payment data not found: {payment_id}", file=sys.stderr, flush=True)
        return Response(status_code=404)

    tg_id = payment["tg_id"]
    tariff_key = payment["tariff"]

    # ===== 6️⃣ ОБРАБОТКА УСПЕШНОГО ПЛАТЕЖА =====
    if current_status == "pending" and new_status == "paid":
        print(f"💰 Processing successful payment: {payment_id}", flush=True)

        try:
            # Активация подписки в БД
            activate_subscription(payment_id)

            # Получение пользователя
            user_id = get_or_create_user(tg_id)

            # Дата окончания подписки
            subscription_until = get_subscription_until(tg_id)

            # ⭐ СОЗДАНИЕ VPN КОНФИГА ЧЕРЕЗ API ⭐
            # Формирование уникального имени клиента
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            client_name = f"user_{tg_id}_{timestamp}"

            print(f"🔑 Creating VPN client: {client_name}", flush=True)

            # ⭐ Асинхронное создание клиента через AmneziaWG API
            client_data = await wg_client.create_client(name=client_name)

            if not client_data:
                raise RuntimeError("Failed to create WireGuard client via API")

            # Извлечение данных клиента
            client_id = client_data.get('id')
            client_ip = client_data.get('address')  # IP назначается автоматически
            client_public_key = client_data.get('publicKey')

            print(
                f"✅ Client created: ID={client_id}, IP={client_ip}",
                flush=True
            )

            # ⭐ Получение конфигурационного файла
            client_config = await wg_client.get_client_config(client_id)

            if not client_config:
                raise RuntimeError(f"Failed to get config for client {client_id}")

            print(f"📄 Config retrieved for client: {client_id}", flush=True)

            # ⭐ Сохранение VPN ключа в БД
            create_vpn_key(
                user_id=user_id,
                payment_id=payment_id,
                client_ip=client_ip,
                client_public_key=client_public_key,
                config=client_config,
                expires_at=subscription_until
            )

            print(
                f"💾 VPN key saved to DB: user={user_id}, ip={client_ip}",
                flush=True
            )

            # ===== 7️⃣ Отправка конфига в Telegram =====
            try:
                # Создание файла для отправки
                file = BufferedInputFile(
                    client_config.encode('utf-8'),
                    filename=f"vpn_config_{payment_id}.conf"
                )

                # Отправка конфига пользователю
                await bot.send_document(
                    chat_id=tg_id,
                    document=file,
                    caption=(
                        f"✅ Ваш VPN конфиг готов!\n\n"
                        f"🔑 Тариф: {tariff_key}\n"
                        f"🌐 IP: {client_ip}\n"
                        f"📅 Активен до: {subscription_until.strftime('%d.%m.%Y')}\n\n"
                        f"📱 Импортируйте файл в приложение AmneziaVPN"
                    )
                )

                print(f"📤 Config sent to Telegram user: {tg_id}", flush=True)

            except Exception as e:
                print(
                    f"❌ Failed to send config to Telegram: {e}",
                    file=sys.stderr,
                    flush=True
                )

        except Exception as e:
            print(
                f"❌ Error processing payment {payment_id}: {e}",
                file=sys.stderr,
                flush=True
            )
            # Не возвращаем ошибку YooKassa, чтобы не было повторных попыток
            # Но логируем для ручной обработки

    # ===== 8️⃣ Проверка идемпотентности =====
    if is_payment_processed(payment_id):
        print(
            f"🔁 Payment already marked as processed: {payment_id}",
            file=sys.stderr,
            flush=True
        )
        return Response(status_code=200)

    # ===== 9️⃣ Обновление статуса в БД =====
    update_payment_status(payment_id, new_status)
    print(f"💾 Payment status updated: {payment_id} -> {new_status}", flush=True)

    # ===== 🔟 Получение информации о тарифе =====
    current_tariff = TARIFFS.get(tariff_key)

    if not current_tariff:
        print(f"⚠️ Unknown tariff: {tariff_key}", file=sys.stderr, flush=True)
        # Используем дефолтные значения
        tariff_name = tariff_key
        yookassa_description = "VPN подписка"
    else:
        tariff_name = current_tariff["name"]
        yookassa_description = current_tariff.get("yookassa_description", "")

    # ===== 1️⃣1️⃣ Telegram уведомление о статусе =====
    if tg_id:
        if new_status == "paid":
            message = (
                f"✅ Оплата успешно завершена!\n\n"
                f"💳 Платёж: {payment_id}\n"
                f"📦 Тариф: {tariff_name}\n"
                f"{yookassa_description}"
            )
        elif new_status == "canceled":
            message = (
                f"❌ Платёж не прошёл\n\n"
                f"💳 ID: {payment_id}\n"
                f"Попробуйте ещё раз или обратитесь в поддержку."
            )
        else:
            message = f"⏳ Платёж {payment_id} в обработке ({new_status})"

        # Отправка уведомления через Telegram API
        async with httpx.AsyncClient(timeout=5) as client:
            try:
                await client.post(
                    TELEGRAM_API,
                    data={"chat_id": tg_id, "text": message}
                )
                print(f"📨 Notification sent to user: {tg_id}", flush=True)
            except Exception as e:
                print(
                    f"❌ Failed to send Telegram notification: {e}",
                    file=sys.stderr,
                    flush=True
                )

    print(
        f"✅ Payment {payment_id} processed | "
        f"TG={tg_id} | Status={new_status}",
        flush=True
    )

    # ===== 1️⃣2️⃣ ВАЖНО: всегда возвращаем 200 =====
    # Чтобы YooKassa не повторял запрос
    return Response(status_code=200)


@app.get("/health")
async def health_check():
    """Health check endpoint для мониторинга"""
    return {
        "status": "healthy",
        "service": "vpn-webhook",
        "timestamp": datetime.now().isoformat()
    }


@app.get("/")
async def root():
    """Корневой endpoint"""
    return {
        "service": "VPN Service Webhook",
        "version": "2.0",
        "endpoints": {
            "webhook": "POST /webhook",
            "health": "GET /health"
        }
    }