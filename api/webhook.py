from fastapi import FastAPI, Request, HTTPException, Response
import json
import sys
import httpx
from ipaddress import ip_address, ip_network
from api.subscriptions import activate_subscription
from api.db import update_payment_status, is_payment_processed, get_payment_status, get_payment_by_id, get_or_create_user, create_vpn_key, get_subscription_until, get_used_client_ips
from api.wireguard import AmneziaWGClient
from bot.tariffs import TARIFFS
from config import TELEGRAM_BOT_TOKEN, WG_CONF_PATH, WG_BIN, WG_INTERFACE, AMNEZIA_WG_API_URL, AMNEZIA_WG_API_PASSWORD
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


print("🔥 APP STARTED", flush=True)

def verify_yookassa_ip(request: Request):
    if not request.client:
        raise HTTPException(status_code=403, detail="No client IP")

    ip = ip_address(request.client.host)
    if not any(ip in net for net in YOO_IPS):
        raise HTTPException(status_code=403, detail="Forbidden IP")


@app.post("/webhook")
async def yookassa_webhook(request: Request):
    # print("🔥 YooKassa webhook received", flush=True)
    print("🔥 YooKassa webhook received")

    # ===== IP check =====
    verify_yookassa_ip(request)

    # ===== Read body =====
    body = await request.body()

    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        print("❌ Invalid JSON", file=sys.stderr)
        return Response(status_code=400)

    event = payload.get("event")
    obj = payload.get("object", {})

    payment_id = obj.get("id")
    status_raw = obj.get("status")
    metadata = obj.get("metadata", {})

    tg_id = metadata.get("tg_id")
    tariff = metadata.get("tariff", "default")

    if not payment_id:
        return Response(status_code=200)

        
    # 1️⃣ Проверяем, есть ли платёж
    current_status = get_payment_status(payment_id)
    if not current_status:
        # неизвестный платёж — логируем и выходим
        print(f"⚠️ Unknown payment_id {payment_id}", file=sys.stderr)
        return {"status": "ignored"}

    # 2️⃣ Если уже финальный — игнор
    if current_status in ("paid", "canceled"):
        print(
            f"🔁 Duplicate webhook ignored: {payment_id} ({current_status})",
            file=sys.stderr
        )
        return {"status": "duplicate"}

    # ===== Нормализация статуса =====
    if status_raw == "succeeded":
        new_status = "paid"
    elif status_raw in ("canceled", "failed"):
        new_status = "canceled"
    else:
        status = "pending"
        
    # 4️⃣ Если статус не меняется — игнор
    if new_status == current_status:
        return {"status": "no_change"}

    payment = get_payment_by_id(payment_id)
    if  current_status == "pending" and new_status == "paid":
        activate_subscription(payment_id)
        tg_id = payment["tg_id"]
        tariff_key = payment["tariff"]

        # пользователь
        user_id = get_or_create_user(tg_id)

        # подписка до
        subscription_until = get_subscription_until(tg_id)

        # создание клиента через AmneziaWG API
        wg_client = AmneziaWGClient(api_url=AMNEZIA_WG_API_URL, password=AMNEZIA_WG_API_PASSWORD)

        # формирование имени клиента
        client_name = f"user_{tg_id}_{subscription_until.strftime('%Y%m%d')}"

        # асинхронно создаем клиента
        client_data = await wg_client.create_client(name=client_name)

        if client_data:
            client_id = client_data.get('id')
            client_ip = client_data.get('address')
            client_public_key = client_data.get('publicKey')

            # получаем конфигурацию клиента
            client_config = await wg_client.get_client_config(client_id)

            # запись VPN ключа (один раз!)
            create_vpn_key(
                user_id=user_id,
                payment_id=payment_id,
                client_ip=client_ip,
                client_public_key=client_public_key,
                config=client_config,
                expires_at=subscription_until
            )

    # ===== Идемпотентность =====
    if is_payment_processed(payment_id):
        print(f"🔁 Payment {payment_id} already processed", file=sys.stderr)
        return Response(status_code=200)

    # ===== Обновляем БД =====
    update_payment_status(payment_id, new_status)

    # ===== Находим читаемое название тарифа =====
    current_tariff = TARIFFS.get(tariff)

    if not current_tariff:
        raise ValueError(f"Unknown tariff: {tariff}")

    # информация по тарифу
    tariff_name = current_tariff["name"]
    yookassa_description = current_tariff["yookassa_description"]

    # ===== Telegram уведомление =====
    if tg_id:
        if new_status == "paid":
            try:
                file = BufferedInputFile(
                    client_config.encode(),
                    filename=f"tiin_service{payment_id}.conf"
                )

                await bot.send_document(
                    chat_id=tg_id,
                    document=file,
                    caption=f"🔐 VPN конфиг для тарифа {tariff}. Активен до {subscription_until:%d.%m.%Y}"
                )
            except Exception as e:
                print("❌ VPN send error:", e, file=sys.stderr)

            message = f"✅ Платёж {payment_id} успешно завершён.\n\nТариф: {tariff_name}\n{yookassa_description}"
        elif new_status == "canceled":
            message = f"❌ Платёж {payment_id} не прошёл."
        else:
            message = f"⏳ Платёж {payment_id}: в процессе ({new_status})"

        async with httpx.AsyncClient(timeout=5) as client:
            try:
                await client.post(
                    TELEGRAM_API,
                    data={"chat_id": tg_id, "text": message}
                )
            except Exception as e:
                print("❌ Telegram notify error:", e, file=sys.stderr)

    print(
        f"✅ Payment {payment_id} processed | TG={tg_id} | status={new_status}",
        file=sys.stderr
    )

    # ===== ВАЖНО: всегда 200 =====
    return Response(status_code=200)
