"""
Tests for trial activation endpoints:
  POST /api/web/activate-test  (activate_test)
  POST /api/web/trial          (activate_trial_by_email)
"""
import sys
from unittest.mock import patch, MagicMock, AsyncMock

import pytest
from fastapi import HTTPException

sys.modules.setdefault("yookassa", MagicMock())


@pytest.fixture
def client():
    from api.webhook import app
    from fastapi.testclient import TestClient
    return TestClient(app)


FAKE_USER = {
    "id": 10, "tg_id": 0, "web_token": "tok-abc",
    "first_name": None, "last_name": None, "email": "u@test.com",
    "subscription_until": None, "test_vless_activated": 0,
}


def _mock_cursor(rowcount=1):
    cur = MagicMock()
    cur.rowcount = rowcount
    return cur


def _mock_db(cursor):
    db = MagicMock()
    db.cursor.return_value = cursor
    return db


# ═════════════════════════════════════════════
#  POST /api/web/activate-test
# ═════════════════════════════════════════════

class TestActivateTest:

    @patch("api.web_api.process_web_referral", create=True)
    @patch("api.db.update_user_subscription_by_id")
    @patch("api.db.create_vpn_key")
    @patch("bot_xui.utils.XUIClient")
    @patch("api.db.get_db")
    @patch("api.web_api.get_user_by_web_token")
    def test_success(self, mock_get_user, mock_get_db, mock_xui_cls,
                     mock_create_key, mock_update_sub, mock_ref, client):
        """Happy path: user exists, not activated, XUI succeeds."""
        mock_get_user.return_value = FAKE_USER.copy()
        cur = _mock_cursor(rowcount=1)
        mock_get_db.return_value = _mock_db(cur)
        xui = MagicMock()
        xui.add_client.return_value = True
        xui.get_subscription_url_by_uuid.return_value = "https://sub/uuid"
        mock_xui_cls.return_value = xui

        resp = client.post("/api/web/activate-test", json={"web_token": "tok-abc"})

        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert "3 дня" in data["message"]
        xui.add_client.assert_called_once()
        mock_create_key.assert_called_once()
        mock_update_sub.assert_called_once()

    @patch("api.web_api.get_user_by_web_token")
    def test_user_not_found(self, mock_get_user, client):
        """Unknown web_token returns 404."""
        mock_get_user.return_value = None

        resp = client.post("/api/web/activate-test", json={"web_token": "bad"})

        assert resp.status_code == 404

    @patch("api.db.get_db")
    @patch("api.web_api.get_user_by_web_token")
    def test_already_activated(self, mock_get_user, mock_get_db, client):
        """Second activation attempt returns 400."""
        mock_get_user.return_value = FAKE_USER.copy()
        cur = _mock_cursor(rowcount=0)  # no rows updated = already activated
        mock_get_db.return_value = _mock_db(cur)

        resp = client.post("/api/web/activate-test", json={"web_token": "tok-abc"})

        assert resp.status_code == 400
        assert "уже" in resp.json()["detail"].lower()

    @patch("bot_xui.utils.XUIClient")
    @patch("api.db.get_db")
    @patch("api.web_api.get_user_by_web_token")
    def test_xui_failure_rollback(self, mock_get_user, mock_get_db,
                                  mock_xui_cls, client):
        """XUI failure returns 500 and resets test_vless_activated flag."""
        mock_get_user.return_value = FAKE_USER.copy()

        # First get_db call: claim flag (success)
        cur1 = _mock_cursor(rowcount=1)
        db1 = _mock_db(cur1)
        # Second get_db call: rollback
        cur2 = _mock_cursor()
        db2 = _mock_db(cur2)
        mock_get_db.side_effect = [db1, db2]

        xui = MagicMock()
        xui.add_client.return_value = False  # XUI fails
        mock_xui_cls.return_value = xui

        resp = client.post("/api/web/activate-test", json={"web_token": "tok-abc"})

        assert resp.status_code == 500
        # Verify rollback: second cursor ran UPDATE ... SET test_vless_activated = 0
        rollback_sql = cur2.execute.call_args[0][0]
        assert "test_vless_activated = 0" in rollback_sql

    @patch("bot_xui.utils.XUIClient")
    @patch("api.db.get_db")
    @patch("api.web_api.get_user_by_web_token")
    def test_xui_exception_rollback(self, mock_get_user, mock_get_db,
                                    mock_xui_cls, client):
        """XUI raising an exception also triggers rollback."""
        mock_get_user.return_value = FAKE_USER.copy()
        cur1 = _mock_cursor(rowcount=1)
        db1 = _mock_db(cur1)
        cur2 = _mock_cursor()
        db2 = _mock_db(cur2)
        mock_get_db.side_effect = [db1, db2]

        xui = MagicMock()
        xui.add_client.side_effect = ConnectionError("panel down")
        mock_xui_cls.return_value = xui

        resp = client.post("/api/web/activate-test", json={"web_token": "tok-abc"})

        assert resp.status_code == 500
        rollback_sql = cur2.execute.call_args[0][0]
        assert "test_vless_activated = 0" in rollback_sql

    @patch("api.web_api.process_web_referral", create=True)
    @patch("api.db.update_user_subscription_by_id")
    @patch("api.db.create_vpn_key")
    @patch("bot_xui.utils.XUIClient")
    @patch("api.db.get_db")
    @patch("api.web_api.get_user_by_web_token")
    def test_xui_called_with_credentials(self, mock_get_user, mock_get_db,
                                         mock_xui_cls, mock_create_key,
                                         mock_update_sub, mock_ref, client):
        """XUIClient must be instantiated with host/user/pass (the bug that broke prod)."""
        mock_get_user.return_value = FAKE_USER.copy()
        cur = _mock_cursor(rowcount=1)
        mock_get_db.return_value = _mock_db(cur)
        xui = MagicMock()
        xui.add_client.return_value = True
        xui.get_subscription_url_by_uuid.return_value = ""
        mock_xui_cls.return_value = xui

        client.post("/api/web/activate-test", json={"web_token": "tok-abc"})

        args = mock_xui_cls.call_args[0]
        assert len(args) == 3, f"XUIClient must get 3 positional args, got {len(args)}"
        assert all(a is not None for a in args), "XUIClient args must not be None"

    @patch("api.db.process_web_referral")
    @patch("api.db.update_user_subscription_by_id")
    @patch("api.db.create_vpn_key")
    @patch("bot_xui.utils.XUIClient")
    @patch("api.db.get_db")
    @patch("api.web_api.get_user_by_web_token")
    def test_referral_processed(self, mock_get_user, mock_get_db, mock_xui_cls,
                                mock_create_key, mock_update_sub,
                                mock_process_ref, client):
        """Referral code is passed through and processed on success."""
        mock_get_user.return_value = FAKE_USER.copy()
        cur = _mock_cursor(rowcount=1)
        mock_get_db.return_value = _mock_db(cur)
        xui = MagicMock()
        xui.add_client.return_value = True
        xui.get_subscription_url_by_uuid.return_value = ""
        mock_xui_cls.return_value = xui
        mock_process_ref.return_value = True

        resp = client.post("/api/web/activate-test",
                           json={"web_token": "tok-abc", "ref": "ref-tok"})

        assert resp.status_code == 200
        mock_process_ref.assert_called_once_with(FAKE_USER["id"], "ref-tok")


