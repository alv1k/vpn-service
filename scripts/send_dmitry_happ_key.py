#!/usr/bin/env python3
import asyncio
import logging
import sys

sys.path.insert(0, "/home/alvik/vpn-service")

from dotenv import load_dotenv
load_dotenv("/home/alvik/vpn-service/.env")

from bot_xui.messaging import send_link_safely

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

TG_ID = 115744584

MESSAGE = (
    "Версия приложения играет важную роль.\n"
    "💡 Ссылка подписки полностью протестирована и стабильно работает на <b>Happ версии 2.18.3</b> (с ядром <b>Xray 26.3.27</b>). "
    "В более старых версиях Happ для Windows может сбоить сетевой загрузчик и отсутствовать поддержка протокола xHTTP. "
    "Рекомендуем обновить приложение с официального сайта https://happproxy.com.\n\n"
    "А чтобы заработало прямо сейчас без ожидания загрузки по ссылке, скопируйте прямой ключ через <b>российский шлюз (XHTTP)</b> — он добавляется напрямую:\n\n"
    "<code>vless://3c02a352-f97a-4510-bc38-520917df18f7@139.100.207.18:8443?alpn=http%2F1.1&encryption=none&host=&path=%2Fapi%2Fv1%2Fws&security=tls&sni=ru-server.tiinservice.online&type=ws#RU-VLESS-tiin_115744584</code>\n\n"
    "<b>Как подключить:</b>\n"
    "1. Скопируйте ключ выше.\n"
    "2. В приложении <b>Happ</b> нажмите <b>« + »</b> вверху ➔ <b>«Добавить из буфера»</b>.\n"
    "3. Нажмите кнопку подключения по центру.\n\n"
    "Напишите прямо здесь, какая версия Happ у вас сейчас установлена?"
)

BUTTONS = [
    [
        {"text": "🟢 Получилось", "callback_data": "setup_win_ok"},
        {"text": "🔴 Не получилось", "callback_data": "setup_win_fail"},
    ]
]

async def main():
    ok = await send_link_safely(
        tg_id=TG_ID,
        text=MESSAGE,
        buttons=BUTTONS,
        parse_mode="HTML",
        source="admin_send",
        scenario="happ_win_key_assist",
    )

    if ok:
        logger.info(f"✅ Сообщение успешно отправлено пользователю {TG_ID}")
    else:
        logger.error(f"❌ Не удалось отправить сообщение {TG_ID}")

if __name__ == "__main__":
    asyncio.run(main())
