#!/usr/bin/env python3
"""
Новая система возврата пользователей (Winback & Retention Funnel).
Чистая 4-этапная логика, защита от спама (cooldown 7 дней, лимит 3 пуша).

Запуск:
  python3 scripts/win_back_users.py         — только отчёт (Dry-Run)
  python3 scripts/win_back_users.py --send  — боевая отправка
"""
import sys
import os
import json
import asyncio
import logging
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.db import execute_query, create_winback_promo, get_referral_count, set_winback_discount, sync_expiry
from bot_xui.utils import XUIClient
from bot_xui.messaging import send_link_safely
from config import XUI_HOST, XUI_USERNAME, XUI_PASSWORD, ADMIN_TG_ID

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("winback")

MB = 1024 * 1024
NOW = datetime.utcnow()
COOLDOWN_DAYS = 7     # Минимум 7 дней тишины между любыми сообщениями
MAX_MESSAGES = 3      # Жесткий лимит сообщений на пользователя

# ─────────────────────────────────────────────
#  Логирование и проверки
# ─────────────────────────────────────────────

def get_recent_sends() -> dict:
    """Получить пользователей, которым отправляли за последние COOLDOWN_DAYS дней."""
    rows = execute_query(
        "SELECT tg_id, MAX(sent_at) as last_sent "
        "FROM winback_log "
        "WHERE sent_at > %s "
        "GROUP BY tg_id",
        (NOW - timedelta(days=COOLDOWN_DAYS),),
        fetch='all',
    ) or []
    return {r['tg_id']: r['last_sent'] for r in rows}

def get_total_sends_count() -> dict:
    """Получить общее количество winback-отправок по каждому tg_id."""
    rows = execute_query("SELECT tg_id, COUNT(*) as c FROM winback_log GROUP BY tg_id", fetch='all') or []
    return {r['tg_id']: r['c'] for r in rows}

def log_send(tg_id: int, scenario: str):
    """Записать отправку в лог."""
    execute_query(
        "INSERT INTO winback_log (tg_id, scenario) VALUES (%s, %s)",
        (tg_id, scenario),
    )

# ─────────────────────────────────────────────
#  Сбор данных
# ─────────────────────────────────────────────

def get_all_users():
    return execute_query(
        "SELECT tg_id, first_name, subscription_until, test_vless_activated, "
        "test_awg_activated, created_at "
        "FROM users WHERE bot_blocked = 0",
        fetch='all',
    ) or []

def get_all_keys():
    rows = execute_query(
        "SELECT tg_id, client_name, vpn_type, expires_at, created_at FROM vpn_keys",
        fetch='all',
    ) or []
    keys_by_tg = {}
    for r in rows:
        keys_by_tg.setdefault(r['tg_id'], []).append(r)
    return keys_by_tg

def get_all_payments():
    rows = execute_query(
        "SELECT tg_id, tariff, amount, status, created_at FROM payments WHERE status = 'paid'",
        fetch='all',
    ) or []
    payments_by_tg = {}
    for r in rows:
        payments_by_tg.setdefault(r['tg_id'], []).append(r)
    return payments_by_tg

def _add_traffic(traffic, tg_id, upload, download, enabled=True, last_online=0):
    existing = traffic.get(tg_id, {'upload': 0, 'download': 0, 'enabled': True, 'last_online': 0})
    existing['upload'] += upload
    existing['download'] += download
    if not enabled:
        existing['enabled'] = False
    if last_online > existing.get('last_online', 0):
        existing['last_online'] = last_online
    traffic[tg_id] = existing

def get_traffic_from_panel(xui):
    traffic = {}
    try:
        inbounds = xui.get_inbounds()
        email_to_tg = {}
        for ib in inbounds:
            settings = ib.get('settings', {})
            if isinstance(settings, str):
                settings = json.loads(settings)
            for client in settings.get('clients', []):
                if client.get('tgId'):
                    email_to_tg[client.get('email')] = int(client['tgId'])

        for ib in inbounds:
            for cs in ib.get('clientStats', []):
                tg_id = email_to_tg.get(cs.get('email'))
                if tg_id:
                    _add_traffic(traffic, tg_id, cs.get('up', 0), cs.get('down', 0),
                                 cs.get('enable', True), cs.get('lastOnline', 0))
    except Exception as e:
        log.warning(f"3x-ui traffic collection error: {e}")

    # AWG Traffic
    try:
        import subprocess
        rows = execute_query("SELECT client_name, tg_id FROM vpn_keys", fetch='all') or []
        name_to_tg = {r['client_name']: r['tg_id'] for r in rows}

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
            if name and name in name_to_tg:
                tg_id = name_to_tg[name]
                last_online_ms = handshake_ts * 1000 if handshake_ts else 0
                _add_traffic(traffic, tg_id, rx_bytes, tx_bytes, last_online=last_online_ms)
    except Exception as e:
        log.warning(f"AWG traffic error: {e}")

    return traffic

