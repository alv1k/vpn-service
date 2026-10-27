"""Тесты для реферальной системы — регистрация, подтверждение по трафику (50 МБ), награды."""
import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from datetime import datetime


def _make_mock_pool():
    mock_conn = MagicMock()
    mock_cursor = MagicMock(dictionary=True)
    mock_conn.cursor.return_value = mock_cursor
    mock_pool = MagicMock()
    mock_pool.get_connection.return_value = mock_conn
    return mock_pool, mock_conn, mock_cursor


# ─────────────────────────────────────────────
#  1. Регистрация по реферальной ссылке
# ─────────────────────────────────────────────

class TestRegisterUserWithReferral:
    @patch("api.db._get_pool")
    def test_register_new_user_with_valid_referrer(self, mock_get_pool):
        """
        Новый пользователь по реферальной ссылке:
        - Создается пользователь в users.
        - referred_by связывается с реферером, referral_rewarded = 0 (награда на холде).
        - Новичок получает приветственные дни (reward_newcomer).
        - Реферер сразу дни НЕ получает (ждет 50 МБ трафика).
        - Функция возвращает True.
        """
        mock_pool, mock_conn, mock_cursor = _make_mock_pool()
        mock_get_pool.return_value = mock_pool

        # 1st fetchone: get_user_by_tg_id(new) -> None (новый)
        # INSERT IGNORE -> lastrowid = 1
        # 2nd fetchone: get_user_by_tg_id(referrer) -> exists
        # 3rd fetchone: _round_subscription_eod SELECT для новичка
        mock_cursor.fetchone.side_effect = [
            None,
            {"id": 10, "tg_id": 111},
            {"subscription_until": datetime(2026, 4, 1, 10, 0)},
        ]
        mock_cursor.lastrowid = 1

        from api.db import register_user_with_referral
        result = register_user_with_referral(
            new_tg_id=222, referrer_tg_id=111, first_name="Тест", last_name="Пользователь"
        )

        assert result is True

        sqls = [c[0][0] for c in mock_cursor.execute.call_args_list]

        # Проверяем, что referred_by установлен с referral_rewarded = 0
        update_ref_sql = [s for s in sqls if "UPDATE users SET referred_by" in s]
        assert len(update_ref_sql) == 1
        assert "referral_rewarded = 0" in update_ref_sql[0]

        # Проверяем, что новичок получил подписку
        newcomer_reward_sql = [s for s in sqls if "subscription_until = DATE_ADD(NOW()" in s]
        assert len(newcomer_reward_sql) == 1

        # Проверяем, что реферер НЕ получал награду на шаге регистрации
        referrer_reward_sql = [s for s in sqls if "referral_count = referral_count + 1" in s]
        assert len(referrer_reward_sql) == 0

    @patch("api.db._get_pool")
    def test_register_existing_user_returns_false(self, mock_get_pool):
        """Уже зарегистрированный пользователь не может повторно применить рефералку."""
        mock_pool, mock_conn, mock_cursor = _make_mock_pool()
        mock_get_pool.return_value = mock_pool
        mock_cursor.fetchone.return_value = {"id": 5, "tg_id": 222}

        from api.db import register_user_with_referral
        result = register_user_with_referral(new_tg_id=222, referrer_tg_id=111)

        assert result is False

    @patch("api.db._get_pool")
    def test_register_without_referrer(self, mock_get_pool):
        """Обычная регистрация без реферера создает пользователя и возвращает False."""
        mock_pool, mock_conn, mock_cursor = _make_mock_pool()
        mock_get_pool.return_value = mock_pool
        mock_cursor.fetchone.return_value = None
        mock_cursor.lastrowid = 1

        from api.db import register_user_with_referral
        result = register_user_with_referral(new_tg_id=222, referrer_tg_id=None)

        assert result is False

    @patch("api.db._get_pool")
    def test_register_self_referral_rejected(self, mock_get_pool):
        """Попытка пригласить самого себя отклоняется."""
        mock_pool, mock_conn, mock_cursor = _make_mock_pool()
        mock_get_pool.return_value = mock_pool
        mock_cursor.fetchone.return_value = None
        mock_cursor.lastrowid = 1

        from api.db import register_user_with_referral
        result = register_user_with_referral(new_tg_id=222, referrer_tg_id=222)

        assert result is False

    @patch("api.db._get_pool")
    def test_register_with_nonexistent_referrer(self, mock_get_pool):
        """Реферер не найден в базе данных."""
        mock_pool, mock_conn, mock_cursor = _make_mock_pool()
        mock_get_pool.return_value = mock_pool
        mock_cursor.fetchone.side_effect = [None, None, None]
        mock_cursor.lastrowid = 1

        from api.db import register_user_with_referral
        result = register_user_with_referral(new_tg_id=222, referrer_tg_id=999)

        assert result is False

    @patch("api.db._get_pool")
    def test_register_race_condition_insert_ignore(self, mock_get_pool):
        """Параллельный запрос: INSERT IGNORE возвращает 0 затронутых строк."""
        mock_pool, mock_conn, mock_cursor = _make_mock_pool()
        mock_get_pool.return_value = mock_pool
        mock_cursor.fetchone.return_value = None
        mock_cursor.lastrowid = 0

        from api.db import register_user_with_referral
        result = register_user_with_referral(new_tg_id=222, referrer_tg_id=111)

        assert result is False


