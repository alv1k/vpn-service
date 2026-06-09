#!/usr/bin/env python3
"""
Анализ пользователей для возврата в сервис.
Проверяет все сценарии неактивности и формирует отчёт.

Запуск: python3 scripts/win_back_users.py [--send]
  без флагов — только отчёт
  --send     — отправить сообщения пользователям
"""
import sys
import os
import json
import asyncio
import logging
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.db import execute_query, get_referral_count, create_winback_promo
from bot_xui.utils import XUIClient
from config import XUI_HOST, XUI_USERNAME, XUI_PASSWORD, ADMIN_TG_ID

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

MB = 1024 * 1024
NOW = datetime.utcnow()
COOLDOWN_DAYS = 5  # Не отправлять одному пользователю чаще чем раз в 5 дней


# ─────────────────────────────────────────────
#  Дедупликация
# ─────────────────────────────────────────────

def get_recent_sends():
    """Получить tg_id, которым отправляли за последние COOLDOWN_DAYS дней. Возвращает {tg_id: last_sent_at}."""
    rows = execute_query(
        "SELECT tg_id, MAX(sent_at) as last_sent "
        "FROM winback_log "
        "WHERE sent_at > %s "
        "GROUP BY tg_id",
        (NOW - timedelta(days=COOLDOWN_DAYS),),
        fetch='all',
    ) or []
    return {r['tg_id']: r['last_sent'] for r in rows}


def log_send(tg_id, scenario):
    """Записать отправку в лог."""
    execute_query(
        "INSERT INTO winback_log (tg_id, scenario) VALUES (%s, %s)",
        (tg_id, scenario),
    )


# ─────────────────────────────────────────────
#  Сбор данных
# ─────────────────────────────────────────────

def get_all_users():
    """Все пользователи из БД ( except those who blocked the bot)."""
    return execute_query(
        "SELECT tg_id, first_name, subscription_until, test_vless_activated, "
        "test_awg_activated, test_softether_activated, created_at "
        "FROM users WHERE bot_blocked = 0",
        fetch='all',
    ) or []


def get_all_keys():
    """Все VPN ключи, сгруппированные по tg_id."""
    rows = execute_query(
        "SELECT tg_id, client_name, vpn_type, expires_at, created_at FROM vpn_keys",
        fetch='all',
    ) or []
    keys_by_tg = {}
    for r in rows:
        keys_by_tg.setdefault(r['tg_id'], []).append(r)
    return keys_by_tg


def get_hysteria_clients_from_panel(xui):
    """Получить tg_id пользователей, у которых есть hysteria2 конфиг в x-ui. Возвращает set(tg_id)."""
    hysteria_tg_ids = set()
    try:
        inbounds = xui.get_inbounds()
        for ib in inbounds:
            if ib.get('protocol') != 'hysteria':
                continue
            settings = json.loads(ib.get('settings', '{}'))
            for client in settings.get('clients', []):
                tg_id = client.get('tgId')
                if tg_id:
                    hysteria_tg_ids.add(int(tg_id))
    except Exception as e:
        log.warning(f"Hysteria client collection error: {e}")
    return hysteria_tg_ids


def get_all_payments():
    """Все оплаченные платежи, сгруппированные по tg_id."""
    rows = execute_query(
        "SELECT tg_id, tariff, amount, status, created_at FROM payments WHERE status = 'paid'",
        fetch='all',
    ) or []
    payments_by_tg = {}
    for r in rows:
        payments_by_tg.setdefault(r['tg_id'], []).append(r)
    return payments_by_tg


def _add_traffic(traffic, tg_id, upload, download, enabled=True, last_online=0):
    """Merge traffic into existing dict for tg_id."""
    existing = traffic.get(tg_id, {'upload': 0, 'download': 0, 'enabled': True, 'last_online': 0})
    existing['upload'] += upload
    existing['download'] += download
    if not enabled:
        existing['enabled'] = False
    if last_online > existing.get('last_online', 0):
        existing['last_online'] = last_online
    traffic[tg_id] = existing


def _get_client_name_to_tg_id():
    """Map client_name → tg_id from vpn_keys table."""
    rows = execute_query(
        "SELECT client_name, tg_id FROM vpn_keys",
        fetch='all',
    ) or []
    return {r['client_name']: r['tg_id'] for r in rows}