# ─────────────────────────────────────────────
#  Кнопки и шаблоны сообщений
# ─────────────────────────────────────────────

MESSAGES = {
    'expired_2d': (
        "⏰ <b>Ваша подписка завершилась 2 дня назад.</b>\n\n"
        "Мы хотим, чтобы ваш интернет оставался быстрым и свободным без перебоев!\n\n"
        "🎁 <b>Специальный промокод:</b> <code>{promo_code}</code> — скидка 15% на любой тариф.\n\n"
        "<i>Нажмите на промокод, чтобы скопировать, и отправьте в чат:</i>\n"
        "<code>/promo {promo_code}</code>"
    ),
    'test_ended_2d': (
        "👋 <b>Тестовый период завершён.</b>\n\n"
        "Надеемся, вам понравилась скорость и стабильность работы VPN!\n\n"
        "🎁 <b>Скидка на первый тариф:</b> промокод <code>{promo_code}</code> (-15%).\n\n"
        "<i>Отправьте в чат:</i> <code>/promo {promo_code}</code> и выберите удобный тариф 🌐"
    ),
    'test_0mb_2d': (
        "👋 <b>Вы активировали тест, но так и не подключились.</b>\n\n"
        "Мы продлили вам доступ ещё на <b>1 день</b>, чтобы вы смогли всё проверить!\n\n"
        "📱 <b>Как подключиться в 3 шага:</b>\n"
        "1️⃣ Нажмите кнопку <b>«Мои конфиги»</b> ниже\n"
        "2️⃣ Скопируйте ссылку подписки\n"
        "3️⃣ Вставьте в приложение (Shadowrocket, Happ, Hiddify)\n\n"
        "Если не получается — напишите нам, поможем настроить! 💬"
    ),
    'never_activated_3d': (
        "👋 <b>Добро пожаловать в TIIN Service!</b>\n\n"
        "Вы зарегистрировались, но ещё не опробовали наш VPN.\n\n"
        "🎁 Активируйте <b>бесплатный пробный период</b> прямо сейчас — это займёт меньше минуты!"
    ),
    'protocol_inactive_7d': (
        "👋 <b>Заметили, что вы не подключались к VPN больше недели.</b>\n\n"
        "Если ваш оператор начал блокировать стандартный протокол — попробуйте <b>AmneziaWG</b> или <b>Hysteria 2</b>.\n"
        "Они специально разработаны для обхода жестких блокировок мобильных операторов.\n\n"
        "Все протоколы доступны в вашем меню конфигов!"
    ),
    'loyalty_gift_10d': (
        "🎁 <b>Вам подарок от TIIN Service!</b>\n\n"
        "Мы ценим каждого пользователя, даже если вы временно перестали пользоваться сервисом.\n\n"
        "В связи с этим дарим вам <b>3 дня бесплатного доступа</b> ко всем протоколам!\n\n"
        "Нажмите кнопку ниже, чтобы активировать подарок в любое удобное время 👇\n\n"
        "<i>С уважением, Команда TIIN Service</i>"
    ),
    'loyalty_survey': (
        "👋 <b>Сегодня последний день подарочного доступа.</b>\n\n"
        "Пожалуйста, оцените качество работы сервиса — это поможет нам стать лучше:"
    ),
    'loyalty_final_farewell': (
        "🔒 <b>Подарочный период завершён.</b>\n\n"
        "Ваш ключ сохранён. Мы закрепили за вашим аккаунтом единовременную скидку <b>20%</b> на первое продление любого тарифа.\n\n"
        "Больше не будем беспокоить вас уведомлениями. Если решите вернуться — мы всегда на связи по команде /start!"
    )
}

