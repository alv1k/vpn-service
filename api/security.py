import base64
from fastapi import HTTPException, Request
import os
from config import (
    YOO_KASSA_SHOP_ID, YOO_KASSA_SECRET_KEY,
    YOO_KASSA_TEST_SHOP_ID, YOO_KASSA_TEST_SECRET_KEY,
)

if not YOO_KASSA_SHOP_ID or not YOO_KASSA_SECRET_KEY:
    raise RuntimeError("YooKassa webhook credentials not set")

_VALID_CREDENTIALS = {(YOO_KASSA_SHOP_ID, YOO_KASSA_SECRET_KEY)}
if YOO_KASSA_TEST_SHOP_ID and YOO_KASSA_TEST_SECRET_KEY:
    _VALID_CREDENTIALS.add((YOO_KASSA_TEST_SHOP_ID, YOO_KASSA_TEST_SECRET_KEY))


def verify_yookassa_signature(request: Request):
    auth = request.headers.get("Authorization")

    if not auth or not auth.startswith("Basic "):
        raise HTTPException(status_code=401, detail="Missing Authorization header")

    try:
        decoded = base64.b64decode(auth.split(" ")[1]).decode()
        shop_id, secret = decoded.split(":")
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid Authorization header")

    if (shop_id, secret) not in _VALID_CREDENTIALS:
        raise HTTPException(status_code=401, detail="Invalid YooKassa signature")