# ─────────────────────────────────────────────
#  2. Базовые функции начисления наград
# ─────────────────────────────────────────────

class TestReferralRewards:
    @patch("api.db._get_pool")
    def test_reward_referrer_updates_subscription(self, mock_get_pool):
        """Прямой вызов reward_referrer продлевает подписку реферера и инкрементирует счетчик."""
        mock_pool, mock_conn, mock_cursor = _make_mock_pool()
        mock_get_pool.return_value = mock_pool
        mock_cursor.fetchone.return_value = {"subscription_until": datetime(2026, 4, 1, 10, 0)}

        from api.db import reward_referrer
        reward_referrer(111)

        reward_sql = mock_cursor.execute.call_args_list[0][0][0]
        assert "referral_count = referral_count + 1" in reward_sql
        assert "subscription_until = DATE_ADD" in reward_sql
        assert mock_conn.commit.call_count >= 1

    @patch("api.db._get_pool")
    def test_reward_newcomer_sets_subscription(self, mock_get_pool):
        """Прямой вызов reward_newcomer задает триал новичку."""
        mock_pool, mock_conn, mock_cursor = _make_mock_pool()
        mock_get_pool.return_value = mock_pool
        mock_cursor.fetchone.return_value = {"subscription_until": datetime(2026, 4, 1, 10, 0)}

        from api.db import reward_newcomer
        reward_newcomer(222)

        reward_sql = mock_cursor.execute.call_args_list[0][0][0]
        assert "subscription_until = DATE_ADD(NOW()" in reward_sql
        assert mock_conn.commit.call_count >= 1

    @patch("api.db._get_pool")
    def test_get_referral_count_with_referrals(self, mock_get_pool):
        """Получение числа подтвержденных рефералов."""
        mock_pool, mock_conn, mock_cursor = _make_mock_pool()
        mock_get_pool.return_value = mock_pool
        mock_cursor.fetchone.return_value = {"referral_count": 5}

        from api.db import get_referral_count
        assert get_referral_count(111) == 5

    @patch("api.db._get_pool")
    def test_get_referral_count_no_user(self, mock_get_pool):
        """Если пользователя нет в базе — возвращает 0."""
        mock_pool, mock_conn, mock_cursor = _make_mock_pool()
        mock_get_pool.return_value = mock_pool
        mock_cursor.fetchone.return_value = None

        from api.db import get_referral_count
        assert get_referral_count(999) == 0


# ─────────────────────────────────────────────
#  3. Подтверждение бонуса (confirm_referral_reward)
# ─────────────────────────────────────────────

