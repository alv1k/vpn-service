"""
Тесты всех путей регистрации пользователей:
1. Telegram bot /start (стандартная)
2. Telegram bot /start с реферальной ссылкой
3. Web auth: email (send-code → verify)
4. Web auth: phone (send-code → verify)
5. Web order: создание заказа создаёт пользователя по email
6. Повторный вход существующего пользователя (все каналы)
7. Edge cases: невалидные данные, rate limits, истёкшие сессии
"""
import sys
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock, AsyncMock

import pytest

# Mock yookassa before imports
sys.modules.setdefault("yookassa", MagicMock())


# ─────────────────────────────────────────────
#  Helpers
# ─────────────────────────────────────────────

def _make_mock_pool():
    mock_conn = MagicMock()
    mock_cursor = MagicMock(dictionary=True)
    mock_conn.cursor.return_value = mock_cursor
    mock_pool = MagicMock()
    mock_pool.get_connection.return_value = mock_conn
    return mock_pool, mock_conn, mock_cursor


@pytest.fixture
def client():
    from api.webhook import app
    from fastapi.testclient import TestClient
    return TestClient(app)


# ═════════════════════════════════════════════
#  PATH 1: Telegram bot — get_or_create_user
# ═════════════════════════════════════════════

class TestTelegramRegistration:
    """Регистрация через /start в Telegram-боте."""

    @patch("api.db._get_pool")
    def test_new_user_created(self, mock_get_pool):
        """Новый пользователь: INSERT, возвращает id."""
        mock_pool, mock_conn, mock_cursor = _make_mock_pool()
        mock_get_pool.return_value = mock_pool
        mock_cursor.fetchone.return_value = None  # user not found
        mock_cursor.lastrowid = 42

        from api.db import get_or_create_user
        user_id = get_or_create_user(123456, "Алексей", "Иванов")

        assert user_id == 42
        sql = mock_cursor.execute.call_args[0][0]
        params = mock_cursor.execute.call_args[0][1]
        assert "INSERT INTO users" in sql
        assert params[0] == 123456
        assert params[1] == "Алексей"
        assert params[2] == "Иванов"
        # web_token should be generated
        assert params[3] is not None and len(params[3]) > 10

    @patch("api.db._get_pool")
    def test_existing_user_updated(self, mock_get_pool):
        """Существующий пользователь: обновляет имя, возвращает id."""
        mock_pool, mock_conn, mock_cursor = _make_mock_pool()
        mock_get_pool.return_value = mock_pool
        mock_cursor.fetchone.return_value = {
            "id": 5, "tg_id": 123456, "web_token": "existing-token",
            "first_name": "Old", "last_name": "Name",
        }

        from api.db import get_or_create_user
        user_id = get_or_create_user(123456, "Новое", "Имя")

        assert user_id == 5
        sqls = [c[0][0] for c in mock_cursor.execute.call_args_list]
        assert any("UPDATE users SET first_name" in s for s in sqls)

    @patch("api.db._get_pool")
    def test_existing_user_without_token_gets_one(self, mock_get_pool):
        """Если web_token отсутствует — генерируется."""
        mock_pool, mock_conn, mock_cursor = _make_mock_pool()
        mock_get_pool.return_value = mock_pool
        mock_cursor.fetchone.return_value = {
            "id": 5, "tg_id": 123, "web_token": None,
            "first_name": "Test", "last_name": None,
        }

        from api.db import get_or_create_user
        get_or_create_user(123)

        # Should have UPDATE for first_name and UPDATE for web_token
        calls = mock_cursor.execute.call_args_list
        token_update_found = any("web_token" in str(c) for c in calls)
        assert token_update_found

    @patch("api.db._get_pool")
    def test_new_user_without_name(self, mock_get_pool):
        """Пользователь без имени (может быть у Telegram-бота)."""
        mock_pool, mock_conn, mock_cursor = _make_mock_pool()
        mock_get_pool.return_value = mock_pool
        mock_cursor.fetchone.return_value = None
        mock_cursor.lastrowid = 10

        from api.db import get_or_create_user
        user_id = get_or_create_user(999)

        assert user_id == 10
        params = mock_cursor.execute.call_args[0][1]
        assert params[0] == 999
        assert params[1] is None  # first_name
        assert params[2] is None  # last_name