def get_buttons_for_scenario(scenario: str, tg_id: int = None):
    from api.db import get_web_token
    token = get_web_token(tg_id) if tg_id else None
    instr_url = f"https://344988.snk.wtf/my/{token}" if token else "https://344988.snk.wtf/my/"

    if scenario in ('expired_2d', 'test_ended_2d'):
        return [[{"text": "💎 Тарифы со скидкой 15%", "callback_data": "tariffs"}]]
    elif scenario == 'test_0mb_2d':
        return [
            [{"text": "📱 Мои конфиги", "callback_data": "my_configs"}],
            [{"text": "📋 Инструкция", "url": instr_url}],
        ]
    elif scenario == 'never_activated_3d':
        return [[{"text": "🎁 Активировать тест", "callback_data": "test_period"}]]
    elif scenario == 'protocol_inactive_7d':
        return [
            [{"text": "📱 Мои конфиги", "callback_data": "my_configs"}],
            [{"text": "💬 Поддержка", "url": "https://t.me/tiinsupport"}]
        ]
    elif scenario == 'loyalty_gift_10d':
        return [[{"text": "🎁 Активировать 3 дня", "callback_data": "wb_activate_gift"}]]
    elif scenario == 'loyalty_survey':
        return [
            [
                {"text": "👍 Отлично", "callback_data": "wb_rate:good"},
                {"text": "😐 Нормально", "callback_data": "wb_rate:normal"},
                {"text": "👎 Плохо", "callback_data": "wb_rate:bad"},
            ],
            [{"text": "💬 Поддержка", "url": "https://t.me/tiinsupport"}]
        ]
    elif scenario == 'loyalty_final_farewell':
        return [[{"text": "💎 Тарифы со скидкой 20%", "callback_data": "tariffs"}]]
    return []

# ─────────────────────────────────────────────
#  Классификатор воронки (Smart Funnel)
# ─────────────────────────────────────────────

def classify_funnel(users, keys_by_tg, payments_by_tg, traffic):
    """
    Классифицирует пользователей по строгой, непересекающейся воронке.
    """
    candidates = []

    for user in users:
        tg_id = user['tg_id']
        if not tg_id or tg_id == ADMIN_TG_ID:
            continue

        user_keys = keys_by_tg.get(tg_id, [])
        user_payments = payments_by_tg.get(tg_id, [])
        user_traffic = traffic.get(tg_id, {'upload': 0, 'download': 0, 'enabled': True, 'last_online': 0})
        total_bytes = user_traffic['upload'] + user_traffic['download']
        sub_until = user.get('subscription_until')
        created_at = user.get('created_at')

        has_active_sub = sub_until and sub_until > NOW
        has_paid_before = len(user_payments) > 0
        test_activated = user.get('test_vless_activated') or user.get('test_awg_activated')

        info = {
            'tg_id': tg_id,
            'name': user.get('first_name', ''),
            'total_mb': round(total_bytes / MB, 2),
            'paid_count': len(user_payments),
            'sub_until': sub_until,
            'user_keys': user_keys
        }

        # 1. Зарегистрировался 3+ дня назад, но тест не активировал и ключей нет
        if not test_activated and not user_keys and not has_paid_before:
            reg_days = (NOW - created_at).days if created_at else 0
            if reg_days >= 3:
                candidates.append({**info, 'scenario': 'never_activated_3d', 'priority': 5})
            continue

        # 2. Тест завершился 2+ дня назад, оплат нет
        if test_activated and not has_paid_before and not has_active_sub:
            # Находим дату окончания теста
            test_end_dates = [k['expires_at'] for k in user_keys if k['expires_at']]
            if test_end_dates:
                latest_test_end = max(test_end_dates)
                days_since_test = (NOW - latest_test_end).days
                if days_since_test >= 2:
                    if total_bytes == 0:
                        candidates.append({**info, 'scenario': 'test_0mb_2d', 'priority': 2})
                    else:
                        candidates.append({**info, 'scenario': 'test_ended_2d', 'priority': 2})
                    continue

        # 3. Платная подписка истекла 2+ дня назад (Этап 1 воронки)
        if sub_until and sub_until <= NOW:
            days_expired = (NOW - sub_until).days
            # Если истекла 2-9 дней назад -> Этап 1: Скидка 15%
            if 2 <= days_expired < 10:
                candidates.append({**info, 'scenario': 'expired_2d', 'days_expired': days_expired, 'priority': 1})
                continue

        # 4. Пользователь с активной подпиской офлайн 7+ дней -> проверить блокировку
        if has_active_sub:
            last_online_ms = user_traffic.get('last_online', 0)
            if last_online_ms > 0:
                last_online_dt = datetime.utcfromtimestamp(last_online_ms / 1000)
                days_offline = (NOW - last_online_dt).days
                if days_offline >= 7:
                    candidates.append({**info, 'scenario': 'protocol_inactive_7d', 'days_offline': days_offline, 'priority': 4})
                    continue

    return candidates

# ─────────────────────────────────────────────
#  Кампания лояльности (10d -> 3d survey -> 4d farewell)
# ─────────────────────────────────────────────

