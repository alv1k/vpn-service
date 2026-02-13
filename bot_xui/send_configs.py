from io import BytesIO
import html
import qrcode
import logging

log = logging.getLogger(__name__)

async def send_vless_config(bot, chat_id: int, vless_url: str, name: str):
    """
    Универсальная отправка VLESS:
    1) .txt файл
    2) QR
    3) текст (HTML escape)
    """
    filename = f"{name}_vless.txt"

    # 1️⃣ файл
    try:
        buf = BytesIO(vless_url.encode())
        buf.name = filename

        await bot.send_document(
            chat_id=chat_id,
            document=buf,
            caption="🔐 VLESS конфигурация"
        )
        return

    except Exception as e:
        log.warning(f"VLESS file failed: {e}")

    # 2️⃣ QR
    try:
        img = qrcode.make(vless_url)
        buf = BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)

        await bot.send_photo(
            chat_id=chat_id,
            photo=buf,
            caption="📱 VLESS QR-код"
        )
        return

    except Exception as e:
        log.warning(f"VLESS QR failed: {e}")

    # 3️⃣ текст (fallback)
    safe = html.escape(vless_url)

    await bot.send_message(
        chat_id=chat_id,
        text=f"🔐 <b>VLESS конфиг</b>\n\n<code>{safe}</code>",
        parse_mode="HTML"
    )
    

    # file = BytesIO(vless_link.encode())
    # file.name = f"{client_uuid}_vless.txt"

    # await query.message.reply_document(
    #     document=file,
    #     caption="📄 VLESS конфиг файлом"
    # )

async def send_amneziawg_config(bot, chat_id: int, config_text: str, name: str):
    """
    AmneziaWG / WireGuard конфиг
    """
    filename = f"{name}_amneziawg.conf"

    # 1️⃣ файл
    try:
        buf = BytesIO(config_text.encode())
        buf.name = filename

        await bot.send_document(
            chat_id=chat_id,
            document=buf,
            caption="🔐 AmneziaWG конфигурация"
        )
        return

    except Exception as e:
        log.warning(f"AWG file failed: {e}")

    # 2️⃣ QR (не все клиенты поддерживают, но пусть будет)
    try:
        img = qrcode.make(config_text)
        buf = BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)

        await bot.send_photo(
            chat_id=chat_id,
            photo=buf,
            caption="📱 AmneziaWG QR-код"
        )
        return

    except Exception as e:
        log.warning(f"AWG QR failed: {e}")

    # 3️⃣ текст
    safe = html.escape(config_text)

    await bot.send_message(
        chat_id=chat_id,
        text=f"🔐 <b>AmneziaWG конфиг</b>\n\n<code>{safe}</code>",
        parse_mode="HTML"
    )