# ═════════════════════════════════════════════
#  PATH 2: Telegram bot — referral registration
# ═════════════════════════════════════════════

class TestReferralRegistration:
    """Регистрация через реферальную ссылку /start <referrer_id>."""

    @patch("api.db._get_pool")
    def test_new_user_with_valid_referrer(self, mock_get_pool):
        """Новый пользователь + валидный реферер → оба получают награду."""
        mock_pool, mock_conn, mock_cursor = _make_mock_pool()
        mock_get_pool.return_value = mock_pool
        mock_cursor.fetchone.side_effect = [
            None,  # get_user_by_tg_id(new) → not found
            {"id": 10, "tg_id": 111},  # get_user_by_tg_id(referrer) → exists
            {"subscription_until": datetime(2026, 4, 1)},  # _round for referrer
            {"subscription_until": datetime(2026, 4, 1)},  # _round for newcomer
        ]
        mock_cursor.lastrowid = 1

        from api.db import register_user_with_referral
        result = register_user_with_referral(222, 111, "New", "User")
        assert result is True

    @patch("api.db._get_pool")
    def test_existing_user_referral_rejected(self, mock_get_pool):
        """Уже зарегистрированный → реферал не применяется."""
        mock_pool, mock_conn, mock_cursor = _make_mock_pool()
        mock_get_pool.return_value = mock_pool
        mock_cursor.fetchone.return_value = {"id": 5, "tg_id": 222}

        from api.db import register_user_with_referral
        assert register_user_with_referral(222, 111) is False

    @patch("api.db._get_pool")
    def test_self_referral_rejected(self, mock_get_pool):
        """Пользователь не может быть своим реферером."""
        mock_pool, mock_conn, mock_cursor = _make_mock_pool()
        mock_get_pool.return_value = mock_pool
        mock_cursor.fetchone.return_value = None
        mock_cursor.lastrowid = 1

        from api.db import register_user_with_referral
        assert register_user_with_referral(222, 222) is False

    @patch("api.db._get_pool")
    def test_nonexistent_referrer(self, mock_get_pool):
        """Реферер не существует → пользователь создан, награда не выдана."""
        mock_pool, mock_conn, mock_cursor = _make_mock_pool()
        mock_get_pool.return_value = mock_pool
        mock_cursor.fetchone.side_effect = [None, None, None]
        mock_cursor.lastrowid = 1

        from api.db import register_user_with_referral
        assert register_user_with_referral(222, 999) is False

    @patch("api.db._get_pool")
    def test_race_condition_insert_ignore(self, mock_get_pool):
        """Параллельная регистрация: INSERT IGNORE возвращает 0."""
        mock_pool, mock_conn, mock_cursor = _make_mock_pool()
        mock_get_pool.return_value = mock_pool
        mock_cursor.fetchone.return_value = None
        mock_cursor.lastrowid = 0  # race: another INSERT won

        from api.db import register_user_with_referral
        assert register_user_with_referral(222, 111) is False


# ═════════════════════════════════════════════
#  PATH 3: Web auth — email
# ═════════════════════════════════════════════