async def process_loyalty_campaign(send_mode: bool = False):
    """
    Кампания лояльности:
    1. Через 10 дней после конца подписки -> Подарок +3 дня
    2. Через 60 часов подарка -> Опрос в 1 клик
    3. После окончания подарка -> Закрепление 20% скидки и финальное прощание
    """
    # 1. Подарок +3 дня (День 10)
    gift_users = execute_query(
        """SELECT u.tg_id, u.first_name, u.subscription_until
           FROM users u
           LEFT JOIN winback_log wl ON u.tg_id = wl.tg_id AND wl.scenario = 'loyalty_gift_10d'
           WHERE u.bot_blocked = 0
             AND u.tg_id IS NOT NULL AND u.tg_id > 0 AND u.tg_id != %s
             AND u.subscription_until <= NOW() - INTERVAL 10 DAY
             AND wl.id IS NULL""",
        (ADMIN_TG_ID,),
        fetch='all'
    ) or []

    log.info(f"🎁 [LOYALTY] Кандидатов на подарок +3 дня (10д+ после подписки): {len(gift_users)}")
    for u in gift_users:
        tg_id = u['tg_id']
        name = u.get('first_name') or 'Пользователь'
        if not send_mode:
            log.info(f"  [DRY-RUN] Подарок +3d для tg:{tg_id} ({name})")
            continue

        try:
            buttons = get_buttons_for_scenario('loyalty_gift_10d', tg_id)
            ok = await send_link_safely(
                tg_id=tg_id,
                text=MESSAGES['loyalty_gift_10d'],
                parse_mode="HTML",
                buttons=buttons,
                source="cron_loyalty",
                scenario="loyalty_gift_10d"
            )
            if ok:
                log_send(tg_id, "loyalty_gift_10d")
                log.info(f"✅ [LOYALTY] Предложение подарка +3d отправлено tg:{tg_id}")
            await asyncio.sleep(0.1)
        except Exception as e:
            log.error(f"❌ [LOYALTY] Ошибка отправки подарка tg:{tg_id}: {e}")

    # 2. Опрос лояльности (за 12ч до окончания подарка после активации)
    survey_users = execute_query(
        """SELECT u.tg_id, u.first_name
           FROM users u
           JOIN winback_log wl_gift ON u.tg_id = wl_gift.tg_id AND wl_gift.scenario = 'loyalty_gift_activated'
           LEFT JOIN winback_log wl_surv ON u.tg_id = wl_surv.tg_id AND wl_surv.scenario = 'loyalty_survey'
           WHERE u.bot_blocked = 0
             AND u.tg_id IS NOT NULL AND u.tg_id > 0 AND u.tg_id != %s
             AND wl_gift.sent_at <= NOW() - INTERVAL 60 HOUR
             AND u.subscription_until > NOW()
             AND wl_surv.id IS NULL""",
        (ADMIN_TG_ID,),
        fetch='all'
    ) or []

    log.info(f"📊 [LOYALTY] Кандидатов на опрос (День 3 подарка): {len(survey_users)}")
    for u in survey_users:
        tg_id = u['tg_id']
        if not send_mode:
            log.info(f"  [DRY-RUN] Опрос лояльности для tg:{tg_id}")
            continue

        try:
            buttons = get_buttons_for_scenario('loyalty_survey', tg_id)
            ok = await send_link_safely(
                tg_id=tg_id,
                text=MESSAGES['loyalty_survey'],
                parse_mode="HTML",
                buttons=buttons,
                source="cron_loyalty",
                scenario="loyalty_survey"
            )
            if ok:
                log_send(tg_id, "loyalty_survey")
                log.info(f"✅ [LOYALTY] Опрос отправлен tg:{tg_id}")
            await asyncio.sleep(0.1)
        except Exception as e:
            log.error(f"❌ [LOYALTY] Ошибка опроса tg:{tg_id}: {e}")

    # 3. Финал подарка + сохранение 20% скидки
    farewell_users = execute_query(
        """SELECT u.tg_id, u.first_name
           FROM users u
           JOIN winback_log wl_surv ON u.tg_id = wl_surv.tg_id AND wl_surv.scenario = 'loyalty_survey'
           LEFT JOIN winback_log wl_end ON u.tg_id = wl_end.tg_id AND wl_end.scenario = 'loyalty_final_farewell'
           WHERE u.bot_blocked = 0
             AND u.tg_id IS NOT NULL AND u.tg_id > 0 AND u.tg_id != %s
             AND u.subscription_until <= NOW()
             AND wl_end.id IS NULL""",
        (ADMIN_TG_ID,),
        fetch='all'
    ) or []

    log.info(f"🔒 [LOYALTY] Кандидатов на финальное закрепление 20% (День 4): {len(farewell_users)}")
    for u in farewell_users:
        tg_id = u['tg_id']
        if not send_mode:
            log.info(f"  [DRY-RUN] Финал лояльности для tg:{tg_id}")
            continue

        try:
            set_winback_discount(tg_id, True)
            buttons = get_buttons_for_scenario('loyalty_final_farewell', tg_id)
            ok = await send_link_safely(
                tg_id=tg_id,
                text=MESSAGES['loyalty_final_farewell'],
                parse_mode="HTML",
                buttons=buttons,
                source="cron_loyalty",
                scenario="loyalty_final_farewell"
            )
            if ok:
                log_send(tg_id, "loyalty_final_farewell")
                log.info(f"✅ [LOYALTY] Финал отправлен tg:{tg_id}")
            await asyncio.sleep(0.1)
        except Exception as e:
            log.error(f"❌ [LOYALTY] Ошибка финала tg:{tg_id}: {e}")

