from aiogram import Router, F
from aiogram.types import CallbackQuery, BufferedInputFile
from api.db import get_last_paid_payment, mark_vpn_issued
from config import WG_SERVER_PUBLIC_KEY, WG_SERVER_ENDPOINT, AMNEZIA_WG_API_URL, AMNEZIA_WG_API_PASSWORD
import logging
import httpx
from api.wireguard import AmneziaWGClient

router = Router()

logger = logging.getLogger(__name__)

@router.callback_query(F.data == "get_vpn")
async def get_vpn_handler(callback: CallbackQuery):
    await callback.answer()
    tg_id = callback.from_user.id

    # 1️⃣ Ищем последний оплаченный платёж
    payment = get_last_paid_payment(tg_id)

    if not payment:
        await callback.answer(
            "❌ У вас нет оплаченных тарифов",
            show_alert=True
        )
        return

    if payment["vpn_issued"]:
        await callback.answer(
            "⚠️ VPN уже был выдан ранее",
            show_alert=True
        )
        return

    # 2️⃣ Создаем VPN конфиг через AmneziaWG API
    # 🔥 ВАЖНО: генерация ТУТ, а не в webhook
    try:
        # Создаем клиента AmneziaWG
        wg_client = AmneziaWGClient(api_url=AMNEZIA_WG_API_URL, password=AMNEZIA_WG_API_PASSWORD)

        # Создаем имя клиента
        client_name = f"tg_{tg_id}_{payment['payment_id'][:8]}"

        # Создаем клиента в AmneziaWG
        client_data = await wg_client.create_client(name=client_name)

        # Получаем ID клиента из ответа
        client_id = client_data['id']

        # Получаем конфигурацию клиента
        client_conf = await wg_client.get_client_config(client_id=client_id)

        # Отправляем файл
        file = BufferedInputFile(
            client_conf.encode(),
            filename=f"vpn_{tg_id}_{payment['payment_id'][:8]}.conf"
        )

        await callback.message.answer_document(
            document=file,
            caption="🔐 Ваш VPN конфиг"
        )

        # 4️⃣ Помечаем, что VPN выдан
        mark_vpn_issued(payment["payment_id"])

        await callback.answer("✅ VPN успешно выдан")

    except Exception as e:
        logger.exception("VPN creation/send failed")
        await callback.answer(
            "❌ Ошибка при создании или отправке VPN",
            show_alert=True
        )
        return