class TestConfirmReferralReward:
    @patch("api.db._get_pool")
    def test_confirm_referral_reward_success(self, mock_get_pool):
        """Успешное подтверждение: атомарный UPDATE, реферер получает дни."""
        mock_pool, mock_conn, mock_cursor = _make_mock_pool()
        mock_get_pool.return_value = mock_pool
        mock_cursor.fetchone.side_effect = [
            {"id": 20, "tg_id": 222, "referred_by": 10, "referral_rewarded": 0},
            {"subscription_until": datetime(2026, 4, 1, 10, 0)},
            {"id": 10, "tg_id": 111},
        ]
        mock_cursor.rowcount = 1

        from api.db import confirm_referral_reward
        res = confirm_referral_reward(20)

        assert res is not None
        assert res["referrer_tg_id"] == 111
        assert res["referrer_id"] == 10
        assert any("referral_rewarded = 1" in c[0][0] for c in mock_cursor.execute.call_args_list)

    @patch("api.db._get_pool")
    def test_confirm_referral_reward_already_rewarded(self, mock_get_pool):
        """Защита от повторного начисления: если referral_rewarded=1, возвращает None."""
        mock_pool, mock_conn, mock_cursor = _make_mock_pool()
        mock_get_pool.return_value = mock_pool
        mock_cursor.fetchone.return_value = {"id": 20, "tg_id": 222, "referred_by": 10, "referral_rewarded": 1}

        from api.db import confirm_referral_reward
        res = confirm_referral_reward(20)

        assert res is None

    @patch("api.db._get_pool")
    def test_confirm_referral_reward_no_referrer(self, mock_get_pool):
        """Пользователь без реферера не может подтвердить реферальный бонус."""
        mock_pool, mock_conn, mock_cursor = _make_mock_pool()
        mock_get_pool.return_value = mock_pool
        mock_cursor.fetchone.return_value = {"id": 20, "tg_id": 222, "referred_by": None, "referral_rewarded": 0}

        from api.db import confirm_referral_reward
        res = confirm_referral_reward(20)

        assert res is None


# ─────────────────────────────────────────────
#  4. Фоновый воркер трафика (>= 50 МБ)
# ─────────────────────────────────────────────

class TestPendingReferralTrafficWorker:
    @pytest.mark.asyncio
    @patch("bot_xui.bot.send_message_by_tg_id", new_callable=AsyncMock)
    @patch("bot_xui.vpn_factory.grant_referral_vpn", new_callable=AsyncMock)
    @patch("bot_xui.bot.XUIClient")
    @patch("sqlite3.connect")
    @patch("api.db.confirm_referral_reward")
    @patch("api.db.execute_query")
    async def test_worker_triggers_when_traffic_over_50mb(
        self, mock_exec_query, mock_confirm, mock_sqlite, mock_xui_cls, mock_grant, mock_send
    ):
        """Если трафик новичка >= 50 МБ, воркер подтверждает бонус и уведомляет реферера."""
        from bot_xui.bot import check_pending_referral_rewards

        # 1. execute_query возвращает пользователя в статусе pending
        mock_exec_query.return_value = [{"id": 20, "tg_id": 222, "referred_by": 10}]

        # 2. XUI SQLite возвращает трафик 60 МБ (62 914 560 байт)
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_cur.fetchall.return_value = [
            {"email": "tiin_222", "up": 30 * 1024 * 1024, "down": 30 * 1024 * 1024}
        ]
        mock_conn.cursor.return_value = mock_cur
        mock_sqlite.return_value = mock_conn

        # 3. confirm_referral_reward успешен
        mock_confirm.return_value = {
            "newcomer_id": 20,
            "newcomer_tg_id": 222,
            "referrer_id": 10,
            "referrer_tg_id": 111,
        }

        mock_bot = AsyncMock()
        await check_pending_referral_rewards(mock_bot)

        # Проверяем, что награда подтверждена в БД
        mock_confirm.assert_called_once_with(20)
        # Проверяем, что в XUI продлен реферер
        mock_grant.assert_called_once()
        # Проверяем, что отправлен пуш в Telegram рефереру
        mock_send.assert_called_once()
        assert mock_send.call_args[0][0] == 111

    @pytest.mark.asyncio
    @patch("bot_xui.bot.send_message_by_tg_id", new_callable=AsyncMock)
    @patch("api.db.confirm_referral_reward")
    @patch("sqlite3.connect")
    @patch("api.db.execute_query")
    async def test_worker_ignores_when_traffic_under_50mb(
        self, mock_exec_query, mock_sqlite, mock_confirm, mock_send
    ):
        """Если трафик новичка < 50 МБ (например, 10 МБ или 0 Б), награда не активируется."""
        from bot_xui.bot import check_pending_referral_rewards

        mock_exec_query.return_value = [{"id": 20, "tg_id": 222, "referred_by": 10}]

        # Трафик всего 10 МБ
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_cur.fetchall.return_value = [
            {"email": "tiin_222", "up": 5 * 1024 * 1024, "down": 5 * 1024 * 1024}
        ]
        mock_conn.cursor.return_value = mock_cur
        mock_sqlite.return_value = mock_conn

        mock_bot = AsyncMock()
        await check_pending_referral_rewards(mock_bot)

        # Не должно вызываться
        mock_confirm.assert_not_called()
        mock_send.assert_not_called()

