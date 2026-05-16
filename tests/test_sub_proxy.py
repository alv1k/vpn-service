"""Tests for api/sub_proxy.py — VLESS subscription proxy endpoint."""
import base64
import sys
import time
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock, AsyncMock
from urllib.parse import unquote

import pytest

sys.modules.setdefault("yookassa", MagicMock())


# ═════════════════════════════════════════════
#  _build_headers
# ═════════════════════════════════════════════

class TestBuildHeaders:

    def test_subscription_userinfo_contains_expire(self):
        from api.sub_proxy import _build_headers
        expires = datetime(2026, 12, 31, 23, 59)
        headers = _build_headers(expires)
        assert "subscription-userinfo" in headers
        assert f"expire={int(expires.timestamp())}" in headers["subscription-userinfo"]

    def test_none_expires_uses_zero(self):
        from api.sub_proxy import _build_headers
        headers = _build_headers(None)
        assert "expire=0" in headers["subscription-userinfo"]

    def test_profile_title_is_base64(self):
        from api.sub_proxy import _build_headers, SUB_PROFILE_TITLE
        headers = _build_headers(None)
        assert headers["profile-title"].startswith("base64:")
        decoded = base64.b64decode(headers["profile-title"].removeprefix("base64:")).decode()
        assert decoded == SUB_PROFILE_TITLE

    def test_update_interval_header(self):
        from api.sub_proxy import _build_headers, SUB_UPDATE_INTERVAL_HOURS
        headers = _build_headers(None)
        assert headers["profile-update-interval"] == str(SUB_UPDATE_INTERVAL_HOURS)


# ═════════════════════════════════════════════
#  _pick_vless_key
# ═════════════════════════════════════════════

class TestPickVlessKey:

    @patch("api.sub_proxy.get_keys_by_tg_id")
    def test_picks_active_over_expired(self, mock_keys):
        from api.sub_proxy import _pick_vless_key
        now = datetime.utcnow()
        mock_keys.return_value = [
            {"vpn_type": "vless", "subscription_link": "https://xui/old",
             "expires_at": now - timedelta(days=10)},
            {"vpn_type": "vless", "subscription_link": "https://xui/new",
             "expires_at": now + timedelta(days=10)},
        ]
        result = _pick_vless_key({"tg_id": 100, "id": 1})
        assert result is not None
        assert result["subscription_link"] == "https://xui/new"

    @patch("api.sub_proxy.get_keys_by_tg_id", return_value=[])
    def test_no_keys_returns_none(self, mock_keys):
        from api.sub_proxy import _pick_vless_key
        with patch("api.sub_proxy.get_keys_by_user_id", return_value=[]):
            result = _pick_vless_key({"tg_id": 100, "id": 1})
            assert result is None

    @patch("api.sub_proxy.get_keys_by_tg_id")
    def test_ignores_keys_without_subscription_link(self, mock_keys):
        from api.sub_proxy import _pick_vless_key
        mock_keys.return_value = [
            {"vpn_type": "vless", "subscription_link": None,
             "expires_at": datetime.utcnow() + timedelta(days=10)},
        ]
        result = _pick_vless_key({"tg_id": 100, "id": 1})
        assert result is None

    @patch("api.sub_proxy.get_keys_by_tg_id")
    def test_ignores_awg_and_softether(self, mock_keys):
        from api.sub_proxy import _pick_vless_key
        future = datetime.utcnow() + timedelta(days=10)
        mock_keys.return_value = [
            {"vpn_type": "awg", "subscription_link": "https://awg",
             "expires_at": future},
            {"vpn_type": "softether", "subscription_link": "https://se",
             "expires_at": future},
        ]
        result = _pick_vless_key({"tg_id": 100, "id": 1})
        assert result is None


# ═════════════════════════════════════════════
#  proxy_subscription endpoint
# ═════════════════════════════════════════════