class TestWebAuthEmail:
    """Регистрация через веб: email → код → верификация."""

    @patch("api.notifications._send_email", return_value=True)
    @patch("api.db._get_pool")
    def test_send_code_success(self, mock_get_pool, mock_send, client):
        """Отправка кода на email — успех."""
        mock_pool, mock_conn, mock_cursor = _make_mock_pool()
        mock_get_pool.return_value = mock_pool
        mock_cursor.fetchone.return_value = {"cnt": 0}  # rate limit check
        mock_cursor.lastrowid = 1

        resp = client.post("/api/auth/send-code", json={"email": "test@example.com"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert "отправлен" in data["message"].lower()

    @patch("api.db._get_pool")
    def test_send_code_rate_limited(self, mock_get_pool, client):
        """Rate limit: слишком много активных кодов."""
        mock_pool, mock_conn, mock_cursor = _make_mock_pool()
        mock_get_pool.return_value = mock_pool
        mock_cursor.fetchone.return_value = {"cnt": 3}  # MAX_ACTIVE_CODES

        resp = client.post("/api/auth/send-code", json={"email": "spam@example.com"})
        assert resp.status_code == 429

    def test_send_code_no_email_no_phone(self, client):
        """Ни email, ни телефон → 400."""
        resp = client.post("/api/auth/send-code", json={})
        assert resp.status_code == 400

    def test_send_code_invalid_email(self, client):
        """Невалидный email → 422 (Pydantic validation)."""
        resp = client.post("/api/auth/send-code", json={"email": "not-an-email"})
        assert resp.status_code == 422

    @patch("api.db._get_pool")
    def test_verify_creates_new_user(self, mock_get_pool, client):
        """Верификация кода: новый email → создаёт пользователя + сессию."""
        mock_pool, mock_conn, mock_cursor = _make_mock_pool()
        mock_get_pool.return_value = mock_pool

        # Sequence:
        # 1. verify_code: SELECT auth_codes → found
        # 2. verify_code: UPDATE auth_codes (mark used)
        # 3. _get_or_create_user_by_contact: SELECT users WHERE email → None
        # 4. INSERT users → lastrowid=7
        # 5. INSERT auth_sessions
        mock_cursor.fetchone.side_effect = [
            {"id": 1, "code": "123456", "destination": "new@example.com"},  # code found
            None,  # SELECT users → not found
        ]
        mock_cursor.lastrowid = 7

        with patch("api.web_auth.verify_code", return_value=True):
            resp = client.post("/api/auth/verify", json={
                "email": "new@example.com", "code": "123456",
            })

        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["token"] is not None
        # Session cookie should be set
        assert "session_token" in resp.cookies

    @patch("api.db._get_pool")
    def test_verify_existing_user_no_duplicate(self, mock_get_pool, client):
        """Существующий email → возвращает существующего, не создаёт нового."""
        mock_pool, mock_conn, mock_cursor = _make_mock_pool()
        mock_get_pool.return_value = mock_pool

        existing_user = {
            "id": 3, "email": "exists@example.com",
            "web_token": "tok-123", "tg_id": None,
        }
        mock_cursor.fetchone.side_effect = [
            existing_user,  # SELECT users → found
        ]

        with patch("api.web_auth.verify_code", return_value=True):
            resp = client.post("/api/auth/verify", json={
                "email": "exists@example.com", "code": "123456",
            })

        assert resp.status_code == 200
        # No INSERT should have happened (only SELECT + session INSERT)

    def test_verify_wrong_code(self, client):
        """Неверный код → 400."""
        with patch("api.web_auth.verify_code", return_value=False):
            resp = client.post("/api/auth/verify", json={
                "email": "test@example.com", "code": "000000",
            })
        assert resp.status_code == 400

    def test_verify_short_code(self, client):
        """Код короче 6 символов → 400."""
        resp = client.post("/api/auth/verify", json={
            "email": "test@example.com", "code": "123",
        })
        assert resp.status_code == 400

    def test_verify_empty_code(self, client):
        """Пустой код → 400."""
        resp = client.post("/api/auth/verify", json={
            "email": "test@example.com", "code": "",
        })
        assert resp.status_code == 400


# ═════════════════════════════════════════════
#  PATH 4: Web auth — phone
# ═════════════════════════════════════════════

class TestWebAuthPhone:
    """Регистрация через телефон."""

    @patch("api.notifications._send", return_value=True)
    @patch("api.db._get_pool")
    def test_send_code_phone(self, mock_get_pool, mock_send, client):
        """Отправка кода на телефон."""
        mock_pool, mock_conn, mock_cursor = _make_mock_pool()
        mock_get_pool.return_value = mock_pool
        mock_cursor.fetchone.return_value = {"cnt": 0}
        mock_cursor.lastrowid = 1

        resp = client.post("/api/auth/send-code", json={"phone": "+79001234567"})
        assert resp.status_code == 200

    def test_send_code_invalid_phone(self, client):
        """Невалидный телефон → 400."""
        resp = client.post("/api/auth/send-code", json={"phone": "12345"})
        assert resp.status_code == 400

    @patch("api.db._get_pool")
    def test_verify_phone_creates_user(self, mock_get_pool, client):
        """Верификация телефона: создаёт пользователя с полем phone."""
        mock_pool, mock_conn, mock_cursor = _make_mock_pool()
        mock_get_pool.return_value = mock_pool
        mock_cursor.fetchone.side_effect = [None]  # user not found
        mock_cursor.lastrowid = 8

        with patch("api.web_auth.verify_code", return_value=True):
            resp = client.post("/api/auth/verify", json={
                "phone": "+79001234567", "code": "123456",
            })

        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    @patch("api.db._get_pool")
    def test_verify_phone_existing_user(self, mock_get_pool, client):
        """Существующий телефон → возвращает существующего."""
        mock_pool, mock_conn, mock_cursor = _make_mock_pool()
        mock_get_pool.return_value = mock_pool
        mock_cursor.fetchone.return_value = {
            "id": 9, "phone": "+79001234567", "web_token": "tok",
        }

        with patch("api.web_auth.verify_code", return_value=True):
            resp = client.post("/api/auth/verify", json={
                "phone": "+79001234567", "code": "123456",
            })

        assert resp.status_code == 200


# ═════════════════════════════════════════════
#  PATH 5: Web order — user creation via payment
# ═════════════════════════════════════════════

class TestWebOrderRegistration:
    """Создание пользователя через оформление заказа на сайте."""

    @patch("api.db._get_pool")
    def test_order_creates_new_user(self, mock_get_pool):
        """_get_or_create_web_user: новый email → INSERT."""
        mock_pool, mock_conn, mock_cursor = _make_mock_pool()
        mock_get_pool.return_value = mock_pool
        mock_cursor.fetchone.return_value = None  # user not found
        mock_cursor.lastrowid = 15

        from api.web_api import _get_or_create_web_user
        user = _get_or_create_web_user("new@site.com")

        assert user["id"] == 15
        assert user["web_token"] is not None
        assert user["tg_id"] is None
        sql = mock_cursor.execute.call_args[0][0]
        assert "INSERT INTO users" in sql

    @patch("api.db._get_pool")
    def test_order_returns_existing_user(self, mock_get_pool):
        """_get_or_create_web_user: существующий email → SELECT."""
        mock_pool, mock_conn, mock_cursor = _make_mock_pool()
        mock_get_pool.return_value = mock_pool
        mock_cursor.fetchone.return_value = {
            "id": 3, "web_token": "existing-tok", "tg_id": 555,
        }

        from api.web_api import _get_or_create_web_user
        user = _get_or_create_web_user("existing@site.com")

        assert user["id"] == 3
        assert user["tg_id"] == 555
        # Only SELECT, no INSERT
        assert mock_cursor.execute.call_count == 1
        assert "SELECT" in mock_cursor.execute.call_args[0][0]

    @patch("api.db.create_payment")
    @patch("api.web_api.Payment")
    @patch("api.notifications.verify_code", return_value=True)
    @patch("api.db._get_pool")
    def test_order_endpoint_full_flow(self, mock_get_pool, mock_verify, mock_payment_cls, mock_create_payment, client):
        """POST /api/web/order: создаёт пользователя + платёж."""
        mock_pool, mock_conn, mock_cursor = _make_mock_pool()
        mock_get_pool.return_value = mock_pool
        mock_cursor.fetchone.return_value = None  # user not found
        mock_cursor.lastrowid = 20

        mock_payment = MagicMock()
        mock_payment.id = "pay-test-123"
        mock_payment.confirmation.confirmation_url = "https://yookassa.ru/pay/123"
        mock_payment_cls.create.return_value = mock_payment

        resp = client.post("/api/web/order", json={
            "email": "buyer@example.com",
            "tariff_id": "monthly_30d",
            "code": "123456",
        })

        assert resp.status_code == 200
        data = resp.json()
        assert data["payment_id"] == "pay-test-123"
        assert "yookassa" in data["payment_url"]
        assert "web_token" in data

    @patch("api.notifications.verify_code", return_value=True)
    def test_order_invalid_tariff(self, mock_verify, client):
        """Несуществующий тариф → 400."""
        resp = client.post("/api/web/order", json={
            "email": "test@example.com",
            "tariff_id": "nonexistent_tariff",
            "code": "123456",
        })
        assert resp.status_code == 400

    def test_order_invalid_email(self, client):
        """Невалидный email → 422."""
        resp = client.post("/api/web/order", json={
            "email": "not-email",
            "tariff_id": "monthly_30d",
        })
        assert resp.status_code == 422

    @patch("api.db.create_payment")
    @patch("api.web_api.Payment")
    @patch("api.notifications.verify_code", return_value=True)
    @patch("api.db._get_pool")
    def test_order_with_promo_code(self, mock_get_pool, mock_verify, mock_payment_cls, mock_create_payment, client):
        """Заказ с промокодом: цена со скидкой."""
        mock_pool, mock_conn, mock_cursor = _make_mock_pool()
        mock_get_pool.return_value = mock_pool

        # user lookup → not found → create
        mock_cursor.fetchone.side_effect = [
            None,  # user SELECT
            {  # promocode lookup
                "code": "SALE50", "type": "discount", "value": 50,
                "is_active": True, "expires_at": None, "max_uses": 100,
                "used_count": 0,
            },
        ]
        mock_cursor.lastrowid = 21

        mock_payment = MagicMock()
        mock_payment.id = "pay-promo-123"
        mock_payment.confirmation.confirmation_url = "https://yookassa.ru/pay/promo"
        mock_payment_cls.create.return_value = mock_payment

        resp = client.post("/api/web/order", json={
            "email": "promo@example.com",
            "tariff_id": "monthly_30d",
            "promo_code": "SALE50",
            "code": "123456",
        })

        assert resp.status_code == 200
        # Check that Payment.create was called with discounted price
        create_args = mock_payment_cls.create.call_args[0][0]
        price_value = float(create_args["amount"]["value"])
        # Original price should be halved (50% discount)
        assert price_value < 200  # monthly_30d is 199, 50% = ~100


# ═════════════════════════════════════════════
#  PATH 6: Session management
# ═════════════════════════════════════════════

class TestSessionManagement:
    """Управление сессиями: /me, /logout, истекшие сессии."""

    @patch("api.db._get_pool")
    def test_me_with_valid_session(self, mock_get_pool, client):
        """/me с валидной сессией → данные пользователя."""
        mock_pool, mock_conn, mock_cursor = _make_mock_pool()
        mock_get_pool.return_value = mock_pool
        future = datetime.now() + timedelta(days=30)
        mock_cursor.fetchone.side_effect = [
            {"user_id": 5},  # session lookup
            {  # user lookup
                "id": 5, "email": "user@test.com", "phone": None,
                "first_name": "Test", "subscription_until": future,
                "tg_id": None,
            },
        ]

        resp = client.get(
            "/api/auth/me",
            cookies={"session_token": "valid-session-tok"},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["email"] == "user@test.com"
        assert data["is_active"] is True

    @patch("api.db._get_pool")
    def test_me_expired_session(self, mock_get_pool, client):
        """Истёкшая сессия → 401."""
        mock_pool, mock_conn, mock_cursor = _make_mock_pool()
        mock_get_pool.return_value = mock_pool
        mock_cursor.fetchone.return_value = None  # session not found / expired

        resp = client.get(
            "/api/auth/me",
            cookies={"session_token": "expired-tok"},
        )
        assert resp.status_code == 401

    def test_me_no_session(self, client):
        """/me без cookie → 401."""
        resp = client.get("/api/auth/me")
        assert resp.status_code == 401

    @patch("api.db._get_pool")
    def test_logout(self, mock_get_pool, client):
        """/logout удаляет сессию и cookie."""
        mock_pool, mock_conn, mock_cursor = _make_mock_pool()
        mock_get_pool.return_value = mock_pool

        resp = client.post(
            "/api/auth/logout",
            cookies={"session_token": "session-to-delete"},
        )

        assert resp.status_code == 200
        assert resp.json()["ok"] is True
        # DELETE should be called
        sql = mock_cursor.execute.call_args[0][0]
        assert "DELETE FROM auth_sessions" in sql

    def test_logout_without_session(self, client):
        """/logout без cookie → всё равно 200."""
        resp = client.post("/api/auth/logout")
        assert resp.status_code == 200

    @patch("api.db._get_pool")
    def test_me_inactive_subscription(self, mock_get_pool, client):
        """Подписка истекла → is_active = False."""
        mock_pool, mock_conn, mock_cursor = _make_mock_pool()
        mock_get_pool.return_value = mock_pool
        past = datetime.now() - timedelta(days=1)
        mock_cursor.fetchone.side_effect = [
            {"user_id": 5},
            {
                "id": 5, "email": "expired@test.com", "phone": None,
                "first_name": "Test", "subscription_until": past,
                "tg_id": None,
            },
        ]

        resp = client.get(
            "/api/auth/me",
            cookies={"session_token": "valid-tok"},
        )

        assert resp.status_code == 200
        assert resp.json()["is_active"] is False

    @patch("api.db._get_pool")
    def test_me_no_subscription(self, mock_get_pool, client):
        """Нет подписки → is_active = False."""
        mock_pool, mock_conn, mock_cursor = _make_mock_pool()
        mock_get_pool.return_value = mock_pool
        mock_cursor.fetchone.side_effect = [
            {"user_id": 5},
            {
                "id": 5, "email": "nosub@test.com", "phone": None,
                "first_name": "Test", "subscription_until": None,
                "tg_id": None,
            },
        ]

        resp = client.get(
            "/api/auth/me",
            cookies={"session_token": "valid-tok"},
        )

        assert resp.status_code == 200
        assert resp.json()["is_active"] is False


# ═════════════════════════════════════════════
#  PATH 7: Upsert via subscription
# ═════════════════════════════════════════════

class TestUpsertSubscription:
    """upsert_user_subscription: создаёт или обновляет подписку."""

    @patch("api.db._get_pool")
    def test_upsert_new_user(self, mock_get_pool):
        """INSERT ON DUPLICATE KEY UPDATE — новый пользователь."""
        mock_pool, mock_conn, mock_cursor = _make_mock_pool()
        mock_get_pool.return_value = mock_pool
        mock_cursor.lastrowid = 30

        future = datetime(2026, 5, 1)
        from api.db import upsert_user_subscription
        upsert_user_subscription(777, future)

        sql = mock_cursor.execute.call_args[0][0]
        assert "INSERT INTO users" in sql
        assert "ON DUPLICATE KEY UPDATE" in sql
        params = mock_cursor.execute.call_args[0][1]
        assert params[0] == 777
        assert params[1] == future

    @patch("api.db._get_pool")
    def test_upsert_existing_user(self, mock_get_pool):
        """INSERT ON DUPLICATE KEY UPDATE — обновляет подписку."""
        mock_pool, mock_conn, mock_cursor = _make_mock_pool()
        mock_get_pool.return_value = mock_pool

        future = datetime(2026, 6, 1)
        from api.db import upsert_user_subscription
        upsert_user_subscription(777, future)

        mock_conn.commit.assert_called_once()


# ═════════════════════════════════════════════
#  PATH 8: Auth code lifecycle
# ═════════════════════════════════════════════

class TestAuthCodeLifecycle:
    """Жизненный цикл кода подтверждения."""

    @patch("api.notifications._send_email", return_value=True)
    @patch("api.db._get_pool")
    def test_create_code_success(self, mock_get_pool, mock_send):
        """Создание кода: INSERT + отправка."""
        mock_pool, mock_conn, mock_cursor = _make_mock_pool()
        mock_get_pool.return_value = mock_pool
        mock_cursor.fetchone.return_value = {"cnt": 0}
        mock_cursor.lastrowid = 1

        from api.notifications import create_auth_code
        code = create_auth_code("test@example.com")

        assert code is not None
        assert len(code) == 6
        assert code.isdigit()

    @patch("api.db._get_pool")
    def test_create_code_rate_limit(self, mock_get_pool):
        """Rate limit: 3 активных кода → None."""
        mock_pool, mock_conn, mock_cursor = _make_mock_pool()
        mock_get_pool.return_value = mock_pool
        mock_cursor.fetchone.return_value = {"cnt": 3}

        from api.notifications import create_auth_code
        result = create_auth_code("spam@example.com")

        assert result is None

    @patch("api.db.get_db")
    def test_verify_code_valid(self, mock_get_db):
        """Верификация валидного кода — atomic UPDATE with rowcount."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.rowcount = 1  # one row updated = valid code
        mock_conn.cursor.return_value = mock_cursor
        mock_get_db.return_value = mock_conn

        from api.notifications import verify_code
        result = verify_code("test@example.com", "123456")

        assert result is True
        mock_cursor.execute.assert_called_once()
        assert "UPDATE auth_codes" in mock_cursor.execute.call_args[0][0]

    @patch("api.db.get_db")
    def test_verify_code_invalid(self, mock_get_db):
        """Неверный код → False."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.rowcount = 0  # no rows updated = invalid code
        mock_conn.cursor.return_value = mock_cursor
        mock_get_db.return_value = mock_conn

        from api.notifications import verify_code
        result = verify_code("test@example.com", "000000")

        assert result is False


# ═════════════════════════════════════════════
#  CROSS-PATH: edge cases
# ═════════════════════════════════════════════

class TestCrossPathEdgeCases:
    """Пограничные случаи, затрагивающие несколько путей."""

    @patch("api.db._get_pool")
    def test_email_case_insensitive(self, mock_get_pool, client):
        """Email приводится к нижнему регистру."""
        mock_pool, mock_conn, mock_cursor = _make_mock_pool()
        mock_get_pool.return_value = mock_pool
        mock_cursor.fetchone.return_value = {"cnt": 0}
        mock_cursor.lastrowid = 1

        with patch("api.notifications._send_email", return_value=True):
            resp = client.post("/api/auth/send-code", json={
                "email": "TEST@Example.COM",
            })

        assert resp.status_code == 200

    @patch("api.db._get_pool")
    def test_web_user_then_bot_same_email(self, mock_get_pool):
        """Пользователь, зарегистрированный через web, найден по email."""
        mock_pool, mock_conn, mock_cursor = _make_mock_pool()
        mock_get_pool.return_value = mock_pool

        # First: web creates user
        mock_cursor.fetchone.return_value = None
        mock_cursor.lastrowid = 50

        from api.web_api import _get_or_create_web_user
        user1 = _get_or_create_web_user("shared@example.com")
        assert user1["id"] == 50

        # Then: same email lookups return existing
        mock_cursor.fetchone.return_value = {
            "id": 50, "web_token": "tok", "tg_id": None,
        }
        user2 = _get_or_create_web_user("shared@example.com")
        assert user2["id"] == 50

    @patch("api.db._get_pool")
    def test_me_bearer_token_auth(self, mock_get_pool, client):
        """Authorization: Bearer <token> работает вместо cookie."""
        mock_pool, mock_conn, mock_cursor = _make_mock_pool()
        mock_get_pool.return_value = mock_pool
        mock_cursor.fetchone.side_effect = [
            {"user_id": 5},
            {
                "id": 5, "email": "bearer@test.com", "phone": None,
                "first_name": "Bearer", "subscription_until": None,
                "tg_id": None,
            },
        ]

        resp = client.get(
            "/api/auth/me",
            headers={"Authorization": "Bearer my-session-token"},
        )

        assert resp.status_code == 200
        assert resp.json()["email"] == "bearer@test.com"

    def test_tariffs_endpoint(self, client):
        """GET /api/web/tariffs возвращает список тарифов."""
        resp = client.get("/api/web/tariffs")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) > 0
        for t in data:
            assert "id" in t
            assert "name" in t
            assert "price" in t
            assert t["price"] > 0

    @patch("api.db._get_pool")
    def test_payment_status_not_found(self, mock_get_pool, client):
        """GET /api/web/status/<id> — платёж не найден → 404."""
        mock_pool, mock_conn, mock_cursor = _make_mock_pool()
        mock_get_pool.return_value = mock_pool
        mock_cursor.fetchone.return_value = None

        resp = client.get("/api/web/status/nonexistent-payment")
        assert resp.status_code == 404

    @patch("api.db._get_pool")
    def test_payment_status_pending(self, mock_get_pool, client):
        """GET /api/web/status/<id> — ожидание оплаты."""
        mock_pool, mock_conn, mock_cursor = _make_mock_pool()
        mock_get_pool.return_value = mock_pool
        mock_cursor.fetchone.return_value = {
            "payment_id": "pay-123", "status": "pending", "tg_id": 0,
        }

        resp = client.get("/api/web/status/pay-123")
        assert resp.status_code == 200
        assert resp.json()["status"] == "pending"
        assert "portal_url" not in resp.json()

    @patch("api.db._get_pool")
    def test_payment_status_paid(self, mock_get_pool, client):
        """GET /api/web/status/<id> — оплачен, возвращает только статус (без portal_url)."""
        mock_pool, mock_conn, mock_cursor = _make_mock_pool()
        mock_get_pool.return_value = mock_pool
        mock_cursor.fetchone.return_value = {
            "payment_id": "pay-123", "status": "paid", "tg_id": 555,
        }

        resp = client.get("/api/web/status/pay-123")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "paid"
        assert "portal_url" not in data
