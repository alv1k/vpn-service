"""Tests for bot_xui/vpn_factory.py — VPN config creation and test handlers."""
import sys
from unittest.mock import patch, MagicMock, AsyncMock
from datetime import datetime, timezone, timedelta
from io import BytesIO

import pytest

sys.modules.setdefault("yookassa", MagicMock())


# ═════════════════════════════════════════════
#  make_qr_bytes
# ═════════════════════════════════════════════

def test_make_qr_bytes_returns_png():
    """QR code generation returns a valid PNG BytesIO."""
    from bot_xui.vpn_factory import make_qr_bytes
    bio = make_qr_bytes("https://example.com")
    assert isinstance(bio, BytesIO)
    header = bio.read(4)
    assert header == b'\x89PNG'


# ═════════════════════════════════════════════
#  create_vless_config
# ═════════════════════════════════════════════

class TestCreateVlessConfig:

    @pytest.mark.asyncio
    @patch("bot_xui.vpn_factory.generate_vless_link", return_value="vless://fake")
    async def test_success(self, mock_gen_link):
        """Creates VLESS client via XUI and returns config dict."""
        from bot_xui.vpn_factory import create_vless_config

        xui = MagicMock()
        xui.add_client.return_value = True

        result = await create_vless_config(tg_id=12345, xui=xui)

        assert result["client_email"] == "tiin_12345"
        assert len(result["client_uuid"]) == 36  # UUID format
        assert result["vless_link"] == "vless://fake"
        assert isinstance(result["expires_at"], datetime)
        xui.add_client.assert_called_once()

    @pytest.mark.asyncio
    async def test_xui_failure_raises(self):
        """XUI returning False raises RuntimeError."""
        from bot_xui.vpn_factory import create_vless_config

        xui = MagicMock()
        xui.add_client.return_value = False

        with pytest.raises(RuntimeError, match="Не удалось"):
            await create_vless_config(tg_id=99, xui=xui)


# ═════════════════════════════════════════════
#  create_awg_config
# ═════════════════════════════════════════════

class TestCreateAwgConfig:

    @pytest.mark.asyncio
    async def test_success(self):
        """AWG config creation calls the API and returns config dict."""
        from bot_xui.vpn_factory import create_awg_config
        import httpx

        fixed_name = "awg-test-fixed"

        async def mock_handler(request: httpx.Request):
            if "/api/session" in str(request.url):
                return httpx.Response(200, json={"ok": True})
            if request.method == "POST" and "/api/wireguard/client" in str(request.url):
                return httpx.Response(200, json={"ok": True})
            if request.method == "GET" and str(request.url).endswith("/api/wireguard/client"):
                return httpx.Response(200, json=[
                    {"name": fixed_name, "id": "cid-1", "address": "10.0.0.5"}
                ])
            if "/configuration" in str(request.url):
                return httpx.Response(200, text="[Interface]\nPrivateKey=abc\n[Peer]\nEndpoint=1.2.3.4")
            return httpx.Response(404)

        transport = httpx.MockTransport(mock_handler)
        mock_client = httpx.AsyncClient(transport=transport)

        with patch("bot_xui.vpn_factory.httpx.AsyncClient", return_value=mock_client):
            result = await create_awg_config(555, client_name=fixed_name)

        assert result["client_id"] == "cid-1"
        assert result["client_ip"] == "10.0.0.5"
        assert "[Interface]" in result["config"]

    @pytest.mark.asyncio
    async def test_client_not_found_raises(self):
        """Raises RuntimeError if client not found after creation."""
        from bot_xui.vpn_factory import create_awg_config
        import httpx

        async def mock_handler(request: httpx.Request):
            if "/api/session" in str(request.url):
                return httpx.Response(200, json={"ok": True})
            if request.method == "POST":
                return httpx.Response(200, json={"ok": True})
            if request.method == "GET" and str(request.url).endswith("/api/wireguard/client"):
                return httpx.Response(200, json=[])  # empty — client not found
            return httpx.Response(404)

        transport = httpx.MockTransport(mock_handler)
        mock_client = httpx.AsyncClient(transport=transport)

        with patch("bot_xui.vpn_factory.httpx.AsyncClient", return_value=mock_client):
            with pytest.raises(RuntimeError, match="не найден"):
                await create_awg_config(555)


# ═════════════════════════════════════════════
#  grant_referral_vpn
# ═════════════════════════════════════════════

