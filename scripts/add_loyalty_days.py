#!/usr/bin/env python3
"""
Добавляет N дней к подписке всех активных клиентов (loyalty бонус).
Синхронизирует: users.subscription_until, vpn_keys.expires_at, x-ui expiryTime.

Запуск:
  python3 scripts/add_loyalty_days.py [--days 7] [--dry-run]
"""
import sys
import os
import json
import sqlite3
import subprocess
import logging
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import argparse

from api.db import execute_query, sync_expiry, sync_expiry_by_user_id

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

NOW = datetime.utcnow()
DAYS = 7
BACKUP_DIR = "/home/alvik/backups"
XUI_DB_PATH = "/etc/x-ui/x-ui.db"
XUI_EXTEND_MS = DAYS * 24 * 60 * 60 * 1000


def backup_tables():
    """Бэкап таблиц users и vpn_keys перед изменениями."""
    os.makedirs(BACKUP_DIR, exist_ok=True)
    ts = NOW.strftime("%Y%m%d_%H%M%S")
    path = os.path.join(BACKUP_DIR, f"loyalty_backup_{ts}.sql")
    log.info("Creating backup: %s", path)
    from config import MYSQL_PORT, MYSQL_USER, MYSQL_PASSWORD, MYSQL_DATABASE
    cmd = [
        "mysqldump",
        "-h", "127.0.0.1",
        "-P", str(MYSQL_PORT),
        "-u", MYSQL_USER,
        f"-p{MYSQL_PASSWORD}",
        MYSQL_DATABASE,
        "users", "vpn_keys",
        "--single-transaction",
        "--quick",
    ]
    with open(path, "w") as f:
        result = subprocess.run(cmd, stdout=f, stderr=subprocess.PIPE, text=True)
    if result.returncode != 0:
        log.error("Backup failed (rc=%d): %s", result.returncode, result.stderr.strip())
        raise RuntimeError("Backup failed, aborting")
    log.info("Backup saved: %s", path)
    return path


def backup_xui_db():
    """Бэкап x-ui SQLite базы."""
    ts = NOW.strftime("%Y%m%d_%H%M%S")
    path = os.path.join(BACKUP_DIR, f"loyalty_backup_xui_{ts}.db")
    log.info("Creating x-ui backup: %s", path)
    import shutil
    shutil.copy2(XUI_DB_PATH, path)
    return path


def get_active_users():
    """Получить активных клиентов: subscription_until > NOW()."""
    rows = execute_query(
        "SELECT id, tg_id, subscription_until FROM users WHERE subscription_until > NOW()",
        fetch="all",
    ) or []
    return rows


def get_user_emails(tg_id, user_id):
    """Получить client_name (email) пользователя из vpn_keys."""
    if tg_id:
        row = execute_query(
            "SELECT client_name FROM vpn_keys WHERE tg_id = %s ORDER BY expires_at DESC LIMIT 1",
            (tg_id,), fetch="one",
        )
    else:
        row = execute_query(
            "SELECT client_name FROM vpn_keys WHERE user_id = %s ORDER BY expires_at DESC LIMIT 1",
            (user_id,), fetch="one",
        )
    if row:
        return [row['client_name']]
    return []


def update_xui_expiry(emails, extend_ms):
    """Обновить expiryTime в x-ui (client_traffics + inbounds.settings JSON)."""
    if not emails:
        return {"updated": 0, "skipped": 0, "errors": 0}

    stats = {"updated": 0, "skipped": 0, "errors": 0}
    conn = sqlite3.connect(XUI_DB_PATH, timeout=15)
    try:
        cur = conn.cursor()

        for email in emails:
            # 1. Найти текущий expiry_time в client_traffics
            cur.execute("SELECT expiry_time, inbound_id FROM client_traffics WHERE email = ?", (email,))
            row = cur.fetchone()
            if not row:
                log.info("  x-ui: no client_traffics for %s, skip", email)
                stats["skipped"] += 1
                continue

            current_expiry_ms, inbound_id = row
            now_ms = int(NOW.timestamp() * 1000)

            base = current_expiry_ms if current_expiry_ms > now_ms else now_ms
            new_expiry_ms = base + extend_ms

            # 2. Обновить client_traffics.expiry_time
            cur.execute("UPDATE client_traffics SET expiry_time = ? WHERE email = ?", (new_expiry_ms, email))

            # 3. Обновить expiryTime в inbounds.settings JSON
            cur.execute("SELECT id, settings FROM inbounds WHERE id = ?", (inbound_id,))
            inbound_row = cur.fetchone()
            if inbound_row:
                try:
                    settings = json.loads(inbound_row[1]) if isinstance(inbound_row[1], str) else inbound_row[1]
                    clients = settings.get("clients", [])
                    for client in clients:
                        if client.get("email") == email:
                            client["expiryTime"] = new_expiry_ms
                    settings_json = json.dumps(settings, ensure_ascii=False)
                    cur.execute("UPDATE inbounds SET settings = ? WHERE id = ?", (settings_json, inbound_id))
                except (json.JSONDecodeError, TypeError) as e:
                    log.warning("  x-ui: failed to update inbounds JSON for %s: %s", email, e)
                    stats["errors"] += 1
                    continue

            log.info("  x-ui: %s expiry %d → %d", email, current_expiry_ms, new_expiry_ms)
            stats["updated"] += 1

        conn.commit()
    except Exception as e:
        conn.rollback()
        log.error("  x-ui update error: %s", e)
        stats["errors"] += 1
    finally:
        conn.close()

    return stats


