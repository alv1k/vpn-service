"""
Тесты выдачи AWG конфига через бота:
1. show_configs — отображение AWG ключей в списке
2. show_single_config — отправка .conf файла для AWG
3. handle_get_awg_config — создание нового AWG конфига
4. handle_get_awg_config — edge cases (уже есть, нет подписки, ошибка API)
"""
import sys
from datetime import datetime, timedelta
from io import BytesIO
from unittest.mock import patch, MagicMock, AsyncMock, ANY

import pytest

# Mock external modules before imports
sys.modules.setdefault("yookassa", MagicMock())


# ─────────────────────────────────────────────
#  Helpers
# ─────────────────────────────────────────────

def _make_query(tg_id=123456):
    """Create a mock CallbackQuery with common attributes."""
    query = AsyncMock()
    query.from_user = MagicMock()
    query.from_user.id = tg_id
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()
    query.message = AsyncMock()
    query.message.delete = AsyncMock()
    query.message.chat = AsyncMock()
    query.message.chat.send_document = AsyncMock()
    query.message.chat.send_message = AsyncMock()
    query.message.chat.send_photo = AsyncMock()
    query.message.reply_document = AsyncMock()
    query.message.reply_text = AsyncMock()
    return query


def _make_awg_key(
    client_name="awg_123456",
    expires_at=None,
    conf_text="[Interface]\nPrivateKey = abc\nAddress = 10.10.0.4/32\n\n[Peer]\nPublicKey = xyz\nEndpoint = 1.2.3.4:443\n",
    client_id="uuid-awg-1",
):
    if expires_at is None:
        expires_at = datetime.utcnow() + timedelta(days=30)
    return {
        "client_name": client_name,
        "vpn_type": "awg",
        "expires_at": expires_at,
        "vless_link": conf_text,
        "subscription_link": None,
        "client_id": client_id,
        "vpn_file": None,
    }


def _make_vless_key(
    client_name="tiin_123456",
    expires_at=None,
):
    if expires_at is None:
        expires_at = datetime.utcnow() + timedelta(days=30)
    return {
        "client_name": client_name,
        "vpn_type": "vless",
        "expires_at": expires_at,
        "vless_link": "vless://uuid@host:443",
        "subscription_link": "https://example.com/sub/abc",
        "client_id": None,
        "vpn_file": None,
    }


# ═════════════════════════════════════════════
#  show_configs — AWG keys in config list
# ═════════════════════════════════════════════

class TestShowConfigsAWG:
    """Проверка отображения AWG ключей в списке конфигов."""

    @patch("bot_xui.views.safe_edit_text", new_callable=AsyncMock)
    @patch("bot_xui.views.get_keys_by_tg_id")
    @pytest.mark.asyncio
    async def test_awg_key_shown_in_list(self, mock_keys, mock_edit):
        """AWG ключ отображается с эмодзи 📱 в списке."""
        from bot_xui.views import show_configs

        mock_keys.return_value = [_make_awg_key()]
        query = _make_query()

        await show_configs(query)

        mock_edit.assert_called_once()
        text = mock_edit.call_args[0][1]
        assert "📱" in text
        assert "awg_123456" in text

    @patch("bot_xui.views.safe_edit_text", new_callable=AsyncMock)
    @patch("bot_xui.views.get_keys_by_tg_id")
    @pytest.mark.asyncio
    async def test_awg_and_vless_shown_together(self, mock_keys, mock_edit):
        """AWG и VLESS ключи отображаются вместе."""
        from bot_xui.views import show_configs

        mock_keys.return_value = [_make_vless_key(), _make_awg_key()]
        query = _make_query()

        await show_configs(query)

        text = mock_edit.call_args[0][1]
        assert "awg_123456" in text
        assert "tiin_123456" in text

    @patch("bot_xui.views.safe_edit_text", new_callable=AsyncMock)
    @patch("bot_xui.views.get_keys_by_tg_id")
    @pytest.mark.asyncio
    async def test_no_add_awg_button_when_awg_exists(self, mock_keys, mock_edit):
        """Кнопка '➕ AmneziaWG' не показывается если AWG ключ уже есть."""
        from bot_xui.views import show_configs

        mock_keys.return_value = [_make_vless_key(), _make_awg_key()]
        query = _make_query()

        await show_configs(query)

        markup = mock_edit.call_args[1]["reply_markup"]
        all_buttons = [btn.text for row in markup.inline_keyboard for btn in row]
        assert "➕ AmneziaWG" not in all_buttons

    @patch("bot_xui.views.safe_edit_text", new_callable=AsyncMock)
    @patch("bot_xui.views.get_keys_by_tg_id")
    @pytest.mark.asyncio
    async def test_add_awg_button_when_no_awg(self, mock_keys, mock_edit):
        """Кнопка '➕ AmneziaWG' показывается если AWG ключа нет."""
        from bot_xui.views import show_configs

        mock_keys.return_value = [_make_vless_key()]
        query = _make_query()

        await show_configs(query)

        markup = mock_edit.call_args[1]["reply_markup"]
        all_buttons = [btn.text for row in markup.inline_keyboard for btn in row]
        assert "➕ AmneziaWG" in all_buttons


