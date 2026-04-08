"""Tests for api/security.py — YooKassa webhook signature verification."""
import base64
import sys
from unittest.mock import patch, MagicMock

import pytest

sys.modules.setdefault("yookassa", MagicMock())


def _make_auth_header(shop_id: str, secret: str) -> str:
    """Build Basic auth header like YooKassa sends."""
    encoded = base64.b64encode(f"{shop_id}:{secret}".encode()).decode()
    return f"Basic {encoded}"


@pytest.fixture
def client():
    from api.webhook import app
    from fastapi.testclient import TestClient
    return TestClient(app)


class TestVerifyYookassaSignature:

    def test_valid_credentials(self):
        """Known shop_id + secret passes verification."""
        from config import YOO_KASSA_SHOP_ID, YOO_KASSA_SECRET_KEY
        from api.security import verify_yookassa_signature

        request = MagicMock()
        request.headers = {"Authorization": _make_auth_header(YOO_KASSA_SHOP_ID, YOO_KASSA_SECRET_KEY)}

        # Should not raise
        verify_yookassa_signature(request)

    def test_test_credentials_accepted(self):
        """Test shop credentials are also accepted if configured."""
        from config import YOO_KASSA_TEST_SHOP_ID, YOO_KASSA_TEST_SECRET_KEY
        if not YOO_KASSA_TEST_SHOP_ID or not YOO_KASSA_TEST_SECRET_KEY:
            pytest.skip("Test YooKassa credentials not configured")

        from api.security import verify_yookassa_signature

        request = MagicMock()
        request.headers = {"Authorization": _make_auth_header(
            YOO_KASSA_TEST_SHOP_ID, YOO_KASSA_TEST_SECRET_KEY
        )}

        verify_yookassa_signature(request)

    def test_missing_header_returns_401(self):
        """No Authorization header raises 401."""
        from api.security import verify_yookassa_signature
        from fastapi import HTTPException

        request = MagicMock()
        request.headers = {}

        with pytest.raises(HTTPException) as exc_info:
            verify_yookassa_signature(request)
        assert exc_info.value.status_code == 401
        assert "Missing" in exc_info.value.detail

    def test_wrong_credentials_returns_401(self):
        """Invalid shop_id/secret pair raises 401."""
        from api.security import verify_yookassa_signature
        from fastapi import HTTPException

        request = MagicMock()
        request.headers = {"Authorization": _make_auth_header("fake_shop", "fake_secret")}

        with pytest.raises(HTTPException) as exc_info:
            verify_yookassa_signature(request)
        assert exc_info.value.status_code == 401
        assert "Invalid" in exc_info.value.detail

    def test_malformed_base64_returns_401(self):
        """Garbage in Authorization header raises 401."""
        from api.security import verify_yookassa_signature
        from fastapi import HTTPException

        request = MagicMock()
        request.headers = {"Authorization": "Basic !!!not-base64!!!"}

        with pytest.raises(HTTPException) as exc_info:
            verify_yookassa_signature(request)
        assert exc_info.value.status_code == 401

    def test_non_basic_scheme_returns_401(self):
        """Bearer or other scheme raises 401."""
        from api.security import verify_yookassa_signature
        from fastapi import HTTPException

        request = MagicMock()
        request.headers = {"Authorization": "Bearer some-token"}

        with pytest.raises(HTTPException) as exc_info:
            verify_yookassa_signature(request)
        assert exc_info.value.status_code == 401