class TestGrantReferralVpn:

    @pytest.mark.asyncio
    @patch("bot_xui.vpn_factory.sync_expiry")
    async def test_extend_existing(self, mock_sync):
        """Extends existing client's expiry when they already have a config."""
        from bot_xui.vpn_factory import grant_referral_vpn

        xui = MagicMock()
        xui.get_client_by_tg_id.return_value = {
            "inbound_id": 1, "client": {"email": "tiin_100"}
        }
        new_ms = int((datetime.now(timezone.utc) + timedelta(days=10)).timestamp() * 1000)
        xui.extend_client_expiry.return_value = new_ms

        result = await grant_referral_vpn(tg_id=100, days=7, xui=xui)

        assert result["action"] == "extended"
        assert result["days"] == 7
        xui.add_client.assert_not_called()
        mock_sync.assert_called_once()

    @pytest.mark.asyncio
    @patch("bot_xui.vpn_factory.sync_expiry")
    @patch("bot_xui.vpn_factory.create_vpn_key")
    @patch("bot_xui.vpn_factory.generate_vless_link", return_value="vless://ref")
    async def test_create_new(self, mock_gen, mock_create_key, mock_sync):
        """Creates new VLESS config when user has no existing config."""
        from bot_xui.vpn_factory import grant_referral_vpn

        xui = MagicMock()
        xui.get_client_by_tg_id.return_value = None
        xui.add_client.return_value = True
        xui.get_client_subscription_url.return_value = "https://sub/url"

        result = await grant_referral_vpn(tg_id=200, days=3, xui=xui)

        assert result["action"] == "created"
        assert result["vless_link"] == "vless://ref"
        xui.add_client.assert_called_once()
        mock_create_key.assert_called_once()

    @pytest.mark.asyncio
    async def test_xui_add_fails_returns_none(self):
        """Returns None if XUI add_client fails."""
        from bot_xui.vpn_factory import grant_referral_vpn

        xui = MagicMock()
        xui.get_client_by_tg_id.return_value = None
        xui.add_client.return_value = False

        result = await grant_referral_vpn(tg_id=300, days=5, xui=xui)
        assert result is None

    @pytest.mark.asyncio
    async def test_exception_returns_none(self):
        """Returns None on unexpected error (doesn't crash)."""
        from bot_xui.vpn_factory import grant_referral_vpn

        xui = MagicMock()
        xui.get_client_by_tg_id.side_effect = ConnectionError("panel down")

        result = await grant_referral_vpn(tg_id=400, days=5, xui=xui)
        assert result is None


# ═════════════════════════════════════════════
#  handle_test_vless
# ═════════════════════════════════════════════

class TestHandleTestVless:

    @pytest.mark.asyncio
    @patch("bot_xui.vpn_factory.get_web_token", return_value="tok123")
    @patch("bot_xui.vpn_factory.set_vless_test_activated")
    @patch("bot_xui.vpn_factory.create_vpn_key")
    @patch("bot_xui.vpn_factory.make_qr_bytes", return_value=BytesIO(b"png"))
    @patch("bot_xui.vpn_factory.create_vless_config")
    @patch("bot_xui.vpn_factory.is_vless_test_activated", return_value=False)
    async def test_success(self, mock_is_act, mock_create, mock_qr,
                           mock_key, mock_set_act, mock_token):
        """Happy path: config created, QR sent, flag set."""
        from bot_xui.vpn_factory import handle_test_vless

        mock_create.return_value = {
            "client_email": "tiin_111", "client_uuid": "uuid-1",
            "vless_link": "vless://test", "expires_at": datetime.now(timezone.utc),
        }

        query = MagicMock()
        query.from_user.id = 111
        query.edit_message_text = AsyncMock()
        query.message.reply_photo = AsyncMock()

        xui = MagicMock()
        xui.get_client_subscription_url.return_value = "https://sub/111"

        await handle_test_vless(query, xui)

        mock_create.assert_called_once_with(111, xui)
        mock_key.assert_called_once()
        mock_set_act.assert_called_once_with(111)
        query.message.reply_photo.assert_called_once()

    @pytest.mark.asyncio
    @patch("bot_xui.views.show_configs", new_callable=AsyncMock)
    @patch("bot_xui.vpn_factory.is_vless_test_activated", return_value=True)
    async def test_already_activated_shows_configs(self, mock_is_act, mock_show):
        """If already activated, redirects to show_configs."""
        from bot_xui.vpn_factory import handle_test_vless

        query = MagicMock()
        query.from_user.id = 222
        xui = MagicMock()

        await handle_test_vless(query, xui)

        mock_show.assert_called_once_with(query, xui)

    @pytest.mark.asyncio
    @patch("bot_xui.vpn_factory.create_vless_config", side_effect=RuntimeError("XUI down"))
    @patch("bot_xui.vpn_factory.is_vless_test_activated", return_value=False)
    async def test_error_shows_message(self, mock_is_act, mock_create):
        """Error during creation shows error message to user."""
        from bot_xui.vpn_factory import handle_test_vless

        query = MagicMock()
        query.from_user.id = 333
        query.edit_message_text = AsyncMock()
        query.message.reply_text = AsyncMock()

        xui = MagicMock()
        await handle_test_vless(query, xui)

        query.message.reply_text.assert_called_once()
        assert "Ошибка" in query.message.reply_text.call_args[0][0]