# ═════════════════════════════════════════════
#  show_single_config — AWG .conf file delivery
# ═════════════════════════════════════════════

class TestShowSingleConfigAWG:
    """Проверка отправки AWG .conf файла при нажатии на конфиг."""

    @patch("bot_xui.views.get_keys_by_tg_id")
    @pytest.mark.asyncio
    async def test_awg_sends_conf_document(self, mock_keys):
        """AWG конфиг отправляется как .conf документ."""
        from bot_xui.views import show_single_config

        awg_key = _make_awg_key()
        mock_keys.return_value = [awg_key]
        query = _make_query()

        await show_single_config(query, "awg_123456", xui=None)

        query.message.delete.assert_called_once()
        query.message.chat.send_document.assert_called_once()
        call_kwargs = query.message.chat.send_document.call_args[1]
        doc = call_kwargs["document"]
        assert doc.name.endswith(".conf")
        assert "AmneziaVPN" in call_kwargs["caption"]

    @patch("bot_xui.views.get_keys_by_tg_id")
    @pytest.mark.asyncio
    async def test_awg_conf_contains_interface_section(self, mock_keys):
        """Отправленный .conf содержит секцию [Interface]."""
        from bot_xui.views import show_single_config

        awg_key = _make_awg_key()
        mock_keys.return_value = [awg_key]
        query = _make_query()

        await show_single_config(query, "awg_123456", xui=None)

        doc = query.message.chat.send_document.call_args[1]["document"]
        content = doc.read().decode("utf-8")
        assert "[Interface]" in content
        assert "[Peer]" in content

    @patch("bot_xui.views.get_keys_by_tg_id")
    @pytest.mark.asyncio
    async def test_awg_empty_config_shows_error(self, mock_keys):
        """Пустой конфиг AWG — показывает ошибку."""
        from bot_xui.views import show_single_config

        awg_key = _make_awg_key(conf_text="")
        mock_keys.return_value = [awg_key]
        query = _make_query()

        await show_single_config(query, "awg_123456", xui=None)

        query.answer.assert_called_once()
        assert "не найден" in query.answer.call_args[0][0]

    @patch("bot_xui.views.get_keys_by_tg_id")
    @pytest.mark.asyncio
    async def test_awg_key_not_found(self, mock_keys):
        """Несуществующий AWG ключ — ошибка."""
        from bot_xui.views import show_single_config

        mock_keys.return_value = [_make_vless_key()]
        query = _make_query()

        await show_single_config(query, "awg_nonexistent", xui=None)

        query.answer.assert_called_once()
        assert "не найден" in query.answer.call_args[0][0]

    @patch("bot_xui.views.get_keys_by_tg_id")
    @pytest.mark.asyncio
    async def test_awg_expired_key_shows_status(self, mock_keys):
        """Истекший AWG ключ — показывает статус Истек."""
        from bot_xui.views import show_single_config

        expired = datetime.utcnow() - timedelta(days=1)
        awg_key = _make_awg_key(expires_at=expired)
        mock_keys.return_value = [awg_key]
        query = _make_query()

        await show_single_config(query, "awg_123456", xui=None)

        caption = query.message.chat.send_document.call_args[1]["caption"]
        assert "❌" in caption


# ═════════════════════════════════════════════
#  handle_get_awg_config — creation flow
# ═════════════════════════════════════════════

