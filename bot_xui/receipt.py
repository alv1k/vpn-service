"""
Receipt scanning via Gemini Vision + finance-api integration.
Flow: user sends photo → Gemini Vision → items → inline category selector → save transaction.
"""
import json
import logging
import base64
import re
from datetime import date
import httpx
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ChatAction
from telegram.ext import ContextTypes
from config import FINANCE_API_URL, FINANCE_BOT_USER, FINANCE_BOT_PASS, GEMINI_API_KEY

logger = logging.getLogger(__name__)

_finance_token = None

_receipt_state = {}

GEMINI_MODEL = "gemini-2.5-flash"
GEMINI_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"

RCPT_PREFIX = "rcpt_"

CATEGORIES = [
    "продукты", "ЖКУ", "автомобиль", "здоровье",
    "сладости", "прочие нужды", "развлечения", "связь",
    "подарки", "одежда", "питомцы", "огород",
    "хобби", "готовая еда", "доставка товаров",
    "благотворительность", "без классификации", "проезд в автобусах",
    "кредиты",
]


async def init_finance_api():
    global _finance_token
    if not FINANCE_API_URL or not FINANCE_BOT_USER or not FINANCE_BOT_PASS:
        logger.warning("Finance API not configured, receipt scanning disabled")
        return
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(f"{FINANCE_API_URL}/api/auth/login", json={
                "username": FINANCE_BOT_USER,
                "password": FINANCE_BOT_PASS,
            })
            r.raise_for_status()
            data = r.json()
            _finance_token = data["token"]
            logger.info("Finance API logged in as %s", FINANCE_BOT_USER)
    except Exception as e:
        logger.error("Finance API login failed: %s", e)


async def _call_finance_api(method, path, json_data=None):
    if not _finance_token:
        raise RuntimeError("Finance API not authenticated")
    url = f"{FINANCE_API_URL}{path}"
    headers = {"Authorization": f"Bearer {_finance_token}"}
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.request(method, url, headers=headers, json=json_data)
        r.raise_for_status()
        return r.json()


async def _get_or_create_instance(tg_id):
    """Find existing instance for tg_id or create a new one via Finance API."""
    try:
        data = await _call_finance_api("GET", f"/api/telegram-instances/{tg_id}")
        return data["instance_id"]
    except Exception:
        pass

    name = f"tg_{tg_id}"
    data = await _call_finance_api("POST", "/api/instances", {"name": name})
    instance_id = data["id"]

    try:
        await _call_finance_api("POST", "/api/telegram-instances", {"tg_id": tg_id, "instance_id": instance_id})
    except Exception as e:
        logger.error("Failed to store telegram_finance_instance: %s", e)

    logger.info("Created finance instance %s for tg_id %s", instance_id, tg_id)
    return instance_id


def _guess_mime(image_bytes):
    if image_bytes[:4] == b"\x89PNG":
        return "image/png"
    if image_bytes[:2] in (b"\xFF\xD8",):
        return "image/jpeg"
    if image_bytes[:4] == b"RIFF":
        return "image/webp"
    return "image/jpeg"


async def _call_gemini_vision(image_bytes):
    b64 = base64.b64encode(image_bytes).decode("utf-8")
    mime = _guess_mime(image_bytes)

    prompt = (
        "Ты парсер кассовых чеков. Извлеки товары и дату из чека на изображении. "
        "Верни ТОЛЬКО JSON объект, без пояснений.\n"
        "Формат: {\"date\": \"2026-07-21\", \"items\": [{\"name\": \"название товара\", \"amount\": число}]}\n"
        "Правила:\n"
        "- Поле date — дата чека в формате YYYY-MM-DD (если не видна — не включай)\n"
        "- Поле amount — это общая стоимость позиции (цена × количество)\n"
        "- Если количество позиции не указано, считай что это 1 штука, amount = цена\n"
        "- Названия товаров — очисти от артикулов и кодов, оставь человекочитаемое название\n"
        "- Игнорируй строки с ИТОГО, СУММА, СДАЧА, НДС, ИНН, адрес, номер чека\n"
        "- Если не можешь распознать ни одного товара, верни {\"items\": []}"
    )

    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(
            f"{GEMINI_URL}?key={GEMINI_API_KEY}",
            json={
                "contents": [{
                    "parts": [
                        {"inline_data": {"mime_type": mime, "data": b64}},
                        {"text": prompt},
                    ]
                }]
            },
        )
        r.raise_for_status()
        data = r.json()

    text = data.get("candidates", [{}])[0].get("content", {}).get("parts", [{}])[0].get("text", "{}")
    date = None
    items = []

    match = re.search(r"\{[\s\S]*\}", text)
    if match:
        try:
            obj = json.loads(match[0])
            if isinstance(obj, dict):
                date = obj.get("date")
                items = obj.get("items", [])
        except json.JSONDecodeError:
            pass

    if not items:
        match = re.search(r"\[[\s\S]*\]", text)
        if match:
            try:
                items = json.loads(match[0])
            except json.JSONDecodeError:
                pass

    return {"items": items, "date": date}


