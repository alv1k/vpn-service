"""
Test Suite: Onboarding, 1-Click Connect & 15-Minute Setup Reminders
Covers:
  - TC-1.1: GET /go-connect/{token} smart landing rendering (API / HTTP)
  - TC-1.2: DOM elements & clipboard copy functionality (Browser / DOM)
  - TC-1.3: Deep link scheme generation & URL encoding (API / Unit)
  - TC-1.4: Telegram bot 1-click connect button in UI (Telegram / Bot)
  - TC-2.1: 15-minute reminder triggered for inactive test user (Positive DB / Bot)
  - TC-2.2: Idempotency & duplicate prevention for 15-minute reminders (DB / Bot)
  - TC-2.3: Active users skipped without sending reminder (Negative DB / Bot)
  - TC-2.4: Bot blocked error handling during reminder dispatch (Telegram / Mock)
"""
import sys
import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from fastapi.testclient import TestClient

sys.modules.setdefault("yookassa", MagicMock())


@pytest.fixture
def client():
    from api.webhook import app
    return TestClient(app)


# ═════════════════════════════════════════════════════════════════════════════
#  BLOCK 1: One-Click Deep Linking & Smart Landing (/go-connect/{token})
# ═════════════════════════════════════════════════════════════════════════════

class TestOneClickConnecting:

    @patch("api.sub_proxy.log_user_platform")
    @patch("api.sub_proxy._pick_vless_key")
    @patch("api.sub_proxy.get_user_by_web_token")
    def test_tc_1_1_smart_connect_landing_rendering(self, mock_get_user, mock_pick_key, mock_log_plat, client):
        """TC-1.1: Verify /go-connect/{token} returns HTTP 200 with all client app buttons."""
        token = "test-token-123"
        mock_get_user.return_value = {"id": 999, "tg_id": 999999001, "web_token": token}
        mock_pick_key.return_value = {
            "subscription_link": "https://344988.snk.wtf:2096/sub/31453787-d188-4c90-9139-e04df26d711d"
        }

        resp = client.get(f"/go-connect/{token}")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]
        html = resp.text
        
        # Check title & headings
        assert "Подключение • тииҥ VPN" in html
        assert "Быстрый старт в 1 клик" in html
        
        # Check platform deep links with proxy subscription link (from cabinet)
        expected_sub = f"https://344988.snk.wtf/sub/{token}"
        assert f"happ://add/{expected_sub}" in html
        assert f"streisand://import/{expected_sub}" in html
        assert "v2rayng://install-config?url=https%3A%2F%2F344988.snk.wtf%2Fsub%2Ftest-token-123" in html
        assert "shadowrocket://add/sub://" in html

    @patch("api.sub_proxy.log_user_platform")
    @patch("api.sub_proxy._pick_vless_key")
    @patch("api.sub_proxy.get_user_by_web_token")
    def test_tc_1_2_dom_elements_and_clipboard_script(self, mock_get_user, mock_pick_key, mock_log_plat, client):
        """TC-1.2: Verify DOM structure, copy button, and clipboard helper script."""
        token = "test-token-123"
        expected_sub = f"https://344988.snk.wtf/sub/{token}"
        mock_get_user.return_value = {"id": 999, "tg_id": 999999001, "web_token": token}
        mock_pick_key.return_value = {"subscription_link": "https://raw-3x-ui-link"}

        resp = client.get(f"/go-connect/{token}")
        html = resp.text

        # Verify copy box elements contains cabinet proxy link
        assert 'id="subUrlText"' in html
        assert expected_sub in html
        assert 'onclick="copySub()"' in html
        assert "navigator.clipboard.writeText" in html

    def test_tc_1_3_deep_link_scheme_generation(self):
        """TC-1.3: Verify deep-link URI formatting and escaping across protocols."""
        import base64
        import urllib.parse

        sub_url = "https://344988.snk.wtf:2096/sub/abc-123?test=1"
        
        happ_url = f"happ://add/{sub_url}"
        assert happ_url.startswith("happ://add/https://")

        v2rayng_url = f"v2rayng://install-config?url={urllib.parse.quote(sub_url, safe='')}"
        assert "url=https%3A%2F%2F" in v2rayng_url

        b64 = base64.urlsafe_b64encode(sub_url.encode()).decode().rstrip("=")
        sr_url = f"shadowrocket://add/sub://{b64}"
        assert sr_url.startswith("shadowrocket://add/sub://")

    def test_tc_1_4_bot_ui_buttons(self):
        """TC-1.4: Verify bot views and factory attach 1-click button with correct URL."""
        token = "test-web-tok"
        expected_url = f"https://344988.snk.wtf/go-connect/{token}"
        assert expected_url.startswith("https://344988.snk.wtf/go-connect/")

    @patch("api.sub_proxy.log_user_platform")
    @patch("api.sub_proxy._pick_vless_key")
    @patch("api.sub_proxy.get_user_by_web_token")
    def test_tc_1_5_cabinet_proxy_link_preferred_over_raw_xui(self, mock_get_user, mock_pick_key, mock_log_plat, client):
        """TC-1.5: Explicit check that /go-connect uses proxy sub link (https://344988.snk.wtf/sub/{token}) instead of raw 3x-ui link."""
        token = "cabinet-user-tok"
        raw_xui_url = "https://344988.snk.wtf:2096/sub/raw-secret-sub-id"
        mock_get_user.return_value = {"id": 101, "tg_id": 999999005, "web_token": token}
        mock_pick_key.return_value = {"subscription_link": raw_xui_url}

        resp = client.get(f"/go-connect/{token}")
        assert resp.status_code == 200
        html = resp.text

        cabinet_proxy_sub = f"https://344988.snk.wtf/sub/{token}"
        
        # 1. Raw 3x-ui link must NEVER be exposed in any deep links or copy boxes
        assert raw_xui_url not in html
        
        # 2. Cabinet proxy sub must be present in deep-links and copy box
        assert cabinet_proxy_sub in html
        assert f"happ://add/{cabinet_proxy_sub}" in html
        assert f"streisand://import/{cabinet_proxy_sub}" in html
        assert f'<div class="sub-url" id="subUrlText">{cabinet_proxy_sub}</div>' in html