# ─────────────────────────────────────────────
#  Основной цикл отправки (Standard Funnel)
# ─────────────────────────────────────────────

async def send_standard_winback(candidates, send_mode: bool = False):
    recent = get_recent_sends()
    sent_counts = get_total_sends_count()

    candidates.sort(key=lambda x: x.get('priority', 99))

    sent = 0
    skipped = 0
    failed = 0

    for u in candidates:
        tg_id = u['tg_id']
        scenario = u['scenario']

        # 1. Cooldown Check (7 дней)
        if tg_id in recent:
            days_ago = (NOW - recent[tg_id]).days
            skipped += 1
            continue

        # 2. Max Messages Check (Лимит 3 пуша)
        count = sent_counts.get(tg_id, 0)
        if count >= MAX_MESSAGES:
            skipped += 1
            continue

        if not send_mode:
            log.info(f"  [DRY-RUN] Отправка [{scenario}] -> tg:{tg_id} ({u.get('name')})")
            continue

        try:
            # Создаем уникальный промокод если нужен
            promo_code = create_winback_promo(tg_id, promo_type='discount', value=15, days_valid=14)
            msg = MESSAGES[scenario].replace('{promo_code}', promo_code)
            buttons = get_buttons_for_scenario(scenario, tg_id)

            ok = await send_link_safely(
                tg_id=tg_id,
                text=msg,
                parse_mode="HTML",
                buttons=buttons,
                source="cron_winback",
                scenario=scenario
            )
            if ok:
                sent += 1
                log_send(tg_id, scenario)
                recent[tg_id] = NOW
                sent_counts[tg_id] = count + 1
                log.info(f"✅ Sent [{scenario}] to tg:{tg_id}")
            else:
                failed += 1
            await asyncio.sleep(0.1)
        except Exception as e:
            failed += 1
            log.error(f"❌ Ошибка отправки tg:{tg_id} [{scenario}]: {e}")

    if send_mode:
        print(f"\nИтоги отправки: Успешно: {sent}, Пропущено (cooldown/лимит): {skipped}, Ошибок: {failed}")

# ─────────────────────────────────────────────
#  Main
# ─────────────────────────────────────────────

def main():
    send_mode = '--send' in sys.argv

    log.info("Сбор данных пользователей и сетевого трафика...")
    xui = XUIClient(XUI_HOST, XUI_USERNAME, XUI_PASSWORD)
    users = get_all_users()
    keys_by_tg = get_all_keys()
    payments_by_tg = get_all_payments()
    traffic = get_traffic_from_panel(xui)

    log.info(f"Пользователей в базе: {len(users)}, активных ключей: {len(keys_by_tg)}")

    # 1. Анализ стандартной воронки
    candidates = classify_funnel(users, keys_by_tg, payments_by_tg, traffic)
    print(f"\n{'='*60}")
    print(f"  SMART WINBACK ВОРОНКА (Кандидатов: {len(candidates)}) | Cooldown: {COOLDOWN_DAYS}д, Max: {MAX_MESSAGES}")
    print(f"{'='*60}")
    for c in candidates:
        print(f"  tg_id={str(c['tg_id']):<12} name={str(c.get('name') or ''):<15} сценарий={c['scenario']:<20} трафик={c['total_mb']:.1f}MB")
    print(f"{'='*60}\n")

    # 2. Выполнение кампании лояльности (10d -> 3d survey -> 4d final)
    asyncio.run(process_loyalty_campaign(send_mode=send_mode))

    # 3. Выполнение стандартной воронки
    asyncio.run(send_standard_winback(candidates, send_mode=send_mode))

if __name__ == "__main__":
    main()