class TestHandleGetAWGConfig:
    """Тесты создания AWG конфига через handle_get_awg_config."""

    @patch("bot_xui.vpn_factory.create_vpn_key")
    @patch("bot_xui.vpn_factory.get_subscription_until")
    @patch("bot_xui.vpn_factory.get_keys_by_tg_id")
    @patch("bot_xui.vpn_factory.create_awg_config", new_callable=AsyncMock)
    @pytest.mark.asyncio
    async def test_creates_awg_config_success(self, mock_create, mock_keys, mock_sub, mock_save):
        """Успешное создание AWG конфига — отправляет .conf файл."""
        from bot_xui.vpn_factory import handle_get_awg_config

        sub_until = datetime.utcnow() + timedelta(days=30)
        mock_keys.return_value = [_make_vless_key()]  # no AWG
        mock_sub.return_value = sub_until
        mock_create.return_value = {
            "client_name": "awg_123456",
            "client_id": "uuid-new",
            "client_ip": "10.10.0.5",
            "config": "[Interface]\nPrivateKey=abc\n\n[Peer]\nPublicKey=xyz\n",
        }

        query = _make_query()
        await handle_get_awg_config(query)

        mock_create.assert_called_once()
        mock_save.assert_called_once()
        query.message.reply_document.assert_called_once()
        doc = query.message.reply_document.call_args[1]["document"]
        assert doc.name.endswith(".conf")

    @patch("bot_xui.vpn_factory.get_keys_by_tg_id")
    @pytest.mark.asyncio
    async def test_already_has_awg_shows_message(self, mock_keys):
        """Если AWG уже есть — сообщение без создания."""
        from bot_xui.vpn_factory import handle_get_awg_config

        mock_keys.return_value = [_make_awg_key()]
        query = _make_query()

        await handle_get_awg_config(query)

        query.edit_message_text.assert_called_once()
        text = query.edit_message_text.call_args[0][0]
        assert "уже есть" in text

    @patch("bot_xui.vpn_factory.get_subscription_until")
    @patch("bot_xui.vpn_factory.get_keys_by_tg_id")
    @pytest.mark.asyncio
    async def test_no_subscription_shows_error(self, mock_keys, mock_sub):
        """Без активной подписки — ошибка."""
        from bot_xui.vpn_factory import handle_get_awg_config

        mock_keys.return_value = [_make_vless_key()]
        mock_sub.return_value = datetime.utcnow() - timedelta(days=1)  # expired
        query = _make_query()

        await handle_get_awg_config(query)

        text = query.edit_message_text.call_args[0][0]
        assert "нет активной подписки" in text

    @patch("bot_xui.vpn_factory.get_subscription_until")
    @patch("bot_xui.vpn_factory.get_keys_by_tg_id")
    @pytest.mark.asyncio
    async def test_no_subscription_at_all(self, mock_keys, mock_sub):
        """subscription_until = None — ошибка."""
        from bot_xui.vpn_factory import handle_get_awg_config

        mock_keys.return_value = []
        mock_sub.return_value = None
        query = _make_query()

        await handle_get_awg_config(query)

        text = query.edit_message_text.call_args[0][0]
        assert "нет активной подписки" in text

    @patch("bot_xui.vpn_factory.create_vpn_key")
    @patch("bot_xui.vpn_factory.get_subscription_until")
    @patch("bot_xui.vpn_factory.get_keys_by_tg_id")
    @patch("bot_xui.vpn_factory.create_awg_config", new_callable=AsyncMock)
    @pytest.mark.asyncio
    async def test_api_error_shows_error_message(self, mock_create, mock_keys, mock_sub, mock_save):
        """Ошибка API при создании — показывает сообщение об ошибке."""
        from bot_xui.vpn_factory import handle_get_awg_config

        mock_keys.return_value = [_make_vless_key()]
        mock_sub.return_value = datetime.utcnow() + timedelta(days=30)
        mock_create.side_effect = RuntimeError("AWG API down")

        query = _make_query()
        await handle_get_awg_config(query)

        query.message.reply_text.assert_called_once()
        text = query.message.reply_text.call_args[0][0]
        assert "Ошибка" in text

    @patch("bot_xui.vpn_factory.create_vpn_key")
    @patch("bot_xui.vpn_factory.get_subscription_until")
    @patch("bot_xui.vpn_factory.get_keys_by_tg_id")
    @patch("bot_xui.vpn_factory.create_awg_config", new_callable=AsyncMock)
    @pytest.mark.asyncio
    async def test_config_saved_to_db(self, mock_create, mock_keys, mock_sub, mock_save):
        """Конфиг сохраняется в БД через create_vpn_key."""
        from bot_xui.vpn_factory import handle_get_awg_config

        sub_until = datetime.utcnow() + timedelta(days=30)
        mock_keys.return_value = []
        mock_sub.return_value = sub_until
        mock_create.return_value = {
            "client_name": "awg_123456",
            "client_id": "uuid-new",
            "client_ip": "10.10.0.5",
            "config": "[Interface]\nPrivateKey=abc\n",
        }

        query = _make_query()
        await handle_get_awg_config(query)

        mock_save.assert_called_once_with(
            tg_id=123456,
            payment_id=None,
            client_id="uuid-new",
            client_name="awg_123456",
            client_ip="10.10.0.5",
            client_public_key=None,
            vless_link="[Interface]\nPrivateKey=abc\n",
            expires_at=sub_until,
            vpn_type="awg",
        )

    @patch("bot_xui.vpn_factory.create_vpn_key")
    @patch("bot_xui.vpn_factory.get_subscription_until")
    @patch("bot_xui.vpn_factory.get_keys_by_tg_id")
    @patch("bot_xui.vpn_factory.create_awg_config", new_callable=AsyncMock)
    @pytest.mark.asyncio
    async def test_client_name_contains_tg_id(self, mock_create, mock_keys, mock_sub, mock_save):
        """Имя клиента содержит tg_id."""
        from bot_xui.vpn_factory import handle_get_awg_config

        mock_keys.return_value = []
        mock_sub.return_value = datetime.utcnow() + timedelta(days=30)
        mock_create.return_value = {
            "client_name": "awg_999",
            "client_id": "uuid",
            "client_ip": "10.10.0.6",
            "config": "[Interface]\n",
        }

        query = _make_query(tg_id=999)
        await handle_get_awg_config(query)

        call_kwargs = mock_create.call_args
        assert "999" in call_kwargs[1].get("client_name", call_kwargs[0][1] if len(call_kwargs[0]) > 1 else "")
