"""
Notification service: email/SMS auth codes.
Generates codes, stores in auth_codes table, sends via SMTP (or SMS in future).
"""
import logging
import random
import string
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta

from api.db import execute_query
from config import (
    SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, SMTP_FROM,
)

logger = logging.getLogger(__name__)

CODE_LENGTH = 6
CODE_TTL_MINUTES = 10
MAX_ACTIVE_CODES = 3  # per destination, prevents spam


def _generate_code() -> str:
    return ''.join(random.choices(string.digits, k=CODE_LENGTH))


# ─────────────────────────────────────────────
#  Code lifecycle
# ─────────────────────────────────────────────

def create_auth_code(destination: str, channel: str = "email") -> str | None:
    """Generate a code, store it, and send it. Returns the code on success, None on failure."""
    # Rate limit: count unexpired, unused codes for this destination
    active = execute_query(
        "SELECT COUNT(*) AS cnt FROM auth_codes "
        "WHERE destination = %s AND used = 0 AND expires_at > NOW()",
        (destination,), fetch='one',
    )
    if active and active['cnt'] >= MAX_ACTIVE_CODES:
        logger.warning(f"Rate limit: {destination} has {active['cnt']} active codes")
        return None

    code = _generate_code()
    expires_at = datetime.utcnow() + timedelta(minutes=CODE_TTL_MINUTES)

    execute_query(
        "INSERT INTO auth_codes (destination, channel, code, expires_at) "
        "VALUES (%s, %s, %s, %s)",
        (destination, channel, code, expires_at),
    )

    sent = _send(destination, code, channel)
    if not sent:
        logger.error(f"Failed to send code to {destination} via {channel}")
        return None

    logger.info(f"Auth code sent to {destination} via {channel}")
    return code


def verify_code(destination: str, code: str) -> bool:
    """Check if the code is valid. Marks it as used on success."""
    row = execute_query(
        "SELECT id FROM auth_codes "
        "WHERE destination = %s AND code = %s AND used = 0 AND expires_at > NOW() "
        "ORDER BY created_at DESC LIMIT 1",
        (destination, code), fetch='one',
    )
    if not row:
        return False

    execute_query(
        "UPDATE auth_codes SET used = 1 WHERE id = %s",
        (row['id'],),
    )
    return True


def cleanup_expired():
    """Delete codes older than 24h (housekeeping)."""
    execute_query(
        "DELETE FROM auth_codes WHERE expires_at < NOW() - INTERVAL 1 DAY",
    )


# ─────────────────────────────────────────────
#  Delivery
# ─────────────────────────────────────────────

def _send(destination: str, code: str, channel: str) -> bool:
    if channel == "email":
        return _send_email(destination, code)
    elif channel == "sms":
        return _send_sms(destination, code)
    return False


def _send_email(to: str, code: str) -> bool:
    if not all([SMTP_HOST, SMTP_USER, SMTP_PASSWORD]):
        logger.error("SMTP not configured")
        return False

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"Код подтверждения: {code}"
    msg["From"] = f"TIIN <{SMTP_FROM or SMTP_USER}>"
    msg["To"] = to
    msg["Reply-To"] = "support@tiinservice.ru"

    text = f"Ваш код подтверждения: {code}\nКод действителен {CODE_TTL_MINUTES} минут."
    html = f"""\
<html>
<body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; padding: 20px;">
  <div style="max-width: 400px; margin: 0 auto; text-align: center;">
    <h2 style="color: #333;">Код подтверждения</h2>
    <div style="font-size: 32px; font-weight: bold; letter-spacing: 8px; padding: 20px;
                background: #f5f5f5; border-radius: 8px; margin: 20px 0;">
      {code}
    </div>
    <p style="color: #666;">Код действителен {CODE_TTL_MINUTES} минут</p>
    <p style="color: #999; font-size: 12px;">Если вы не запрашивали код, проигнорируйте это письмо.</p>
  </div>
</body>
</html>"""

    msg.attach(MIMEText(text, "plain", "utf-8"))
    msg.attach(MIMEText(html, "html", "utf-8"))

    try:
        port = int(SMTP_PORT or 587)
        if port == 465:
            with smtplib.SMTP_SSL(SMTP_HOST, port) as server:
                server.login(SMTP_USER, SMTP_PASSWORD)
                server.sendmail(SMTP_FROM or SMTP_USER, to, msg.as_string())
        else:
            with smtplib.SMTP(SMTP_HOST, port) as server:
                server.starttls()
                server.login(SMTP_USER, SMTP_PASSWORD)
                server.sendmail(SMTP_FROM or SMTP_USER, to, msg.as_string())
        return True
    except Exception as e:
        logger.exception(f"SMTP send failed: {e}")
        return False


