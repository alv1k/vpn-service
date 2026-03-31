"""
Веб-портал для пользователей — fallback если Telegram заблокирован.
Персональная страница по токену: /my/{token}
"""
import base64
import html as html_mod
import json as json_mod
import logging
from datetime import datetime
from io import BytesIO

import qrcode
from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from api.db import get_user_by_web_token, get_keys_by_tg_id, get_keys_by_user_id, is_vless_test_activated_by_id

logger = logging.getLogger(__name__)
web_router = APIRouter()


def _generate_qr_base64(data: str) -> str:
    bio = BytesIO()
    qr = qrcode.QRCode(version=1, box_size=8, border=4)
    qr.add_data(data)
    qr.make(fit=True)
    qr.make_image(fill_color="black", back_color="white").save(bio, "PNG")
    return base64.b64encode(bio.getvalue()).decode()


def _format_date(dt):
    if not dt:
        return "—"
    if hasattr(dt, 'strftime'):
        return dt.strftime("%d.%m.%Y")
    return str(dt)


HAPP_ROUTING_CONFIG = {
    "blockip": [], "blocksites": [],
    "directip": ["geoip:private", "geoip:ru"],
    "directsites": [
        "geosite:category-ru", "geosite:category-gov-ru",
        "geosite:mailru", "geosite:vk",
        "domain:ru", "domain:su", "domain:xn--p1ai",
        "vk.com", "vk.cc", "vk.me", "cdn-vk.net",
        "tbank-online.com", "tinkoff.ru",
        "sberbank.ru", "online.sberbank.ru",
        "yandex.ru", "yandex.net", "yandex.com",
        "mail.ru", "list.ru", "inbox.ru",
        "ok.ru", "odnoklassniki.ru",
        "avito.ru", "ozon.ru",
        "wildberries.ru", "wb.ru",
        "gosuslugi.ru", "mos.ru",
        "lenta.com", "lenta.tech",
        "kinopoisk.ru", "ivi.ru", "rutube.ru",
    ],
    "dnshosts": {"cloudflare-dns.com": "1.1.1.1", "dns.google": "8.8.8.8"},
    "domainstrategy": "IPIfNonMatch",
    "domesticdnsdomain": "https://dns.google/dns-query",
    "domesticdnsip": "77.88.8.8", "domesticdnstype": "DoH",
    "fakedns": False,
    "geoipurl": "https://github.com/Loyalsoldier/v2ray-rules-dat/releases/latest/download/geoip.dat",
    "geositeurl": "https://github.com/Loyalsoldier/v2ray-rules-dat/releases/latest/download/geosite.dat",
    "globalproxy": True, "name": "Tiin Split Rules",
    "proxyip": [], "proxysites": [],
    "remotednsdomain": "https://cloudflare-dns.com/dns-query",
    "remotednsip": "77.88.8.1", "remotednstype": "DoU",
    "routeorder": "block-proxy-direct",
}


def _happ_routing_deeplink() -> str:
    b64 = base64.b64encode(json_mod.dumps(HAPP_ROUTING_CONFIG, ensure_ascii=False).encode()).decode()
    return f"happ://routing/add/{b64}"


@web_router.get("/happ-routing", response_class=HTMLResponse)
async def happ_routing_redirect():
    deeplink = _happ_routing_deeplink()
    b64 = deeplink.split("happ://routing/add/")[1]
    intent_link = f"intent://routing/add/{b64}#Intent;scheme=happ;end"
    return f"""<!DOCTYPE html>
<html><head>
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Tiin — настройка маршрутизации</title>
<link rel="icon" type="image/png" href="/favicon.png">
<style>
  body {{ font-family: -apple-system, sans-serif; text-align: center; padding: 40px 20px; background: #f5f5f5; }}
  .btn {{ display: inline-block; padding: 16px 32px; background: #007aff; color: white;
          border-radius: 12px; text-decoration: none; font-size: 18px; margin: 10px; }}
</style>
</head><body>
<h2>Настройка маршрутизации</h2>
<p>Нажмите кнопку, чтобы добавить правила в Happ:</p>
<a class="btn" id="openBtn" href="{deeplink}">📲 Открыть в Happ</a>
<p style="margin-top:20px;color:#888;font-size:14px;">Если не открылось автоматически, нажмите кнопку выше.</p>
<script>
  // Try direct deep link first
  var dl = "{deeplink}";
  var intentDl = "{intent_link}";
  var isAndroid = /android/i.test(navigator.userAgent);
  if (isAndroid) {{
    document.getElementById('openBtn').href = intentDl;
  }}
  setTimeout(function() {{ window.location.href = isAndroid ? intentDl : dl; }}, 300);
</script>
</body></html>"""