def verify_mysql(user_id, tg_id, expected_until):
    """Проверить что users.subscription_until и vpn_keys.expires_at обновлены."""
    issues = []

    row = execute_query(
        "SELECT subscription_until FROM users WHERE id = %s",
        (user_id,), fetch="one",
    )
    if row:
        actual = row['subscription_until']
        if actual and expected_until:
            diff = abs((actual - expected_until).total_seconds())
            if diff > 2:
                issues.append(f"users.subscription_until mismatch: expected={expected_until}, got={actual}")
    else:
        issues.append(f"user id={user_id} not found")

    if tg_id:
        keys = execute_query(
            "SELECT id, expires_at FROM vpn_keys WHERE tg_id = %s AND expires_at > NOW()",
            (tg_id,), fetch="all",
        ) or []
    else:
        keys = execute_query(
            "SELECT id, expires_at FROM vpn_keys WHERE user_id = %s AND expires_at > NOW()",
            (user_id,), fetch="all",
        ) or []

    for key in keys:
        actual = key['expires_at']
        if actual and expected_until:
            diff = abs((actual - expected_until).total_seconds())
            if diff > 2:
                issues.append(f"vpn_keys id={key['id']} expires_at mismatch: expected={expected_until}, got={actual}")

    return issues


def verify_xui(emails, expected_until_ms):
    """Проверить что x-ui expiryTime обновлён."""
    if not emails:
        return []

    issues = []
    conn = sqlite3.connect(XUI_DB_PATH, timeout=15)
    try:
        cur = conn.cursor()
        for email in emails:
            cur.execute("SELECT expiry_time FROM client_traffics WHERE email = ?", (email,))
            row = cur.fetchone()
            if not row:
                continue
            actual = row[0]
            if actual > 0 and abs(actual - expected_until_ms) > 60000:
                issues.append(f"x-ui {email} expiry_time mismatch: expected={expected_until_ms}, got={actual}")
    except Exception as e:
        issues.append(f"x-ui verify error: {e}")
    finally:
        conn.close()

    return issues


def apply_loyalty(days, dry_run=False):
    """Основная логика: добавить дни к подписке активным клиентам."""
    log.info("=" * 60)
    log.info("Loyalty bonus: +%d days | dry_run=%s", days, dry_run)
    log.info("=" * 60)

    if not dry_run:
        backup_tables()
        backup_xui_db()

    users = get_active_users()
    log.info("Active users found: %d", len(users))

    if not users:
        log.info("No active users to update. Exiting.")
        return

    log.info("-" * 60)
    for u in users:
        old = u['subscription_until']
        new = old + timedelta(days=days)
        log.info("  user id=%d tg_id=%s | %s → %s", u['id'], u['tg_id'], old, new)
    log.info("-" * 60)

    if dry_run:
        log.info("DRY RUN — no changes made.")
        return

    stats = {"updated": 0, "errors": 0, "xui_updated": 0, "xui_skipped": 0, "xui_errors": 0, "verify_issues": 0}

    for u in users:
        user_id = u['id']
        tg_id = u['tg_id']
        old_until = u['subscription_until']
        new_until = old_until + timedelta(days=days)
        new_until_ms = int(new_until.timestamp() * 1000)

        log.info("Processing user id=%d tg_id=%s ...", user_id, tg_id)

        try:
            # MySQL: users + vpn_keys
            if tg_id:
                sync_expiry(tg_id, new_until)
            else:
                sync_expiry_by_user_id(user_id, new_until)
            stats["updated"] += 1

            # x-ui: client_traffics + inbounds.settings
            emails = get_user_emails(tg_id, user_id)
            if emails:
                xui_stats = update_xui_expiry(emails, XUI_EXTEND_MS)
                stats["xui_updated"] += xui_stats["updated"]
                stats["xui_skipped"] += xui_stats["skipped"]
                stats["xui_errors"] += xui_stats["errors"]
            else:
                log.info("  no x-ui emails, skip")
                stats["xui_skipped"] += 1

            # Verify
            issues = verify_mysql(user_id, tg_id, new_until)
            issues += verify_xui(emails, new_until_ms)
            if issues:
                for issue in issues:
                    log.warning("  VERIFY: %s", issue)
                stats["verify_issues"] += 1
            else:
                log.info("  ✓ verified OK")

        except Exception as e:
            log.error("  ERROR user id=%d: %s", user_id, e, exc_info=True)
            stats["errors"] += 1

    log.info("=" * 60)
    log.info("DONE — stats: %s", stats)
    log.info("=" * 60)


def main():
    parser = argparse.ArgumentParser(description="Loyalty bonus: add days to active subscriptions")
    parser.add_argument("--days", type=int, default=DAYS, help="Days to add (default: 7)")
    parser.add_argument("--dry-run", action="store_true", help="Preview only, no changes")
    args = parser.parse_args()

    apply_loyalty(args.days, args.dry_run)


if __name__ == "__main__":
    main()