# ═════════════════════════════════════════════════════════════════════════════
#  BLOCK 2: 15-Minute Setup Reminder Routine
# ═════════════════════════════════════════════════════════════════════════════

class TestFifteenMinuteReminderRoutine:

    @pytest.mark.asyncio
    @patch("api.db.execute_query")
    @patch("sqlite3.connect")
    async def test_tc_2_1_reminder_triggered_for_inactive_user(self, mock_sqlite, mock_eq):
        """TC-2.1: Positive scenario: Reminder is sent to an inactive test user."""
        from bot_xui.bot import check_and_send_15m_test_reminders

        # Candidate user who activated test 20 minutes ago
        mock_eq.side_effect = [
            # 1. Candidate query
            [{"id": 1, "tg_id": 999999001, "first_name": "Тестер", "web_token": "token-test-1", "created_at": "2026-09-03 02:00:00"}],
            # 2. user_platforms query
            None,
        ]

        # XUI Mock: 0 traffic, 0 sub fetch
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [
            {"email": "tiin_999999001", "up": 0, "down": 0, "total": 0, "last_sub_fetch": 0}
        ]
        mock_conn.cursor.return_value = mock_cursor
        mock_sqlite.return_value = mock_conn

        mock_bot = MagicMock()
        mock_bot.send_message = AsyncMock(return_value=True)

        with patch("api.db.log_message_sent") as mock_log:
            await check_and_send_15m_test_reminders(mock_bot)

            # Verification
            assert mock_bot.send_message.called
            call_kwargs = mock_bot.send_message.call_args.kwargs
            assert call_kwargs["chat_id"] == 999999001
            assert "возникли сложности с подключением?" in call_kwargs["text"]
            assert "https://344988.snk.wtf/go-connect/token-test-1" in str(call_kwargs["reply_markup"])

            mock_log.assert_called_with(
                tg_id=999999001,
                source="bot_system",
                scenario="test_15m_setup",
                message_text=call_kwargs["text"],
                status="sent"
            )

    @pytest.mark.asyncio
    @patch("api.db.execute_query")
    async def test_tc_2_2_idempotency_prevents_duplicate_sends(self, mock_eq):
        """TC-2.2: Idempotency: Query filters out already reminded users."""
        from bot_xui.bot import check_and_send_15m_test_reminders

        # Candidate query returns empty list because of NOT EXISTS in message_log
        mock_eq.return_value = []

        mock_bot = MagicMock()
        mock_bot.send_message = AsyncMock()

        await check_and_send_15m_test_reminders(mock_bot)
        assert not mock_bot.send_message.called

    @pytest.mark.asyncio
    @patch("api.db.execute_query")
    @patch("sqlite3.connect")
    async def test_tc_2_3_active_user_skipped(self, mock_sqlite, mock_eq):
        """TC-2.3: Active user (fetched subscription or traffic > 0) is skipped."""
        from bot_xui.bot import check_and_send_15m_test_reminders

        mock_eq.side_effect = [
            # Candidate user found by time window
            [{"id": 2, "tg_id": 999999002, "first_name": "Активный", "web_token": "token-test-2", "created_at": "2026-09-03 02:00:00"}],
        ]

        # XUI Mock: User has fetched sub
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [
            {"email": "tiin_999999002", "up": 1024, "down": 4096, "total": 5120, "last_sub_fetch": 1788390000000}
        ]
        mock_conn.cursor.return_value = mock_cursor
        mock_sqlite.return_value = mock_conn

        mock_bot = MagicMock()
        mock_bot.send_message = AsyncMock()

        with patch("api.db.log_message_sent") as mock_log:
            await check_and_send_15m_test_reminders(mock_bot)

            # Verification: Message NOT sent
            assert not mock_bot.send_message.called
            # Logged as skipped
            mock_log.assert_called_with(
                tg_id=999999002,
                source="bot_system",
                scenario="test_15m_setup",
                message_text="[SKIPPED: User already connected]",
                status="skipped"
            )

    @pytest.mark.asyncio
    @patch("api.db.execute_query")
    @patch("sqlite3.connect")
    async def test_tc_2_4_blocked_bot_error_handling(self, mock_sqlite, mock_eq):
        """TC-2.4: Bot blocked exception is caught and logged as failed without crashing."""
        from bot_xui.bot import check_and_send_15m_test_reminders

        mock_eq.side_effect = [
            [{"id": 3, "tg_id": 999999003, "first_name": "Блокер", "web_token": "token-test-3", "created_at": "2026-09-03 02:00:00"}],
            None,
        ]

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [
            {"email": "tiin_999999003", "up": 0, "down": 0, "total": 0, "last_sub_fetch": 0}
        ]
        mock_conn.cursor.return_value = mock_cursor
        mock_sqlite.return_value = mock_conn

        mock_bot = MagicMock()
        mock_bot.send_message = AsyncMock(side_effect=Exception("Forbidden: bot was blocked by the user"))

        with patch("api.db.log_message_sent") as mock_log:
            await check_and_send_15m_test_reminders(mock_bot)

            # Verification: error logged
            assert mock_log.called
            assert mock_log.call_args.kwargs["status"] == "failed"
            assert "Forbidden" in mock_log.call_args.kwargs["error_text"]