def _send_sms(to: str, code: str) -> bool:
    """SMS sending stub — implement when SMS provider is chosen."""
    logger.warning(f"SMS not implemented, code for {to}: {code}")
    return False


# ─────────────────────────────────────────────
#  Branded email template
# ─────────────────────────────────────────────

def _branded_html(body_content: str) -> str:
    """Wrap content in branded TIIN dark-theme email template."""
    return f"""\
<html>
<body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
             background: #0a0a0a; padding: 20px; color: #e5e5e5; margin: 0;">
  <div style="max-width: 420px; margin: 0 auto;">
    <div style="text-align: center; padding: 2rem 0 1rem;">
      <h1 style="color: #fff; font-size: 1.5rem; letter-spacing: .05em; margin: 0;">TIIN</h1>
    </div>

    {body_content}

    <div style="text-align: center; padding: 2rem 0; color: #555; font-size: .75rem;">
      <a href="https://t.me/tiin_service_bot" style="color: #888; text-decoration: none;">Telegram-бот</a>
      &nbsp;·&nbsp;
      <a href="mailto:support@tiinservice.ru" style="color: #888; text-decoration: none;">support@tiinservice.ru</a>
    </div>
  </div>
</body>
</html>"""


# ─────────────────────────────────────────────
#  Transactional emails (payment, expiry)
# ─────────────────────────────────────────────

def _send_html_email(to: str, subject: str, text: str, html: str) -> bool:
    """Generic HTML email sender."""
    if not all([SMTP_HOST, SMTP_USER, SMTP_PASSWORD]):
        logger.error("SMTP not configured")
        return False

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"TIIN <{SMTP_FROM or SMTP_USER}>"
    msg["To"] = to
    msg["Reply-To"] = "support@tiinservice.ru"

    msg.attach(MIMEText(text, "plain", "utf-8"))
    msg.attach(MIMEText(html, "html", "utf-8"))

    try:
        port = int(SMTP_PORT or 587)
        if port == 465:
            with smtplib.SMTP_SSL(SMTP_HOST, port) as server:
                server.login(SMTP_USER, SMTP_PASSWORD)
                server.sendmail(SMTP_FROM or SMTP_USER, to, msg.as_string())
        else:
            with smtplib.SMTP(SMTP_HOST, port) as server:
                server.starttls()
                server.login(SMTP_USER, SMTP_PASSWORD)
                server.sendmail(SMTP_FROM or SMTP_USER, to, msg.as_string())
        logger.info(f"Email sent to {to}: {subject}")
        return True
    except Exception as e:
        logger.exception(f"SMTP send failed: {e}")
        return False


def send_payment_success_email(to: str, tariff_name: str, period: str, portal_url: str) -> bool:
    """Send payment confirmation email to web user."""
    subject = f"TIIN — Оплата прошла успешно"

    text = (
        f"Оплата прошла успешно!\n\n"
        f"Тариф: {tariff_name}\n"
        f"Период: {period}\n\n"
        f"Перейдите в личный кабинет для подключения:\n{portal_url}\n\n"
        f"Сохраните эту ссылку — она понадобится для доступа к конфигу."
    )

    body = f"""\
    <div style="background: #161616; border: 1px solid #262626; border-radius: 12px; padding: 1.5rem; margin-bottom: 1rem;">
      <div style="text-align: center; font-size: 2rem; margin-bottom: .5rem;">&#10004;&#65039;</div>
      <h2 style="color: #22c55e; text-align: center; font-size: 1.2rem; margin: 0;">Оплата прошла успешно!</h2>
      <div style="margin-top: 1rem; color: #ccc; font-size: .9rem;">
        <div>&#9889; Тариф: <b>{tariff_name}</b></div>
        <div style="margin-top: .3rem;">&#128197; Период: {period}</div>
      </div>
    </div>

    <div style="text-align: center; margin: 1.5rem 0;">
      <a href="{portal_url}"
         style="display: inline-block; padding: 14px 32px; background: #7c3aed; color: #fff;
                border-radius: 10px; font-size: 1rem; font-weight: 600; text-decoration: none;">
        Перейти к подключению
      </a>
    </div>

    <div style="background: #1c1507; border: 1px solid #854d0e; border-radius: 10px;
                padding: 1rem; margin-top: 1rem;">
      <div style="font-weight: 600; color: #facc15; font-size: .9rem;">&#9888;&#65039; Сохраните ссылку!</div>
      <p style="color: #a3a3a3; font-size: .8rem; margin-top: .4rem; line-height: 1.4;">
        Добавьте страницу в закладки. Это ваш единственный способ доступа к конфигу VPN.
      </p>
    </div>"""

    html = _branded_html(body)
    return _send_html_email(to, subject, text, html)