@web_router.get("/my/{token}", response_class=HTMLResponse)
async def personal_page(token: str):
    user = get_user_by_web_token(token)
    if not user:
        return HTMLResponse(_page_not_found(), status_code=404)

    tg_id = user['tg_id']
    sub_until = user.get('subscription_until')
    now = datetime.now()
    is_active = sub_until and sub_until > now

    keys = get_keys_by_tg_id(tg_id) if tg_id else []
    if not keys:
        keys = get_keys_by_user_id(user['id'])
    vless_keys = [k for k in keys if k['vpn_type'] == 'vless' and k.get('subscription_link')]
    active_vless = [k for k in vless_keys if k['expires_at'] and k['expires_at'] > now]

    # Берём первую активную ссылку подписки
    sub_url = active_vless[0]['subscription_link'] if active_vless else ""
    qr_b64 = _generate_qr_base64(sub_url) if sub_url else ""

    test_used = is_vless_test_activated_by_id(user['id'])

    return HTMLResponse(_render_page(
        name=html_mod.escape(user.get('first_name') or 'Пользователь'),
        is_active=is_active,
        sub_until=_format_date(sub_until),
        sub_url=sub_url,
        qr_b64=qr_b64,
        happ_routing_link=_happ_routing_deeplink(),
        email=html_mod.escape(user.get('email') or ''),
        web_token=token,
        test_used=test_used,
    ))


# ─────────────────────────────────────────────
#  HTML
# ─────────────────────────────────────────────

def _page_not_found():
    return """<!DOCTYPE html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Не найдено</title>
<link rel="icon" type="image/png" href="/favicon.png">
<style>body{font-family:-apple-system,system-ui,sans-serif;display:flex;justify-content:center;align-items:center;
min-height:100vh;margin:0;background:#0a0a0a;color:#fff}
.c{text-align:center;padding:2rem}h1{font-size:3rem;margin:0}p{color:#888;margin-top:1rem}</style>
</head><body><div class="c"><h1>404</h1><p>Страница не найдена</p></div></body></html>"""


def _render_page(name, is_active, sub_until, sub_url, qr_b64, happ_routing_link="", email="", web_token="", test_used=False):
    status_color = "#22c55e" if is_active else "#ef4444"
    status_text = "Активна" if is_active else "Неактивна"
    status_dot = "&#9679;"

    return f"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>TIIN - Личный кабинет</title>