class TestProxySubscription:

    def setup_method(self):
        """Clear cache between tests."""
        from api import sub_proxy
        sub_proxy._CACHE.clear()

    @pytest.mark.asyncio
    @patch("api.sub_proxy.get_user_by_web_token", return_value=None)
    async def test_invalid_token_404(self, mock_user):
        from fastapi import HTTPException
        from api.sub_proxy import proxy_subscription
        with pytest.raises(HTTPException) as exc:
            await proxy_subscription("bad-token")
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    @patch("api.sub_proxy._pick_vless_key", return_value=None)
    @patch("api.sub_proxy.get_user_by_web_token", return_value={"tg_id": 100, "id": 1})
    async def test_no_vless_key_404(self, mock_user, mock_pick):
        from fastapi import HTTPException
        from api.sub_proxy import proxy_subscription
        with pytest.raises(HTTPException) as exc:
            await proxy_subscription("valid-token")
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    @patch("api.sub_proxy._fetch_xui", new_callable=AsyncMock)
    @patch("api.sub_proxy._pick_vless_key")
    @patch("api.sub_proxy.get_user_by_web_token", return_value={"tg_id": 100, "id": 1})
    async def test_happy_path_returns_proxied_body(self, mock_user, mock_pick, mock_fetch):
        from api.sub_proxy import proxy_subscription
        future = datetime.utcnow() + timedelta(days=23)
        mock_pick.return_value = {
            "subscription_link": "https://xui.example/sub/abc",
            "expires_at": future,
        }
        # XUI returns base64-encoded vless link
        vless = "vless://uuid@host:443?type=tcp&security=reality#old"
        expected_vless = "vless://uuid@host:443?type=tcp&security=reality#🐿️ TIIN vpn | ✅Active"
        mock_fetch.return_value = base64.b64encode(vless.encode())

        response = await proxy_subscription("tok")
        body_decoded = base64.b64decode(response.body).decode()
        assert body_decoded == expected_vless

        # Headers
        assert "subscription-userinfo" in response.headers
        assert f"expire={int(future.timestamp())}" in response.headers["subscription-userinfo"]

    @pytest.mark.asyncio
    @patch("api.sub_proxy._fetch_xui", new_callable=AsyncMock)
    @patch("api.sub_proxy._pick_vless_key")
    @patch("api.sub_proxy.get_user_by_web_token", return_value={"tg_id": 100, "id": 1})
    async def test_cache_hit_skips_xui_fetch(self, mock_user, mock_pick, mock_fetch):
        from api.sub_proxy import proxy_subscription
        import time
        mock_pick.return_value = {
            "subscription_link": "https://xui.example/sub/abc",
            "expires_at": datetime.utcnow() + timedelta(days=10),
        }
        mock_fetch.return_value = base64.b64encode(b"vless://u@h:443#r")

        # First call — fetches
        await proxy_subscription("tok")
        assert mock_fetch.call_count == 1

        # Second call — should hit cache
        await proxy_subscription("tok")
        assert mock_fetch.call_count == 1  # unchanged (1 was still the count)    @pytest.mark.asyncio
    @pytest.mark.asyncio
    @patch("api.sub_proxy._fetch_xui", new_callable=AsyncMock)
    @patch("api.sub_proxy._pick_vless_key")
    @patch("api.sub_proxy.get_user_by_web_token", return_value={"tg_id": 100, "id": 1})
    async def test_cache_expires_after_ttl(self, mock_user, mock_pick, mock_fetch):
        from api import sub_proxy
        mock_pick.return_value = {
            "subscription_link": "https://xui.example/sub/abc",
            "expires_at": datetime.utcnow() + timedelta(days=10),
        }
        mock_fetch.return_value = base64.b64encode(b"vless://u@h:443#r")

        await sub_proxy.proxy_subscription("tok")
        assert mock_fetch.call_count == 1

        # Expire cache entry manually
        ts, body, hdr = sub_proxy._CACHE["tok"]
        # Set TS to be older than SUB_CACHE_TTL
        sub_proxy._CACHE["tok"] = (datetime.utcnow() - timedelta(seconds=sub_proxy.SUB_CACHE_TTL + 1), body, hdr)

        await sub_proxy.proxy_subscription("tok")
        assert mock_fetch.call_count == 2

    @pytest.mark.asyncio
    @patch("api.sub_proxy._fetch_xui", new_callable=AsyncMock)
    @patch("api.sub_proxy._pick_vless_key")
    @patch("api.sub_proxy.get_user_by_web_token", return_value={"tg_id": 100, "id": 1})
    async def test_xui_error_falls_back_to_stale_cache(self, mock_user, mock_pick, mock_fetch):
        from api import sub_proxy
        mock_pick.return_value = {
            "subscription_link": "https://xui.example/sub/abc",
            "expires_at": datetime.utcnow() + timedelta(days=10),
        }
        mock_fetch.return_value = base64.b64encode(b"vless://u@h:443#r")

        # Prime cache
        await sub_proxy.proxy_subscription("tok")

        # Expire it and make XUI fail
        ts, body, hdr = sub_proxy._CACHE["tok"]
        sub_proxy._CACHE["tok"] = (datetime.utcnow() - timedelta(seconds=sub_proxy.SUB_CACHE_TTL + 1), body, hdr)
        mock_fetch.side_effect = RuntimeError("XUI down")

        response = await sub_proxy.proxy_subscription("tok")
        # Fallback returns the stale cached body
        assert response.body == body

    @pytest.mark.asyncio
    @patch("api.sub_proxy._fetch_xui", new_callable=AsyncMock, side_effect=RuntimeError("boom"))
    @patch("api.sub_proxy._pick_vless_key")
    @patch("api.sub_proxy.get_user_by_web_token", return_value={"tg_id": 100, "id": 1})
    async def test_xui_error_no_cache_returns_503(self, mock_user, mock_pick, mock_fetch):
        from fastapi import HTTPException
        from api.sub_proxy import proxy_subscription
        mock_pick.return_value = {
            "subscription_link": "https://xui.example/sub/abc",
            "expires_at": datetime.utcnow() + timedelta(days=10),
        }
        with pytest.raises(HTTPException) as exc:
            await proxy_subscription("tok")
        assert exc.value.status_code == 503