# ═════════════════════════════════════════════
#  handle_test_awg
# ═════════════════════════════════════════════

class TestHandleTestAwg:

    @pytest.mark.asyncio
    @patch("bot_xui.vpn_factory.set_awg_test_activated")
    @patch("bot_xui.vpn_factory.create_vpn_key")
    @patch("bot_xui.vpn_factory.create_awg_config")
    @patch("bot_xui.vpn_factory.is_awg_test_activated", return_value=False)
    async def test_success(self, mock_is_act, mock_create, mock_key, mock_set_act):
        """Happy path: AWG config created, file sent, flag set."""
        from bot_xui.vpn_factory import handle_test_awg

        mock_create.return_value = {
            "client_name": "test-444-abc", "client_id": "cid-1",
            "client_ip": "10.0.0.5", "config": "[Interface]\nPrivateKey=abc",
        }

        query = MagicMock()
        query.from_user.id = 444
        query.edit_message_text = AsyncMock()
        query.message.reply_document = AsyncMock()

        xui = MagicMock()
        await handle_test_awg(query, xui)

        mock_create.assert_called_once_with(444)
        mock_key.assert_called_once()
        mock_set_act.assert_called_once_with(444)
        query.message.reply_document.assert_called_once()

    @pytest.mark.asyncio
    @patch("bot_xui.vpn_factory.create_awg_config", side_effect=RuntimeError("API down"))
    @patch("bot_xui.vpn_factory.is_awg_test_activated", return_value=False)
    async def test_error_shows_fallback(self, mock_is_act, mock_create):
        """Error shows fallback message suggesting VLESS."""
        from bot_xui.vpn_factory import handle_test_awg

        query = MagicMock()
        query.from_user.id = 555
        query.edit_message_text = AsyncMock()
        query.message.reply_text = AsyncMock()

        xui = MagicMock()
        await handle_test_awg(query, xui)

        query.message.reply_text.assert_called_once()
        assert "Ошибка" in query.message.reply_text.call_args[0][0]


# ═════════════════════════════════════════════
#  create_softether_config
# ═════════════════════════════════════════════

class TestCreateSoftEtherConfig:

    @patch("bot_xui.vpn_factory.softether")
    def test_success(self, mock_se):
        """Creates SoftEther user and returns config dict."""
        from bot_xui.vpn_factory import create_softether_config

        mock_se.create_user.return_value = True
        mock_se.set_user_expiry.return_value = True

        result = create_softether_config(tg_id=600, days=30)

        assert result["username"].startswith("se_600_")
        assert len(result["password"]) == 16  # hex(8)
        assert "host" in result["config"]
        assert result["vpn_file"]  # non-empty
        mock_se.create_user.assert_called_once()
        mock_se.set_user_expiry.assert_called_once()

    @patch("bot_xui.vpn_factory.softether")
    def test_create_fails_raises(self, mock_se):
        """Raises RuntimeError if SoftEther user creation fails."""
        from bot_xui.vpn_factory import create_softether_config

        mock_se.create_user.return_value = False

        with pytest.raises(RuntimeError, match="Failed to create"):
            create_softether_config(tg_id=700)

    @patch("bot_xui.vpn_factory.softether")
    def test_expiry_fails_cleans_up(self, mock_se):
        """If expiry setting fails, user is deleted and error raised."""
        from bot_xui.vpn_factory import create_softether_config

        mock_se.create_user.return_value = True
        mock_se.set_user_expiry.return_value = False

        with pytest.raises(RuntimeError, match="Failed to set"):
            create_softether_config(tg_id=800)

        mock_se.delete_user.assert_called_once()