<link rel="icon" type="image/png" href="/favicon.png">
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:-apple-system,system-ui,'Segoe UI',sans-serif;background:#0a0a0a;color:#e5e5e5;
min-height:100vh;padding:1rem;-webkit-font-smoothing:antialiased}}
.container{{max-width:420px;margin:0 auto}}
.header{{text-align:center;padding:2rem 0 1rem}}
.header h1{{font-size:1.5rem;color:#fff}}
.header .sub{{color:#888;font-size:.85rem;margin-top:.3rem}}
.card{{background:#161616;border:1px solid #262626;border-radius:12px;padding:1.2rem;margin-bottom:1rem}}
.card h2{{font-size:.9rem;color:#888;text-transform:uppercase;letter-spacing:.05em;margin-bottom:.8rem}}
.status{{display:flex;align-items:center;gap:.5rem;font-size:1rem}}
.status .dot{{color:{status_color};font-size:1.4rem;line-height:1}}
.status .text{{color:{status_color};font-weight:600}}
.expiry{{color:#888;font-size:.85rem;margin-top:.4rem}}
.qr-wrap{{text-align:center;padding:1rem 0}}
.qr-wrap img{{width:200px;height:200px;border-radius:8px;background:#fff;padding:8px}}
.sub-link{{background:#1a1a1a;border:1px solid #333;border-radius:8px;padding:.7rem;
font-size:.7rem;word-break:break-all;color:#a78bfa;font-family:monospace;cursor:pointer;
position:relative;text-align:center}}
.sub-link:active{{background:#222}}
.copied{{position:fixed;top:1rem;left:50%;transform:translateX(-50%);background:#22c55e;color:#fff;
padding:.5rem 1.2rem;border-radius:8px;font-size:.85rem;opacity:0;transition:opacity .3s;
pointer-events:none;z-index:99}}
.copied.show{{opacity:1}}

/* Wizard */
.step{{display:none}}.step.active{{display:block}}
.device-grid{{display:grid;grid-template-columns:1fr 1fr;gap:.6rem;margin-top:.8rem}}
.device-btn{{background:#1a1a1a;border:1px solid #333;border-radius:10px;padding:1rem .5rem;
text-align:center;cursor:pointer;transition:all .15s;-webkit-tap-highlight-color:transparent}}
.device-btn:hover,.device-btn:active{{border-color:#a78bfa;background:#1f1a2e}}
.device-btn .icon{{font-size:2rem;margin-bottom:.3rem}}
.device-btn .label{{font-size:.8rem;color:#ccc}}
.app-card{{background:#1a1a1a;border:1px solid #333;border-radius:10px;padding:1rem;margin-bottom:.6rem;
display:flex;align-items:center;gap:.8rem;cursor:pointer;transition:all .15s}}
.app-card:hover,.app-card:active{{border-color:#a78bfa;background:#1f1a2e}}
.app-card .app-icon{{font-size:2rem;width:48px;text-align:center;flex-shrink:0}}
.app-card .app-info{{flex:1}}.app-card .app-name{{font-weight:600;color:#fff;font-size:.95rem}}
.app-card .app-desc{{font-size:.75rem;color:#888;margin-top:.2rem}}
.connect-btn{{display:block;width:100%;padding:1rem;border:none;border-radius:10px;
font-size:1rem;font-weight:600;cursor:pointer;text-align:center;text-decoration:none;
margin-top:.8rem;transition:all .15s}}
.connect-btn.primary{{background:#7c3aed;color:#fff}}
.connect-btn.primary:hover{{background:#6d28d9}}
.connect-btn.secondary{{background:#1a1a1a;border:1px solid #333;color:#ccc;margin-top:.5rem}}
.connect-btn.secondary:hover{{background:#222}}
.back-link{{text-align:center;margin-top:1rem}}
.back-link a{{color:#888;font-size:.85rem;text-decoration:none}}
.back-link a:hover{{color:#a78bfa}}
.step-indicator{{text-align:center;color:#555;font-size:.75rem;margin-bottom:.8rem}}
.note{{color:#888;font-size:.8rem;text-align:center;margin-top:.8rem;line-height:1.4}}

.no-sub{{text-align:center;padding:2rem 0;color:#888}}
.no-sub .emoji{{font-size:2.5rem;margin-bottom:.5rem}}

/* Support form */
.support-form textarea{{width:100%;background:#1a1a1a;border:1px solid #333;border-radius:8px;
color:#e5e5e5;padding:.8rem;font-size:.9rem;font-family:inherit;resize:vertical;min-height:80px;
box-sizing:border-box}}
.support-form textarea:focus{{border-color:#7c3aed;outline:none}}
.support-form input[type=email]{{width:100%;background:#1a1a1a;border:1px solid #333;border-radius:8px;
color:#e5e5e5;padding:.7rem .8rem;font-size:.9rem;font-family:inherit;box-sizing:border-box;margin-bottom:.6rem}}
.support-form input[type=email]:focus{{border-color:#7c3aed;outline:none}}
.support-btn{{display:block;width:100%;padding:.8rem;border:none;border-radius:10px;
background:#7c3aed;color:#fff;font-size:.95rem;font-weight:600;cursor:pointer;margin-top:.8rem}}
.support-btn:hover{{background:#6d28d9}}
.support-btn:disabled{{opacity:.5;cursor:not-allowed}}
.support-ok{{color:#22c55e;text-align:center;font-size:.9rem;margin-top:.8rem;display:none}}
.support-err{{color:#ef4444;text-align:center;font-size:.85rem;margin-top:.5rem;display:none}}
</style>
</head>
<body>
<div class="container">

<div class="header">
    <h1>TIIN</h1>
    <div class="sub">Личный кабинет</div>
</div>

<div class="card">
    <h2>Подписка</h2>
    <div class="status">
        <span class="dot">{status_dot}</span>
        <span class="text">{status_text}</span>
    </div>
    <div class="expiry">до {sub_until}</div>
    <a href="https://t.me/tiin_service_bot?start=renew" class="connect-btn primary"
       style="margin-top:1rem;text-align:center;display:block;text-decoration:none">
        🔄 Продлить подписку
    </a>
</div>

{_render_wizard(sub_url, qr_b64, happ_routing_link) if sub_url else _render_no_sub(web_token, test_used)}

<!-- Support form -->
<div class="card">
    <h2>&#9993;&#65039; Поддержка</h2>
    <p style="color:#888;font-size:.85rem;line-height:1.4;margin-bottom:.8rem">
        Напишите нам, и мы ответим на вашу почту. Для отправки сообщения
        нужно подтвердить email — мы отправим код подтверждения.
    </p>
    <div class="support-form">
        <!-- Step 1: email -->
        <div id="supStep1">
            <input type="email" id="supportEmail" placeholder="Ваш email" value="{email}">
            <button class="support-btn" onclick="supSendCode()">Получить код</button>
        </div>
        <!-- Step 2: code + message -->
        <div id="supStep2" style="display:none">
            <p style="color:#888;font-size:.85rem;margin-bottom:.6rem">
                Код отправлен на <b id="supEmailShow" style="color:#a78bfa"></b>
            </p>
            <input type="text" id="supportCode" placeholder="Код из письма" maxlength="6"
                   style="width:100%;background:#1a1a1a;border:1px solid #333;border-radius:8px;
                   color:#e5e5e5;padding:.7rem .8rem;font-size:1.1rem;font-family:inherit;
                   box-sizing:border-box;margin-bottom:.6rem;text-align:center;letter-spacing:4px">
            <textarea id="supportMsg" placeholder="Опишите вопрос или проблему..." rows="3"></textarea>
            <button class="support-btn" onclick="supSubmit()">Отправить</button>
            <p style="margin-top:.5rem;text-align:center">
                <a href="javascript:supBack()" style="color:#888;font-size:.8rem;text-decoration:none">Изменить email</a>
            </p>
        </div>
        <!-- Done -->
        <div class="support-ok" id="supportOk">&#10004; Сообщение отправлено! Мы ответим на вашу почту.</div>
        <div class="support-err" id="supportErr"></div>
    </div>
</div>

<div class="back-link">
    <a href="https://t.me/tiin_service_bot">Telegram-бот</a>
</div>

</div>

<div class="copied" id="copiedToast">Скопировано!</div>

<script>
const SUB_URL = {json_mod.dumps(sub_url)};

function copyLink() {{
    navigator.clipboard.writeText(SUB_URL).then(function(){{
        var t = document.getElementById('copiedToast');
        t.classList.add('show');
        setTimeout(function(){{ t.classList.remove('show') }}, 1500);
    }});
}}

// Wizard
var selectedDevice = '';
var selectedApp = null;

const APPS = {{
    android: [
        {{ name: 'Happ', desc: 'Простой и быстрый', icon: '⚡', store: 'https://play.google.com/store/apps/details?id=com.happproxy&hl=ru', scheme: 'happ://add/' }},
        {{ name: 'Hiddify', desc: 'Популярный, много функций', icon: '🔷', store: 'https://play.google.com/store/apps/details?id=app.hiddify.com', scheme: 'hiddify://import/' }},
    ],
    ios: [
        {{ name: 'Happ', desc: 'Простой и быстрый', icon: '⚡', store: 'https://apps.apple.com/app/happ-proxy-utility/id6504287215', scheme: 'happ://add/' }},
        {{ name: 'Streisand', desc: 'Надёжный для iOS', icon: '🟣', store: 'https://apps.apple.com/app/streisand/id6450534064', scheme: 'streisand://import/' }},
    ],
    windows: [
        {{ name: 'Hiddify', desc: 'Для Windows и macOS', icon: '🔷', store: 'https://github.com/hiddify/hiddify-app/releases', scheme: 'hiddify://import/' }},
    ],
    macos: [
        {{ name: 'Hiddify', desc: 'Для macOS и Windows', icon: '🔷', store: 'https://github.com/hiddify/hiddify-app/releases', scheme: 'hiddify://import/' }},
    ],
    tv: [
        {{ name: 'VPN4TV', desc: 'Для Android TV', icon: '📺', store: 'https://play.google.com/store/apps/details?id=com.vpn4tv.hiddify', scheme: 'hiddify://import/' }},
    ]
}};

function selectDevice(device) {{
    selectedDevice = device;
    var list = document.getElementById('appList');
    list.innerHTML = '';
    var apps = APPS[device] || [];
    apps.forEach(function(app, i) {{
        list.innerHTML += '<div class="app-card" onclick="selectApp(' + i + ')">' +
            '<div class="app-icon">' + app.icon + '</div>' +
            '<div class="app-info"><div class="app-name">' + app.name + '</div>' +
            '<div class="app-desc">' + app.desc + '</div></div></div>';
    }});
    showStep(2);
}}

function selectApp(index) {{
    selectedApp = APPS[selectedDevice][index];
    document.getElementById('chosenAppName').textContent = selectedApp.name;
    document.getElementById('downloadLink').href = selectedApp.store;
    document.getElementById('autoConnectBtn').href = selectedApp.scheme + SUB_URL;
    showStep(3);
}}

function goBack(step) {{
    showStep(step);
}}

// Routing
const HAPP_ROUTING_LINK = {json_mod.dumps(happ_routing_link)};

const ROUTING_GUIDES = {{
    'Happ': `
        <p class="note" style="margin-top:.8rem">Нажмите для автоматической настройки:</p>
        <a href="${{HAPP_ROUTING_LINK}}" class="connect-btn primary">Настроить маршруты в Happ</a>
        <p class="note" style="margin-top:.8rem">Правила применятся автоматически.</p>`,
    'Hiddify': `
        <p class="note" style="margin-top:.8rem"><b>Настройка в Hiddify:</b></p>
        <p class="note">1. Откройте приложение &rarr; Настройки &rarr; Конфигурация</p>
        <p class="note">2. Найдите раздел <b>Правила маршрутизации</b></p>
        <p class="note">3. Включите <b>Обход локальных адресов</b></p>
        <p class="note">4. В разделе «Прямое подключение» добавьте: <b>geosite:category-ru</b> и <b>geoip:ru</b></p>
        <p class="note" style="margin-top:.5rem;color:#a78bfa">Российские сайты будут открываться напрямую.</p>`,
    'Streisand': `
        <p class="note" style="margin-top:.8rem"><b>Настройка в Streisand:</b></p>
        <p class="note">1. Откройте приложение &rarr; Настройки &rarr; Маршрутизация</p>
        <p class="note">2. Выберите режим <b>Обход локальных и выбранных адресов</b></p>
        <p class="note">3. Добавьте в список обхода: <b>.ru, .su, .рф</b></p>
        <p class="note">4. Включите <b>GeoIP: RU &rarr; Direct</b></p>
        <p class="note" style="margin-top:.5rem;color:#a78bfa">Российские сайты будут открываться напрямую.</p>`,
    'VPN4TV': `
        <p class="note" style="margin-top:.8rem"><b>Настройка на Android TV:</b></p>
        <p class="note">Маршрутизация настраивается так же, как в Hiddify.</p>
        <p class="note">Настройки &rarr; Конфигурация &rarr; Правила маршрутизации &rarr; добавьте <b>geosite:category-ru</b> и <b>geoip:ru</b> в прямое подключение.</p>`,
}};

function showStep(n) {{
    document.querySelectorAll('.step').forEach(function(s) {{ s.classList.remove('active') }});
    var el = document.getElementById('step' + n);
    if (el) el.classList.add('active');
    if (n === 4 && selectedApp) {{
        var guide = ROUTING_GUIDES[selectedApp.name] || ROUTING_GUIDES['Hiddify'];
        document.getElementById('routingContent').innerHTML = guide;
    }}
}}

// Support form
function supErr(msg) {{
    var el = document.getElementById('supportErr');
    el.textContent = msg; el.style.display = msg ? 'block' : 'none';
}}

function supSendCode() {{
    var email = document.getElementById('supportEmail').value.trim();
    supErr('');
    if (!email) {{ supErr('Введите email'); return; }}
    var btn = event.target; btn.disabled = true; btn.textContent = 'Отправка...';

    fetch('/api/web/support/send-code', {{
        method: 'POST',
        headers: {{'Content-Type': 'application/json'}},
        body: JSON.stringify({{email: email}})
    }})
    .then(function(r) {{ return r.json().then(function(d) {{ return {{status: r.status, data: d}}; }}); }})
    .then(function(res) {{
        if (res.data.ok) {{
            document.getElementById('supStep1').style.display = 'none';
            document.getElementById('supStep2').style.display = 'block';
            document.getElementById('supEmailShow').textContent = email;
            document.getElementById('supportCode').focus();
        }} else {{
            supErr(res.data.detail || res.data.message || 'Ошибка отправки кода');
        }}
    }})
    .catch(function() {{ supErr('Ошибка сети'); }})
    .finally(function() {{ btn.disabled = false; btn.textContent = 'Получить код'; }});
}}

function supBack() {{
    document.getElementById('supStep2').style.display = 'none';
    document.getElementById('supStep1').style.display = 'block';
    supErr('');
}}

function supSubmit() {{
    var email = document.getElementById('supportEmail').value.trim();
    var code = document.getElementById('supportCode').value.trim();
    var msg = document.getElementById('supportMsg').value.trim();
    supErr('');
    if (!code) {{ supErr('Введите код из письма'); return; }}
    if (!msg) {{ supErr('Введите сообщение'); return; }}
    var btn = event.target; btn.disabled = true; btn.textContent = 'Отправка...';

    fetch('/api/web/support/contact', {{
        method: 'POST',
        headers: {{'Content-Type': 'application/json'}},
        body: JSON.stringify({{email: email, message: msg, code: code}})
    }})
    .then(function(r) {{ return r.json().then(function(d) {{ return {{status: r.status, data: d}}; }}); }})
    .then(function(res) {{
        if (res.data.ok) {{
            document.getElementById('supStep2').style.display = 'none';
            document.getElementById('supportOk').style.display = 'block';
        }} else {{
            supErr(res.data.detail || res.data.message || 'Ошибка отправки');
        }}
    }})
    .catch(function() {{ supErr('Ошибка сети'); }})
    .finally(function() {{ btn.disabled = false; btn.textContent = 'Отправить'; }});
}}

// Init
showStep(1);
</script>
</body>
</html>"""


def _render_no_sub(web_token="", test_used=False):
    if not test_used and web_token:
        test_btn = f"""
        <button class="connect-btn primary" id="testBtn" onclick="activateTest()" style="margin-top:1rem">
            🎁 Активировать тест — 3 дня бесплатно
        </button>
        <div id="testErr" style="color:#ef4444;text-align:center;font-size:.85rem;margin-top:.5rem;display:none"></div>
        <script>
        function activateTest() {{
            var btn = document.getElementById('testBtn');
            var err = document.getElementById('testErr');
            btn.disabled = true; btn.textContent = 'Активация...'; err.style.display = 'none';
            var ref = localStorage.getItem('tiin_ref') || null;
            if (!ref) {{ var _r = new URLSearchParams(window.location.search).get('ref'); if (_r && _r.length <= 64 && /^[A-Za-z0-9_-]+$/.test(_r)) ref = _r; }}
            fetch('/api/web/activate-test', {{
                method: 'POST',
                headers: {{'Content-Type': 'application/json'}},
                body: JSON.stringify({{web_token: '{web_token}', ref: ref}})
            }})
            .then(function(r) {{ return r.json().then(function(d) {{ return {{status: r.status, data: d}}; }}); }})
            .then(function(res) {{
                if (res.data.ok) {{
                    window.location.reload();
                }} else {{
                    err.textContent = res.data.detail || 'Ошибка активации';
                    err.style.display = 'block';
                    btn.disabled = false; btn.textContent = '🎁 Активировать тест — 3 дня бесплатно';
                }}
            }})
            .catch(function() {{
                err.textContent = 'Ошибка сети';
                err.style.display = 'block';
                btn.disabled = false; btn.textContent = '🎁 Активировать тест — 3 дня бесплатно';
            }});
        }}
        </script>"""
    else:
        test_btn = '<p style="margin-top:.5rem;font-size:.8rem">Оформите подписку на <a href="https://tiinservice.ru" style="color:#a78bfa">tiinservice.ru</a></p>'

    return f"""
<div class="card">
    <div class="no-sub">
        <div class="emoji">❄️</div>
        <p>Нет активной подписки</p>
        {test_btn}
    </div>
</div>"""


def _render_wizard(sub_url, qr_b64, happ_routing_link=""):
    return f"""
<!-- Step 1: Device -->
<div class="step" id="step1">
<div class="card">
    <div class="step-indicator">Шаг 1 из 4</div>
    <h2>Ваше устройство</h2>
    <div class="device-grid">
        <div class="device-btn" onclick="selectDevice('android')">
            <div class="icon">🤖</div><div class="label">Android</div>
        </div>
        <div class="device-btn" onclick="selectDevice('ios')">
            <div class="icon">🍏</div><div class="label">iPhone / iPad</div>
        </div>
        <div class="device-btn" onclick="selectDevice('windows')">
            <div class="icon">💻</div><div class="label">Windows</div>
        </div>
        <div class="device-btn" onclick="selectDevice('macos')">
            <div class="icon">🖥</div><div class="label">macOS</div>
        </div>
        <div class="device-btn" onclick="selectDevice('tv')">
            <div class="icon">📺</div><div class="label">Android TV</div>
        </div>
    </div>
</div>
</div>

<!-- Step 2: App -->
<div class="step" id="step2">
<div class="card">
    <div class="step-indicator">Шаг 2 из 4</div>
    <h2>Выберите приложение</h2>
    <div id="appList"></div>
    <div class="back-link"><a href="javascript:goBack(1)">&larr; Назад</a></div>
</div>
</div>

<!-- Step 3: Connect -->
<div class="step" id="step3">
<div class="card">
    <div class="step-indicator">Шаг 3 из 4</div>
    <h2>Подключение</h2>

    <p class="note">1. Скачайте <b><span id="chosenAppName"></span></b> если ещё не установлено:</p>
    <a id="downloadLink" href="#" target="_blank" class="connect-btn secondary">Скачать приложение</a>

    <p class="note" style="margin-top:1.2rem">2. Нажмите для автоматической настройки:</p>
    <a id="autoConnectBtn" href="#" class="connect-btn primary">Подключить VPN</a>

    <p class="note" style="margin-top:1.2rem">Или добавьте вручную — скопируйте ссылку:</p>
    <div class="sub-link" onclick="copyLink()">{html_mod.escape(sub_url)}</div>

    <div class="qr-wrap">
        <img src="data:image/png;base64,{qr_b64}" alt="QR">
    </div>
    <p class="note">Отсканируйте QR-код камерой или из приложения</p>

    <a href="javascript:showStep(4)" class="connect-btn secondary">Настроить маршрутизацию &rarr;</a>
    <div class="back-link"><a href="javascript:goBack(2)">&larr; Назад</a></div>
</div>
</div>

<!-- Step 4: Routing -->
<div class="step" id="step4">
<div class="card">
    <div class="step-indicator">Шаг 4 из 4</div>
    <h2>Маршрутизация</h2>
    <p class="note">Российские сайты будут работать напрямую, без VPN — быстрее и стабильнее.</p>

    <div id="routingContent"></div>

    <div class="back-link" style="margin-top:1rem"><a href="javascript:goBack(3)">&larr; Назад</a></div>
</div>
</div>
"""