def _fmt(n):
    return f"{float(n):,.2f}".replace(",", " ")


async def process_receipt_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.photo:
        return
    tg_id = update.effective_user.id

    msg1 = await update.message.reply_text("⏳ Скачиваю фото...")
    msg2 = None

    try:
        photo = update.message.photo[-1]
        file = await context.bot.get_file(photo.file_id)
        image_bytes = bytes(await file.download_as_bytearray())

        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
        msg2 = await update.message.reply_text("⏳ Загружаю в Gemini Vision...")
        result = await _call_gemini_vision(image_bytes)
        items = result.get("items", [])
        receipt_date = result.get("date")
        if not items:
            await msg1.delete()
            await msg2.edit_text("❌ Не удалось распознать товары. Попробуйте сфотографировать чек при лучшем освещении.")
            return

        await msg2.edit_text("⏳ Обрабатываю результат...")

        for it in items:
            it["name"] = re.sub(r'^\d+[\.\)]\s*', '', str(it.get("name", ""))).strip()
            amt = round(float(it.get("amount") or 0), 2)
            it["amount"] = amt
            it["quantity"] = 1
            it["price"] = amt

        instance_id = await _get_or_create_instance(tg_id)
        _receipt_state[update.effective_chat.id] = {
            "tg_id": tg_id,
            "items": items,
            "date": receipt_date,
            "instance_id": instance_id,
            "category": "",
        }

        total = sum(float(it.get("amount", 0) or 0) for it in items)
        lines = [f"📄 Найдено товаров: {len(items)}"]
        for i, it in enumerate(items, 1):
            name = it.get("name", "—")
            amt = _fmt(it.get("amount", 0))
            lines.append(f"{i}. {name} — {amt} ₽")
        lines.append(f"\n💵 Итого: {_fmt(total)} ₽")
        text = "\n".join(lines)

        keyboard = _build_keyboard("")
        await msg1.delete()
        await msg2.edit_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

    except Exception as e:
        logger.exception("Receipt processing error")
        target = msg2 if msg2 else msg1
        try:
            await target.edit_text(f"❌ Ошибка: {e}")
        except Exception:
            pass


def _build_keyboard(selected):
    keyboard = []
    for i in range(0, len(CATEGORIES), 4):
        row = []
        for cat in CATEGORIES[i:i + 4]:
            label = f"✓ {cat}" if cat == selected else cat
            row.append(InlineKeyboardButton(label, callback_data=f"{RCPT_PREFIX}cat_{cat}"))
        keyboard.append(row)
    keyboard.append([
        InlineKeyboardButton("💾 Сохранить", callback_data=f"{RCPT_PREFIX}save"),
        InlineKeyboardButton("❌ Отмена", callback_data=f"{RCPT_PREFIX}cancel"),
    ])
    return keyboard


async def receipt_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data[len(RCPT_PREFIX):]
    chat_id = update.effective_chat.id
    state = _receipt_state.get(chat_id)
    if not state:
        await query.edit_message_text("⏳ Сессия истекла, отправьте чек заново.")
        return

    if data == "cancel":
        _receipt_state.pop(chat_id, None)
        await query.edit_message_text("❌ Отменено")
        return

    if data.startswith("cat_"):
        cat = data[4:]
        state["category"] = cat
        keyboard = _build_keyboard(cat)
        await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(keyboard))
        return

    if data == "save":
        category = state.get("category", "") or "без классификации"
        items = state["items"]
        instance_id = state["instance_id"]
        _receipt_state.pop(chat_id, None)

        success = 0
        errors = 0
        for it in items:
            try:
                body = {
                    "name": it.get("name", "Покупка"),
                    "date": state.get("date") or date.today().isoformat(),
                    "type": "expense",
                    "price": it.get("price") or None,
                    "quantity": it.get("quantity") or None,
                    "amount": it.get("amount", 0),
                    "category": category,
                    "comment": "",
                }
                await _call_finance_api("POST", f"/api/instances/{instance_id}/transactions", body)
                success += 1
            except Exception as e:
                logger.error("Failed to save transaction: %s", e)
                errors += 1

        total = sum(float(it.get("amount", 0) or 0) for it in items)
        text = f"✅ Сохранено: {success} позиций на {_fmt(total)} ₽"
        if errors:
            text += f"\n⚠️ Ошибок: {errors}"
        text += f"\n📂 Категория: {category}"
        await query.edit_message_text(text)