def get_traffic_from_panel(xui):
    """Получить трафик ВСЕХ клиентов: VLESS + Hysteria2 (3x-ui) + AWG + SoftEther. Возвращает {tg_id: {upload, download, enabled}}."""
    traffic = {}

    # ── VLESS + Hysteria2 (3x-ui) ──
    inbounds = xui.get_inbounds()
    # Build email→tg_id map ONCE from all inbounds (covers both VLESS and hysteria clients)
    email_to_tg = {}
    for ib in inbounds:
        settings = json.loads(ib.get('settings', '{}'))
        for client in settings.get('clients', []):
            if client.get('tgId'):
                email_to_tg[client.get('email')] = int(client['tgId'])

    for ib in inbounds:
        for cs in ib.get('clientStats', []):
            tg_id = email_to_tg.get(cs.get('email'))
            if tg_id:
                _add_traffic(traffic, tg_id, cs.get('up', 0), cs.get('down', 0),
                             cs.get('enable', True), cs.get('lastOnline', 0))

    # ── AWG (awg show dump) ──
    try:
        import subprocess
        name_to_tg = _get_client_name_to_tg_id()

        # Map public_key → client name from AWG DB
        from awg_api.db import list_clients as awg_list_clients
        awg_clients = awg_list_clients()
        pub_to_name = {c['public_key']: c['name'] for c in awg_clients}

        result = subprocess.run(
            ["awg", "show", "awg0", "dump"], capture_output=True, text=True, timeout=5
        )
        for line in result.stdout.strip().split("\n")[1:]:
            parts = line.split("\t")
            if len(parts) < 7:
                continue
            pub_key = parts[0]
            handshake_ts = int(parts[4]) if parts[4].isdigit() else 0
            rx_bytes = int(parts[5]) if parts[5].isdigit() else 0
            tx_bytes = int(parts[6]) if parts[6].isdigit() else 0
            name = pub_to_name.get(pub_key)
            if name:
                tg_id = name_to_tg.get(name)
                if tg_id:
                    # awg handshake is unix seconds; _add_traffic expects ms (matches VLESS lastOnline)
                    last_online_ms = handshake_ts * 1000 if handshake_ts else 0
                    _add_traffic(traffic, tg_id, rx_bytes, tx_bytes, last_online=last_online_ms)
    except Exception as e:
        log.warning(f"AWG traffic error: {e}")

    # ── SoftEther (vpncmd UserGet) ──
    try:
        from bot_xui.softether import list_users as se_list_users
        name_to_tg = name_to_tg if 'name_to_tg' in dir() else _get_client_name_to_tg_id()

        for user in se_list_users():
            username = user.get('username', '')
            tg_id = name_to_tg.get(username)
            if tg_id and user.get('transfer_bytes', 0) > 0:
                # SoftEther reports total bytes (combined up+down), split evenly
                total = user['transfer_bytes']
                _add_traffic(traffic, tg_id, total // 2, total // 2)
    except Exception as e:
        log.warning(f"SoftEther traffic error: {e}")

    return traffic


# ─────────────────────────────────────────────
#  Классификация
# ─────────────────────────────────────────────

def _key_age_days(user_keys):
    """Возраст самого старого ключа в днях."""
    oldest = min(user_keys, key=lambda k: k['created_at'] or NOW)
    if oldest['created_at']:
        return (NOW - oldest['created_at']).days
    return 0


def _test_expired_days(user_keys):
    """Дней с момента окончания последнего тестового ключа."""
    test_keys = [k for k in user_keys if k['expires_at'] and k['expires_at'] < NOW]
    if not test_keys:
        return 0
    latest = max(test_keys, key=lambda k: k['expires_at'])
    return (NOW - latest['expires_at']).days


# Тайминги: через сколько дней после события отправлять сообщение
DELAY = {
    'zero_traffic': 1,        # 1 день после создания конфига
    'low_traffic': 1,         # 1 день после создания конфига
    'expired_fresh': 1,       # 1 день после истечения подписки
    'expired_old': 30,        # 30 дней после истечения
    'second_expiry_reminder': 14,  # 14 дней после истечения — повторное напоминание
    'test_no_purchase': 1,    # 1 день после окончания теста (был трафик)
    'test_no_connect': 1,    # 1 день после окончания теста (0 трафика)
    'payment_no_config': 0,   # сразу
    'panel_db_mismatch': 0,   # сразу
    'multi_config_partial': 7,# 7 дней без трафика
    'never_activated': 1,     # 1 день после регистрации
    'vless_only_inactive': 1,  # VLESS-only офлайн 1+ день — предложить AWG
    'awg_inactive': 1,         # есть AWG конфиг, офлайн 1+ день — напомнить о себе
    'hysteria_inactive': 7,    # Hysteria2 конфиг, не подключался 7+ дней
    'long_inactive_7d': 7,    # не заходил 7+ дней (был активен)
    'recently_inactive': 1,   # не заходил 1-3 дня (был активен)
    'referral_prompt': 3,     # активный пользователь, но не пригласил ни одного друга
}


def classify_users(users, keys_by_tg, payments_by_tg, traffic, hysteria_tg_ids=None):
    """Классифицировать пользователей по сценариям возврата."""
    if hysteria_tg_ids is None:
        hysteria_tg_ids = set()
    results = {
        'zero_traffic': [],        # 0 MB — не подключался
        'low_traffic': [],         # < 5 MB — попробовал, не заработало
        'expired_fresh': [],       # подписка истекла 1-7 дней
        'expired_old': [],         # подписка истекла 30+ дней
        'second_expiry_reminder': [],  # повторное напоминание через 14 дней
        'test_no_purchase': [],    # тест использован, был трафик, не купил
        'test_no_connect': [],     # тест использован, 0 трафика, не купил
        'multi_config_partial': [],# несколько конфигов, один тип не используется
        'payment_no_config': [],   # оплатил, но конфиг не выдан
        'panel_db_mismatch': [],   # активен в БД, деактивирован в панели
        'never_activated': [],     # зарегистрировался, тест не активировал, ключей нет
        'vless_only_inactive': [],  # VLESS-only, офлайн 1+ день, нет AWG — предложить AWG
        'awg_inactive': [],        # есть AWG конфиг, офлайн 1+ день — напомнить о себе
        'hysteria_inactive': [],   # Hysteria2 конфиг, не подключался 7+ дней
        'long_inactive_7d': [],    # не заходил 7+ дней (был активен)
        'recently_inactive': [],   # не заходил 1-3 дня (был активен)
        'referral_prompt': [],     # активный, но не пригласил друзей
    }

    for user in users:
        tg_id = user['tg_id']
        if not tg_id or tg_id == ADMIN_TG_ID:
            continue

        user_keys = keys_by_tg.get(tg_id, [])
        user_payments = payments_by_tg.get(tg_id, [])
        user_traffic = traffic.get(tg_id, {'upload': 0, 'download': 0, 'enabled': True})
        total_bytes = user_traffic['upload'] + user_traffic['download']
        sub_until = user.get('subscription_until')

        has_active_key = any(
            k['expires_at'] and k['expires_at'] > NOW for k in user_keys
        )
        has_vless = any(k['vpn_type'] == 'vless' for k in user_keys) or tg_id in hysteria_tg_ids
        has_awg = any(k['vpn_type'] == 'awg' for k in user_keys)

        key_age = _key_age_days(user_keys) if user_keys else 0

        vpn_types = list(set(k['vpn_type'] for k in user_keys))
        if tg_id in hysteria_tg_ids and 'hysteria' not in vpn_types:
            vpn_types.append('hysteria')
        info = {
            'tg_id': tg_id,
            'name': user.get('first_name', ''),
            'sub_until': sub_until,
            'total_mb': round(total_bytes / MB, 2),
            'keys': len(user_keys),
            'vpn_types': vpn_types,
            'paid_count': len(user_payments),
        }

        # Сценарий: Оплатил, конфиг не выдан — СРАЗУ
        if user_payments and not user_keys:
            results['payment_no_config'].append(info)
            continue

        # Сценарий: Зарегистрировался, тест не активировал, ключей нет
        test_used = (
            user.get('test_vless_activated') or
            user.get('test_awg_activated') or
            user.get('test_softether_activated')
        )
        if not user_keys and not test_used and not user_payments:
            reg_age = (NOW - user['created_at']).days if user.get('created_at') else 0
            if reg_age >= DELAY['never_activated']:
                results['never_activated'].append({**info, 'reg_days': reg_age})
            continue

        # Нет ключей — пропускаем
        if not user_keys:
            continue

        # Сценарий: Активен в БД, деактивирован в панели — СРАЗУ
        if has_active_key and has_vless and not user_traffic.get('enabled', True):
            results['panel_db_mismatch'].append(info)

        # Сценарий: 0 MB трафика — через 1 день после создания конфига
        last_online_ms = user_traffic.get('last_online', 0)
        is_active_recently = (last_online_ms > 0 and (NOW - datetime.utcfromtimestamp(last_online_ms / 1000)).days < 1)

        if total_bytes == 0 and has_active_key and not is_active_recently and key_age >= DELAY['zero_traffic']:
            results['zero_traffic'].append(info)
            continue

        # Сценарий: < 5 MB — через 1 день после создания конфига
        if 0 < total_bytes < 5 * MB and has_active_key and not is_active_recently and key_age >= DELAY['low_traffic']:
            results['low_traffic'].append(info)
            continue

        # Сценарий: Подписка истекла
        if sub_until and sub_until < NOW:
            days_expired = (NOW - sub_until).days
            if DELAY['expired_fresh'] <= days_expired <= 7:
                results['expired_fresh'].append({**info, 'days_expired': days_expired})
            elif days_expired >= DELAY['expired_old']:
                results['expired_old'].append({**info, 'days_expired': days_expired})
            continue

        # Сценарий: Тест использован, не купил — через 1 день после окончания теста
        if test_used and not user_payments and not has_active_key:
            days_since_test = _test_expired_days(user_keys)
            if total_bytes > 0 and days_since_test >= DELAY['test_no_purchase']:
                results['test_no_purchase'].append({**info, 'days_since_test': days_since_test})
            elif total_bytes == 0 and days_since_test >= DELAY['test_no_connect']:
                results['test_no_connect'].append({**info, 'days_since_test': days_since_test})
            continue

        # Сценарий: VLESS-only пользователь ушёл в офлайн — предложить AWG
        last_online_ms = user_traffic.get('last_online', 0)
        if has_active_key and has_vless and not has_awg and last_online_ms > 0:
            last_online_dt = datetime.utcfromtimestamp(last_online_ms / 1000)
            days_offline = (NOW - last_online_dt).days
            if days_offline >= DELAY['vless_only_inactive']:
                results['vless_only_inactive'].append({**info, 'days_offline': days_offline})
                continue

        # Сценарий: Есть AWG конфиг, но пользователь ушёл в офлайн — напомнить о себе
        if has_active_key and has_awg and last_online_ms > 0:
            last_online_dt = datetime.utcfromtimestamp(last_online_ms / 1000)
            days_offline = (NOW - last_online_dt).days
            if days_offline >= DELAY['awg_inactive']:
                results['awg_inactive'].append({**info, 'days_offline': days_offline})
                continue

        # Сценарий: Был онлайн 1-3 дня назад, перестал заходить
        if has_active_key and last_online_ms > 0:
            last_online_dt = datetime.utcfromtimestamp(last_online_ms / 1000)
            days_offline = (NOW - last_online_dt).days
            if DELAY['recently_inactive'] <= days_offline <= 3:
                results['recently_inactive'].append({**info, 'days_offline': days_offline})
                continue

        # Сценарий: Несколько типов конфигов, один не используется — 7 дней
        if has_active_key and len(info['vpn_types']) > 1 and key_age >= DELAY['multi_config_partial']:
            vless_keys = [k for k in user_keys if k['vpn_type'] == 'vless']
            other_keys = [k for k in user_keys if k['vpn_type'] != 'vless']
            if vless_keys and other_keys and total_bytes > 0:
                results['multi_config_partial'].append(info)

        # Сценарий: Повторное напоминание об истечении подписки (14 дней)
        if sub_until and sub_until < NOW:
            days_expired = (NOW - sub_until).days
            if days_expired == DELAY['second_expiry_reminder']:
                results['second_expiry_reminder'].append({**info, 'days_expired': days_expired})

        # Сценарий: Hysteria2 конфиг, не подключался 7+ дней
        has_hysteria = tg_id in hysteria_tg_ids
        if has_active_key and has_hysteria and last_online_ms > 0:
            last_online_dt = datetime.utcfromtimestamp(last_online_ms / 1000)
            days_offline = (NOW - last_online_dt).days
            if days_offline >= DELAY['hysteria_inactive']:
                results['hysteria_inactive'].append({**info, 'days_offline': days_offline})

        # Сценарий: Не заходил 7+ дней (был активен ранее)
        if has_active_key and last_online_ms > 0:
            last_online_dt = datetime.utcfromtimestamp(last_online_ms / 1000)
            days_offline = (NOW - last_online_dt).days
            if days_offline >= DELAY['long_inactive_7d']:
                results['long_inactive_7d'].append({**info, 'days_offline': days_offline})

        # Сценарий: Активный пользователь, но не пригласил друзей
        if has_active_key and total_bytes > 0:
            ref_count = get_referral_count(tg_id)
            if ref_count == 0:
                reg_age = (NOW - user['created_at']).days if user.get('created_at') else 0
                if reg_age >= DELAY['referral_prompt']:
                    results['referral_prompt'].append({**info, 'reg_days': reg_age})

    return results


# ─────────────────────────────────────────────
#  Сообщения
# ─────────────────────────────────────────────

MESSAGES = {
    'zero_traffic': (
        "👋 Привет!\n\n"
        "Мы заметили, что вы ещё не подключились к VPN. "
        "Нужна помощь с настройкой?\n\n"
        "📱 <b>Быстрый старт:</b>\n"
        "1️⃣ Нажмите <b>Мои конфиги</b>\n"
        "2️⃣ Скопируйте ссылку подписки\n"
        "3️⃣ Вставьте в приложение (Happ, Hiddify, Streisand)\n\n"
        "Если что-то не получается — напишите нам 💬"
    ),
    'low_traffic': (
        "👋 Привет!\n\n"
        "Похоже, VPN подключение не заработало как нужно. "
        "Мы можем помочь!\n\n"
        "Попробуйте:\n"
        "• Обновите ссылку подписки (Мои конфиги → скопируйте заново)\n"
        "• Используйте приложение <b>Happ</b> или <b>Hiddify</b>\n"
        "• Включите/выключите VPN заново\n\n"
        "Если не помогло — напишите в поддержку, разберёмся 💬"
    ),
    'expired_fresh': (
        "⏰ Ваша подписка недавно истекла.\n\n"
        "Продлите сейчас и получите бесперебойный доступ к VPN!\n\n"
        "🎁 <b>Персональный промокод:</b> <b>{promo_code}</b> — скидка 10% на любой тариф!\n"
        "Действует 7 дней, только для вас."
    ),
    'expired_old': (
        "👋 Давно не виделись!\n\n"
        "Мы обновили сервис — стало быстрее и стабильнее.\n\n"
        "🎁 <b>Подарок для возвращения:</b>\n"
        "Промокод <b>{promo_code}</b> — скидка 20% на любой тариф!\n"
        "Действует 14 дней, только для вас."
    ),
    'test_no_purchase': (
        "👋 Вы пробовали наш тестовый период.\n\n"
        "Готовы к полному доступу?\n\n"
        "🎁 <b>Специальное предложение:</b>\n"
        "Промокод <b>{promo_code}</b> — скидка 15% на первый тариф!\n"
        "Действует 7 дней, только для вас.\n\n"
        "Выберите тариф — подписка от {price} ₽/мес с доступом ко всем сайтам 🌐"
    ),
    'test_no_connect': (
        "👋 Вы активировали тестовый период, но так и не подключились.\n\n"
        "Мы продлили вам доступ на <b>1 день</b> — попробуйте прямо сейчас!\n\n"
        "🎁 <b>Бонус за возвращение:</b>\n"
        "Промокод <b>{promo_code}</b> — скидка 15% на первый тариф!\n"
        "Действует 7 дней.\n\n"
        "📱 <b>Быстрый старт:</b>\n"
        "1️⃣ Нажмите <b>Мои конфиги</b>\n"
        "2️⃣ Скопируйте ссылку подписки\n"
        "3️⃣ Вставьте в приложение (Happ, Hiddify, Streisand)\n\n"
        "Если что-то не получается — напишите нам, поможем! 💬"
    ),
    'payment_no_config': (
        "⚠️ Мы обнаружили, что ваш платёж был успешным, "
        "но VPN конфиг не был создан.\n\n"
        "Мы уже разбираемся с этим. Если вопрос не решится в ближайшее время — "
        "напишите в поддержку 💬"
    ),
    'panel_db_mismatch': (
        "⚠️ Обнаружена проблема с вашим конфигом. "
        "Мы уже работаем над исправлением.\n\n"
        "Если VPN не подключается — напишите в поддержку 💬"
    ),
    'never_activated': (
        "👋 Привет!\n\n"
        "Вы зарегистрировались, но ещё не попробовали VPN.\n"
        "Активируйте <b>бесплатный тест</b> — это займёт пару минут!\n\n"
        "🔒 Безопасный интернет без ограничений."
    ),
    'vless_only_inactive': (
        "👋 Заметили, что вы не подключались к VPN больше суток.\n\n"
        "Если есть проблемы с подключением — попробуйте протокол "
        "<b>AmneziaWG</b>. Он лучше работает на нестабильных каналах, "
        "мобильном интернете и в удалённых регионах.\n\n"
        "Нажмите кнопку ниже — мы выдадим вам конфиг AmneziaWG "
        "в дополнение к текущему VLESS."
    ),
    'awg_inactive': (
        "👋 Привет!\n\n"
        "Заметили, что вы давно не подключались к VPN. "
        "Всё ли в порядке?\n\n"
        "Если возникли вопросы или проблемы с подключением — "
        "напишите нам, поможем! 💬"
    ),
    'recently_inactive': (
        "👋 Мы скучаем!\n\n"
        "Заметили, что вы давно не заходили. "
        "Всё ли в порядке с подключением?\n\n"
        "💡 Кстати, у нас есть бесплатный прокси для Telegram — "
        "работает без VPN, просто нажмите кнопку ниже."
    ),
    'second_expiry_reminder': (
        "⏰ Напоминаем: ваша подписка истекла {days_expired} дней назад.\n\n"
        "Продлите сейчас, чтобы вернуть доступ к VPN!\n\n"
        "💡 <b>Специальное предложение для вас:</b>\n"
        "Промокод <b>{promo_code}</b> — скидка 20% на любой тариф!\n"
        "Действует 14 дней, только для вас."
    ),
    'hysteria_inactive': (
        "👋 Привет!\n\n"
        "Заметили, что вы давно не подключались к <b>Hysteria 2</b>.\n\n"
        "Этот протокол отлично работает для обхода жёстких блокировок.\n"
        "Если возникли проблемы — напишите нам, поможем настроить! 💬"
    ),
    'long_inactive_7d': (
        "👋 Давно не виделись!\n\n"
        "Вы не заходили к нам больше недели. "
        "Мы обновили сервис — стало быстрее и стабильнее!\n\n"
        "Возвращайтесь — будем рады видеть вас снова 🎁"
    ),
    'referral_prompt': (
        "👋 Привет!\n\n"
        "Вы с нами уже <b>{reg_days} дней</b> — надеемся, всё работает отлично!\n\n"
        "💡 <b>Знали ли вы, что можно получать VPN бесплатно?</b>\n\n"
        "Пригласите друга — и вы оба получите бонус:\n"
        "🎁 Вы: <b>+10 дней</b> подписки\n"
        "🎁 Друг: <b>+3 дня</b> бесплатно\n\n"
        "Делитесь ссылкой прямо сейчас!"
    ),
}


def get_buttons_for_scenario(scenario, tg_id=None):
    from api.db import get_web_token
    token = get_web_token(tg_id) if tg_id else None
    instr_url = f"https://344988.snk.wtf/my/{token}" if token else "https://344988.snk.wtf/my/"
    if scenario in ('zero_traffic', 'low_traffic', 'panel_db_mismatch'):
        return [
            [{"text": "📱 Мои конфиги", "callback_data": "my_configs"}],
            [{"text": "📋 Инструкция", "url": instr_url}],
        ]
    elif scenario == 'test_no_connect':
        return [
            [{"text": "📱 Мои конфиги", "callback_data": "my_configs"}],
            [{"text": "📋 Инструкция", "url": instr_url}],
            [{"text": "💎 Тарифы", "callback_data": "tariffs"}],
        ]
    elif scenario in ('expired_fresh', 'expired_old', 'test_no_purchase'):
        return [
            [{"text": "💎 Тарифы со скидкой", "callback_data": "tariffs"}],
        ]
    elif scenario == 'payment_no_config':
        return [
            [{"text": "💬 Написать нам", "url": "https://t.me/tiin_service_bot"}],
        ]
    elif scenario == 'never_activated':
        return [
            [{"text": "🎁 Активировать тест", "callback_data": "test_period"}],
            [{"text": "💎 Тарифы", "callback_data": "tariffs"}],
        ]
    elif scenario == 'vless_only_inactive':
        return [
            [{"text": "⚡ Получить AmneziaWG конфиг", "callback_data": "get_awg_config"}],
            [{"text": "📱 Мои конфиги", "callback_data": "my_configs"}],
        ]
    elif scenario == 'awg_inactive':
        return [
            [{"text": "📱 Мои конфиги", "callback_data": "my_configs"}],
            [{"text": "💬 Написать нам", "url": "https://t.me/tiin_service_bot"}],
        ]
    elif scenario == 'recently_inactive':
        return [
            [{"text": "🌐 Прокси для Telegram", "callback_data": "tg_proxy"}],
            [{"text": "📱 Мои конфиги", "callback_data": "my_configs"}],
        ]
    elif scenario == 'second_expiry_reminder':
        return [
            [{"text": "💎 Продлить со скидкой", "callback_data": "tariffs"}],
        ]
    elif scenario == 'hysteria_inactive':
        return [
            [{"text": "📱 Мои конфиги", "callback_data": "my_configs"}],
            [{"text": "💬 Написать нам", "url": "https://t.me/tiin_service_bot"}],
        ]
    elif scenario == 'long_inactive_7d':
        return [
            [{"text": "💎 Тарифы", "callback_data": "tariffs"}],
            [{"text": "📱 Мои конфиги", "callback_data": "my_configs"}],
        ]
    elif scenario == 'referral_prompt':
        return [
            [{"text": "👥 Получить реферальную ссылку", "callback_data": "referral"}],
            [{"text": "📱 Мои конфиги", "callback_data": "my_configs"}],
        ]
    return []


# ─────────────────────────────────────────────
#  Отчёт и отправка
# ─────────────────────────────────────────────

def print_report(results):
    print(f"\n{'='*60}")
    print(f"  ТАЙМИНГИ (дней после события) | cooldown: {COOLDOWN_DAYS}д")
    print(f"{'='*60}")
    for k, v in DELAY.items():
        print(f"  {k:<25} {v}д")

    total = 0
    for scenario, users in results.items():
        if not users:
            continue
        print(f"\n{'='*60}")
        print(f"  {scenario.upper()} ({len(users)} пользователей) [задержка: {DELAY.get(scenario, '?')}д]")
        print(f"{'='*60}")
        for u in users:
            extra = ""
            if 'days_expired' in u:
                extra = f" | истёк {u['days_expired']}д назад"
            if 'days_since_test' in u:
                extra += f" | тест {u['days_since_test']}д назад"
            if 'reg_days' in u:
                extra += f" | рег {u['reg_days']}д назад"
            if 'days_offline' in u:
                extra += f" | офлайн {u['days_offline']}д"
            print(
                f"  tg_id={str(u['tg_id'] or 0):<12} "
                f"name={str(u.get('name') or ''):<15} "
                f"traffic={u['total_mb']:.1f}MB "
                f"keys={u['keys']} "
                f"types={u['vpn_types']} "
                f"paid={u['paid_count']}"
                f"{extra}"
            )
        total += len(users)
    print(f"\n{'='*60}")
    print(f"  ИТОГО: {total} пользователей для возврата")
    print(f"{'='*60}\n")


def _extend_test_keys(tg_id: int):
    """Extend all expired test keys for user by 1 day from now."""
    tomorrow = NOW + timedelta(days=1)
    execute_query(
        "UPDATE vpn_keys SET expires_at = %s "
        "WHERE tg_id = %s AND expires_at < NOW()",
        (tomorrow, tg_id),
    )
    # Extend VLESS keys in x-ui panel
    try:
        xui = XUIClient(XUI_HOST, XUI_USERNAME, XUI_PASSWORD)
        inbounds = xui.get_inbounds()
        one_day_ms = 24 * 60 * 60 * 1000
        for ib in inbounds:
            raw_settings = ib.get("settings", "{}")
            settings = raw_settings if isinstance(raw_settings, dict) else json.loads(raw_settings)
            for client in settings.get("clients", []):
                if str(tg_id) in client.get("email", ""):
                    expiry = client.get("expiryTime", 0)
                    if 0 < expiry < int(NOW.timestamp() * 1000):
                        xui.extend_client_expiry(ib["id"], client, one_day_ms)
                        log.info(f"  Extended VLESS key {client['email']} in x-ui for {tg_id}")
    except Exception as e:
        log.warning(f"  Failed to extend VLESS in x-ui for {tg_id}: {e}")


MAX_MESSAGES = 3  # Максимальное количество сообщений одному пользователю

async def send_messages(results):
    from bot_xui.messaging import send_link_safely

    recent = get_recent_sends()
    sent = 0
    skipped = 0
    failed = 0

    # Count previous sends for all candidates to check the cap
    sent_counts = {}
    rows = execute_query("SELECT tg_id, COUNT(*) as c FROM winback_log GROUP BY tg_id", fetch='all') or []
    sent_counts = {r['tg_id']: r['c'] for r in rows}

    for scenario, users in results.items():
        if not users or scenario == 'multi_config_partial':
            continue
        if scenario == 'zero_traffic':
            continue

        for u in users:
            tg_id = u['tg_id']
            if not tg_id:
                log.info(f"⏭ Skip user_id={u.get('id','?')} [{scenario}] — no tg_id")
                skipped += 1
                continue

            # Cooldown check
            if tg_id in recent:
                days_ago = (NOW - recent[tg_id]).days
                log.info(f"⏭ Skip {tg_id} [{scenario}] — последняя отправка {days_ago}д назад")
                skipped += 1
                continue

            # Cap check
            current_count = sent_counts.get(tg_id, 0)
            if current_count >= MAX_MESSAGES:
                log.info(f"⏭ Skip {tg_id} [{scenario}] — достигнут лимит {MAX_MESSAGES} сообщений")
                skipped += 1
                continue

            # Prepare final message if limit reached
            is_final = (current_count + 1 == MAX_MESSAGES)
            if is_final:
                msg = (
                    "👋 Привет!\n\n"
                    "Мы заметили, что наш сервис вас не заинтересовал. "
                    "Не будем вас беспокоить — отключаем уведомления.\n\n"
                    "Если передумаете — просто запустите бота командой /start в любое время!"
                )
                buttons = []
            else:
                msg_template = MESSAGES.get(scenario)
                if not msg_template: continue
                msg = msg_template
                if '{price}' in msg:
                    from bot_xui.tariffs import TARIFFS
                    min_price = min(t['price'] for t in TARIFFS.values() if t['price'] > 0)
                    msg = msg.replace('{price}', str(min_price))
                if '{days_expired}' in msg:
                    msg = msg.replace('{days_expired}', str(u.get('days_expired', '')))
                if '{reg_days}' in msg:
                    msg = msg.replace('{reg_days}', str(u.get('reg_days', '')))
                if '{promo_code}' in msg:
                    promo_code = create_winback_promo(tg_id, promo_type='discount', value=15, days_valid=14)
                    msg = msg.replace('{promo_code}', promo_code)
                buttons = get_buttons_for_scenario(scenario, tg_id)

                # Extend expired test keys for specific scenario
                if scenario == 'test_no_connect':
                    _extend_test_keys(tg_id)

            ok = await send_link_safely(
                tg_id=tg_id,
                text=msg,
                parse_mode="HTML",
                buttons=buttons if buttons else None,
                source="cron_winback", scenario="final" if is_final else scenario,
            )
            if ok:
                sent += 1
                log_send(tg_id, "final" if is_final else scenario)
                recent[tg_id] = NOW
                sent_counts[tg_id] = current_count + 1
                log.info(f"✅ Sent [{'final' if is_final else scenario}] to {tg_id}")
            else:
                failed += 1
                log.warning(f"❌ Failed to {tg_id}")

    print(f"\nОтправлено: {sent}, пропущено (cooldown/limit): {skipped}, ошибок: {failed}")


# ─────────────────────────────────────────────
#  Main
# ─────────────────────────────────────────────

def main():
    send_mode = '--send' in sys.argv

    log.info("Сбор данных...")
    xui = XUIClient(XUI_HOST, XUI_USERNAME, XUI_PASSWORD)
    users = get_all_users()
    keys_by_tg = get_all_keys()
    payments_by_tg = get_all_payments()
    traffic = get_traffic_from_panel(xui)
    hysteria_tg_ids = get_hysteria_clients_from_panel(xui)

    log.info(f"Пользователей: {len(users)}, с ключами: {len(keys_by_tg)}, с трафиком: {len(traffic)}, с hysteria2: {len(hysteria_tg_ids)}")

    results = classify_users(users, keys_by_tg, payments_by_tg, traffic, hysteria_tg_ids)
    print_report(results)

    if send_mode:
        print("⚠️  Режим отправки. Отправляю сообщения...")
        asyncio.run(send_messages(results))
    else:
        print("ℹ️  Режим просмотра. Для отправки: python3 scripts/win_back_users.py --send")


if __name__ == "__main__":
    main()
