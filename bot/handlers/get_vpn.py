from aiogram import Router, F
from aiogram.types import CallbackQuery, BufferedInputFile
from api.db import get_last_paid_payment, mark_vpn_issued
from config import WG_SERVER_PUBLIC_KEY, WG_SERVER_ENDPOINT
import logging

router = Router()

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

        # 2️⃣ Генерируем VPN конфиг
        # 🔥 ВАЖНО: генерация ТУТ, а не в webhook
        client_conf = generate_vpn_config(
            tg_id=tg_id,
            tariff=payment["tariff"]
        )

        # 3️⃣ Отправляем файл
        try:
            file = BufferedInputFile(
                client_conf.encode(),
                filename="vpn.conf"
            )

            await callback.message.answer_document(
                document=file,
                caption="🔐 Ваш VPN конфиг"
            )

        except Exception as e:
            logger.exception("VPN send failed")
            await callback.answer(
                "❌ Ошибка при отправке VPN",
                show_alert=True
            )
            return

        # 4️⃣ Помечаем, что VPN выдан
        mark_vpn_issued(payment["payment_id"])

        await callback.answer("✅ VPN успешно выдан")