# ═════════════════════════════════════════════
#  POST /api/web/trial  (email flow)
# ═════════════════════════════════════════════

class TestTrialByEmail:

    @patch("api.web_api.activate_test", new_callable=AsyncMock)
    @patch("api.web_api._get_or_create_web_user")
    @patch("api.notifications.verify_code")
    def test_success(self, mock_verify, mock_get_user, mock_activate, client):
        """Happy path: valid code, user created, trial activated."""
        mock_verify.return_value = True
        mock_get_user.return_value = {"id": 20, "web_token": "w-tok", "tg_id": None}
        mock_activate.return_value = {"ok": True, "message": "Тест активирован на 3 дня"}

        resp = client.post("/api/web/trial",
                           json={"email": "a@b.com", "code": "123456"})

        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["web_token"] == "w-tok"

    @patch("api.notifications.verify_code")
    def test_invalid_code(self, mock_verify, client):
        """Wrong or expired code returns 400."""
        mock_verify.return_value = False

        resp = client.post("/api/web/trial",
                           json={"email": "a@b.com", "code": "000000"})

        assert resp.status_code == 400
        assert "код" in resp.json()["detail"].lower()

    @patch("api.web_api.activate_test", new_callable=AsyncMock)
    @patch("api.web_api._get_or_create_web_user")
    @patch("api.notifications.verify_code")
    def test_already_activated_returns_ok(self, mock_verify, mock_get_user,
                                         mock_activate, client):
        """If trial already activated, still returns 200 with web_token."""
        mock_verify.return_value = True
        mock_get_user.return_value = {"id": 20, "web_token": "w-tok", "tg_id": None}
        mock_activate.side_effect = HTTPException(status_code=400, detail="already")

        resp = client.post("/api/web/trial",
                           json={"email": "a@b.com", "code": "123456"})

        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["web_token"] == "w-tok"
        assert "уже" in data["message"]

    @patch("api.web_api.activate_test", new_callable=AsyncMock)
    @patch("api.web_api._get_or_create_web_user")
    @patch("api.notifications.verify_code")
    def test_500_propagates(self, mock_verify, mock_get_user,
                            mock_activate, client):
        """Non-400 errors from activate_test propagate as-is."""
        mock_verify.return_value = True
        mock_get_user.return_value = {"id": 20, "web_token": "w-tok", "tg_id": None}
        mock_activate.side_effect = HTTPException(status_code=500, detail="xui fail")

        resp = client.post("/api/web/trial",
                           json={"email": "a@b.com", "code": "123456"})

        assert resp.status_code == 500

    @patch("api.web_api.activate_test", new_callable=AsyncMock)
    @patch("api.web_api._get_or_create_web_user")
    @patch("api.notifications.verify_code")
    def test_email_normalized(self, mock_verify, mock_get_user,
                              mock_activate, client):
        """Email is lowered and stripped before processing."""
        mock_verify.return_value = True
        mock_get_user.return_value = {"id": 20, "web_token": "w-tok", "tg_id": None}
        mock_activate.return_value = {"ok": True, "message": "ok"}

        client.post("/api/web/trial",
                    json={"email": "  A@B.COM  ", "code": "123456"})

        mock_verify.assert_called_once_with("a@b.com", "123456")

    def test_invalid_email_rejected(self, client):
        """Malformed email is rejected by pydantic validation."""
        resp = client.post("/api/web/trial",
                           json={"email": "not-an-email", "code": "123456"})

        assert resp.status_code == 422