def send_expiry_warning_email(to: str, days_left: int, expiry_date: str) -> bool:
    """Send subscription expiry warning email."""
    if days_left == 1:
        label = "1 день"
    elif days_left in (2, 3, 4):
        label = f"{days_left} дня"
    else:
        label = f"{days_left} дней"

    subject = f"TIIN — Подписка истекает через {label}"

    text = (
        f"Подписка истекает через {label}\n\n"
        f"Дата окончания: {expiry_date}\n\n"
        f"Продлите подписку, чтобы не потерять доступ:\n"
        f"https://344988.snk.wtf\n"
    )

    body = f"""\
    <div style="background: #161616; border: 1px solid #262626; border-radius: 12px; padding: 1.5rem;">
      <div style="text-align: center; font-size: 2rem; margin-bottom: .5rem;">&#9203;</div>
      <h2 style="color: #facc15; text-align: center; font-size: 1.1rem; margin: 0;">
        Подписка истекает через {label}
      </h2>
      <p style="color: #888; text-align: center; font-size: .9rem; margin-top: .5rem;">
        Дата окончания: <b style="color: #ccc;">{expiry_date}</b>
      </p>
      <p style="color: #888; text-align: center; font-size: .85rem; margin-top: .5rem;">
        Продлите, чтобы не потерять доступ к VPN.
      </p>
    </div>

    <div style="text-align: center; margin: 1.5rem 0;">
      <a href="https://344988.snk.wtf"
         style="display: inline-block; padding: 14px 32px; background: #7c3aed; color: #fff;
                border-radius: 10px; font-size: 1rem; font-weight: 600; text-decoration: none;">
        Продлить подписку
      </a>
    </div>"""

    html = _branded_html(body)
    return _send_html_email(to, subject, text, html)


def send_support_autoreply(to: str) -> bool:
    """Send auto-reply confirmation when user contacts support."""
    subject = "TIIN — Мы получили ваше обращение"

    text = (
        "Здравствуйте!\n\n"
        "Мы получили ваше сообщение и ответим в ближайшее время.\n\n"
        "С уважением,\nкоманда TIIN"
    )

    body = """\
    <div style="background: #161616; border: 1px solid #262626; border-radius: 12px; padding: 1.5rem;">
      <div style="text-align: center; font-size: 2rem; margin-bottom: .5rem;">&#9993;&#65039;</div>
      <h2 style="color: #a78bfa; text-align: center; font-size: 1.1rem; margin: 0;">
        Мы получили ваше сообщение
      </h2>
      <p style="color: #888; text-align: center; font-size: .9rem; margin-top: .8rem; line-height: 1.5;">
        Спасибо за обращение! Мы ответим вам в ближайшее время.
      </p>
    </div>"""

    html = _branded_html(body)
    return _send_html_email(to, subject, text, html)


def send_support_message_to_team(from_email: str, message: str) -> bool:
    """Forward user's support message to the team inbox."""
    import html as html_mod
    subject = f"TIIN Support — сообщение от {from_email}"

    text = f"От: {from_email}\n\n{message}"

    safe_email = html_mod.escape(from_email)
    safe_message = html_mod.escape(message)

    body = f"""\
    <div style="background: #161616; border: 1px solid #262626; border-radius: 12px; padding: 1.5rem;">
      <h2 style="color: #a78bfa; font-size: 1rem; margin: 0 0 .8rem;">Новое обращение</h2>
      <div style="color: #888; font-size: .85rem;">От: <b style="color: #ccc;">{safe_email}</b></div>
      <div style="margin-top: 1rem; background: #1a1a1a; border: 1px solid #333; border-radius: 8px;
                  padding: 1rem; color: #e5e5e5; font-size: .9rem; line-height: 1.5; white-space: pre-wrap;">
        {safe_message}
      </div>
    </div>"""

    html = _branded_html(body)
    return _send_html_email("support@tiinservice.ru", subject, text, html)
