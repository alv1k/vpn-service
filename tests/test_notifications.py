"""Тесты для api/notifications.py — email, codes, branded templates."""
import sys
from unittest.mock import patch, MagicMock, call
import pytest

sys.modules.setdefault("yookassa", MagicMock())


# ─────────────────────────────────────────────
#  Code generation
# ─────────────────────────────────────────────

def test_generate_code_length():
    from api.notifications import _generate_code, CODE_LENGTH
    code = _generate_code()
    assert len(code) == CODE_LENGTH
    assert code.isdigit()


def test_generate_code_is_random():
    from api.notifications import _generate_code
    codes = {_generate_code() for _ in range(50)}
    assert len(codes) > 1  # not always the same


# ─────────────────────────────────────────────
#  create_auth_code
# ─────────────────────────────────────────────

@patch("api.notifications._send", return_value=True)
@patch("api.notifications.execute_query")
def test_create_auth_code_success(mock_query, mock_send):
    from api.notifications import create_auth_code
    mock_query.return_value = {"cnt": 0}  # no active codes
    code = create_auth_code("user@example.com", "email")
    assert code is not None
    assert len(code) == 6
    mock_send.assert_called_once()


@patch("api.notifications.execute_query")
def test_create_auth_code_rate_limited(mock_query):
    from api.notifications import create_auth_code, MAX_ACTIVE_CODES
    mock_query.return_value = {"cnt": MAX_ACTIVE_CODES}
    code = create_auth_code("spam@example.com", "email")
    assert code is None


@patch("api.notifications._send", return_value=False)
@patch("api.notifications.execute_query")
def test_create_auth_code_send_fails(mock_query, mock_send):
    from api.notifications import create_auth_code
    mock_query.return_value = {"cnt": 0}
    code = create_auth_code("fail@example.com", "email")
    assert code is None


# ─────────────────────────────────────────────
#  verify_code
# ─────────────────────────────────────────────

@patch("api.db.get_db")
def test_verify_code_valid(mock_get_db):
    from api.notifications import verify_code
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_cursor.rowcount = 1
    mock_conn.cursor.return_value = mock_cursor
    mock_get_db.return_value = mock_conn
    assert verify_code("user@example.com", "123456") is True
    mock_cursor.execute.assert_called_once()
    mock_conn.commit.assert_called_once()


@patch("api.db.get_db")
def test_verify_code_invalid(mock_get_db):
    from api.notifications import verify_code
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_cursor.rowcount = 0
    mock_conn.cursor.return_value = mock_cursor
    mock_get_db.return_value = mock_conn
    assert verify_code("user@example.com", "000000") is False


# ─────────────────────────────────────────────
#  cleanup_expired
# ─────────────────────────────────────────────

@patch("api.notifications.execute_query")
def test_cleanup_expired(mock_query):
    from api.notifications import cleanup_expired
    cleanup_expired()
    mock_query.assert_called_once()
    assert "DELETE" in mock_query.call_args[0][0]


# ─────────────────────────────────────────────
#  _send dispatcher
# ─────────────────────────────────────────────

@patch("api.notifications._send_email", return_value=True)
def test_send_dispatches_email(mock_email):
    from api.notifications import _send
    assert _send("user@example.com", "123456", "email") is True
    mock_email.assert_called_once_with("user@example.com", "123456")


def test_send_sms_returns_false():
    from api.notifications import _send
    assert _send("+79001234567", "123456", "sms") is False


def test_send_unknown_channel():
    from api.notifications import _send
    assert _send("dest", "code", "pigeon") is False


# ─────────────────────────────────────────────
#  _send_email
# ─────────────────────────────────────────────

@patch("api.notifications.SMTP_HOST", "smtp.example.com")
@patch("api.notifications.SMTP_PORT", "587")
@patch("api.notifications.SMTP_USER", "user@example.com")
@patch("api.notifications.SMTP_PASSWORD", "pass")
@patch("api.notifications.SMTP_FROM", "noreply@example.com")
@patch("api.notifications.smtplib.SMTP")
def test_send_email_starttls(mock_smtp_cls):
    from api.notifications import _send_email
    mock_server = MagicMock()
    mock_smtp_cls.return_value.__enter__ = MagicMock(return_value=mock_server)
    mock_smtp_cls.return_value.__exit__ = MagicMock(return_value=False)
    result = _send_email("to@example.com", "123456")
    assert result is True
    mock_smtp_cls.assert_called_once_with("smtp.example.com", 587)


@patch("api.notifications.SMTP_HOST", "smtp.example.com")
@patch("api.notifications.SMTP_PORT", "465")
@patch("api.notifications.SMTP_USER", "user@example.com")
@patch("api.notifications.SMTP_PASSWORD", "pass")
@patch("api.notifications.SMTP_FROM", "noreply@example.com")
@patch("api.notifications.smtplib.SMTP_SSL")
def test_send_email_ssl(mock_smtp_ssl):
    from api.notifications import _send_email
    mock_server = MagicMock()
    mock_smtp_ssl.return_value.__enter__ = MagicMock(return_value=mock_server)
    mock_smtp_ssl.return_value.__exit__ = MagicMock(return_value=False)
    result = _send_email("to@example.com", "123456")
    assert result is True
    mock_smtp_ssl.assert_called_once_with("smtp.example.com", 465)


@patch("api.notifications.SMTP_HOST", "")
@patch("api.notifications.SMTP_USER", "")
@patch("api.notifications.SMTP_PASSWORD", "")
def test_send_email_no_config():
    from api.notifications import _send_email
    assert _send_email("to@example.com", "code") is False


@patch("api.notifications.SMTP_HOST", "smtp.example.com")
@patch("api.notifications.SMTP_PORT", "587")
@patch("api.notifications.SMTP_USER", "user@example.com")
@patch("api.notifications.SMTP_PASSWORD", "pass")
@patch("api.notifications.SMTP_FROM", "noreply@example.com")
@patch("api.notifications.smtplib.SMTP", side_effect=Exception("Connection refused"))
def test_send_email_exception(mock_smtp):
    from api.notifications import _send_email
    assert _send_email("to@example.com", "code") is False


# ─────────────────────────────────────────────
#  _branded_html
# ─────────────────────────────────────────────

def test_branded_html_contains_branding():
    from api.notifications import _branded_html
    html = _branded_html("<p>Test content</p>")
    assert "TIIN" in html
    assert "<p>Test content</p>" in html
    assert "support@tiinservice.ru" in html
    assert "tiin_service_bot" in html


# ─────────────────────────────────────────────
#  Transactional emails
# ─────────────────────────────────────────────

@patch("api.notifications._send_html_email", return_value=True)
def test_send_payment_success_email(mock_send):
    from api.notifications import send_payment_success_email
    result = send_payment_success_email("u@e.com", "Месяц", "30 дней", "https://example.com/my/tok")
    assert result is True
    args = mock_send.call_args
    assert args[0][0] == "u@e.com"
    assert "Оплата" in args[0][1]
    assert "Месяц" in args[0][2]


@patch("api.notifications._send_html_email", return_value=True)
def test_send_expiry_warning_1_day(mock_send):
    from api.notifications import send_expiry_warning_email
    result = send_expiry_warning_email("u@e.com", 1, "2026-04-01")
    assert result is True
    subject = mock_send.call_args[0][1]
    assert "1 день" in subject


@patch("api.notifications._send_html_email", return_value=True)
def test_send_expiry_warning_3_days(mock_send):
    from api.notifications import send_expiry_warning_email
    result = send_expiry_warning_email("u@e.com", 3, "2026-04-03")
    assert result is True
    subject = mock_send.call_args[0][1]
    assert "3 дня" in subject


@patch("api.notifications._send_html_email", return_value=True)
def test_send_expiry_warning_7_days(mock_send):
    from api.notifications import send_expiry_warning_email
    result = send_expiry_warning_email("u@e.com", 7, "2026-04-07")
    assert result is True
    subject = mock_send.call_args[0][1]
    assert "7 дней" in subject


@patch("api.notifications._send_html_email", return_value=True)
def test_send_support_autoreply(mock_send):
    from api.notifications import send_support_autoreply
    result = send_support_autoreply("u@e.com")
    assert result is True
    assert mock_send.call_args[0][0] == "u@e.com"
    assert "обращение" in mock_send.call_args[0][1].lower()


@patch("api.notifications._send_html_email", return_value=True)
def test_send_support_message_to_team(mock_send):
    from api.notifications import send_support_message_to_team
    result = send_support_message_to_team("user@e.com", "Help me!")
    assert result is True
    assert mock_send.call_args[0][0] == "support@tiinservice.ru"
    assert "user@e.com" in mock_send.call_args[0][1]


@patch("api.notifications._send_html_email", return_value=True)
def test_send_support_message_escapes_html(mock_send):
    from api.notifications import send_support_message_to_team
    send_support_message_to_team("<script>xss</script>@e.com", "<b>bad</b>")
    html_body = mock_send.call_args[0][3]
    assert "<script>" not in html_body
    assert "&lt;script&gt;" in html_body
