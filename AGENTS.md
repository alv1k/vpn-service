# vpn-service — ядро VPN (API, бот, админ-панель)

Общие правила инфраструктуры — в корневом `/home/alvik/AGENTS.md` (контроль изменений, бэкапы перед правкой, логирование исходящих сообщений через `log_message_sent()`).

## Multi-Node архитектура VPN (DE ⇄ RU ⇄ NL)

### Smart Multi-Operator Inbounds Roster (DE, 3x-ui)
| Inbound | Протокол / Порт | Фокус | Имя в подписке |
|---|---|---|---|
| `#4` | `Hysteria2` (`4443/udp`) | Универсальный, 52% трафика | `🇩🇪 🚀 Hysteria2 • Turbo (Быстрый)` |
| `#1` | `VLESS-Reality` (`7443/tcp`) | Основной TCP (МТС/РТК), 40% | `🇩🇪 👁️ Reality • Vision (МТС / РТК)` |
| `#15` | `VLESS-xHTTP` (`48745/tcp`) | ⚠️ выключен (xHTTP-линия передана `#21`/`mr-empeche` :443) | — |
| `#2` | `VLESS-xHTTP` (`47447/tcp`) | Стелс-xHTTP (МегаФон), рерайт → :443 mr-empeche | `🇩🇪 🌊 xHTTP • Stream (МегаФон)` |
| `#10` | `VLESS-Reality` (`53151/tcp`) | ⚠️ выключен (резерв, Deutsche Bahn SNI) | — |
| `#14` | `VLESS-xHTTP` (`38745/tcp`) | ⚠️ выключен (резерв) | — |
| `#16` | `Hysteria2` (`35443/udp`) | ⚠️ выключен (резервный UDP) | — |
| `#21` | `VLESS-Reality` (`20443/tcp`) | **Основной Reality**: SNI de-gateway/szm6/test-reality/mr-empeche, за nginx ssl_preread :443, xver/proxy_protocol | `🇩🇪 🚀 Reality • Vision` |
| `RU #1` | `VLESS-WS-TLS` (`8443/tcp`) | RU-шлюз Selectel SPB (резерв — квота) | `🇷🇺 🛡️ XHTTP • Шлюз (РФ ➔ DE)` |
| `RU #2` | `Hysteria2` (`35443/udp`) | RU скоростной шлюз Selectel (резерв — квота) | `🇷🇺 ⚡ Hysteria2 • Шлюз (РФ ➔ DE)` |
| `#17` | AmneziaWG (`443/udp`) | AWG Main, MTU 1280, Split Tunneling 30 CIDR, direct YouTube | — |

### AEZA Node (`193.233.249.164`, основная RU-точка входа, безлимит)
- 3x-ui **3.11.0**, панель `127.0.0.1:6547`, webBasePath `/VeEVCecDUl1ct7JxNj/`. Доступ с DE — только через SSH-туннель **`aeza-xui-tunnel.service`** (`127.0.0.1:16547 → 127.0.0.1:6547`, `Restart=always`). API-учётка `tiin_sync`, пароль в `/etc/aeza-xui.creds` (600, alvik) — `config.py` читает fallback'ом.
- **Инбаунд #2**: `VLESS-xHTTP+Reality :443`, SNI `www.avito.ru` (не «дефолтный» шаблон), path `/sdk/v2/sync`, mode `stream-one`, pbk `25WSn8XyOIuDIFndbHyORH-Ef7Car2I5e1fnVMeIWCQ`, приватный shortId `3f42088b4b3e2a15` (28.11.2026). Имя в подписке: `🇷🇺 🚀 RU Server2 • xHTTP Reality (РФ ➔ DE)` — **первый профиль** в выдаче sub_proxy.
- **Egress**: аутбаунд `to-germany` (VLESS-Reality → `91.132.161.112:443`, SNI `test-reality.tiinservice.online`, добавлен в nginx map DE). Routing: ВСЕ клиенты in-443-tcp → `to-germany` (правило без фильтра user, с 28.11.2026).
- **Синхронизация клиентов**: `scripts/sync_aeza_node.py` (hourly cron, `10 * * * *`, лог `logs/aeza_sync.log`) — сверка id/subId/expiry/enable с DE, привязка к inbound #2. Источник истины о клиентах ноды — `/panel/api/clients/list` (поле `inboundIds`), НЕ JSON `settings` инбаундов (в 3.11 он устаревает). Клиентские add/update/attach применяются ядром hot (без рестарта); `restartXrayService` — только по флагу `--restart-xray` (рвёт ВСЕ соединения, 28.11.2026 именно так был оборван сеанс пользователя). Инкрементная синхронизация — `sync_client_to_aeza_node()` в `bot_xui/multi_node.py`, вызывается обёрткой `sync_client_to_ru_nodes()` (вместе с Selectel) из vpn_factory/payment/db/webhook.
- **Гигиена**: SSH только по ключу (`PasswordAuthentication no`), ufw (22, 443/tcp), fail2ban sshd. IP проверяется cheburcheck_monitor.py (critical target). **Anti-detect (28.11.2026)**: xhttp-path нестандартный (`/sdk/v2/sync`), приватный shortId, SNI avito. В ссылках — голый IP (front-домен не привязан): при блокировке IP — смена IP в панели AEZA → правка `AEZA_HOST` в config.py (или env) → restart api.service; клиенты получают новый адрес автообновлением подписки.
- Клиенты `admin_test1`/`aeza-client1` — тестовые, не удалять (используются для E2E-прогонов).

### RU Relay Node (`ru-server.tiinservice.online`, `139.100.207.18`) — резерв после исчерпания квоты
- Панель 3x-ui RU: `https://139.100.207.18:2053/0ruabxwdsz96R7jSIN` (env `RU_XUI_HOST/USERNAME/PASSWORD`).
- **L3 мост AmneziaWG**: `10.50.0.1` (DE) ⇄ `10.50.0.2` (RU), `51850/udp`, MTU 1420, DPI-обфускация `Jc=4, Jmin=40, Jmax=70, S1=33, S2=47, H1..H4` (конфиги `/etc/amnezia/amneziawg/awg-{ru,de}-bridge.conf`). `10.50.0.2` — только для управления 3x-ui API и внутренних туннелей.
- **Split-Tunneling на RU**: `.ru/.su/.рф`, Яндекс, Госуслуги, банки, маркетплейсы, `geosite:category-ru`, `geoip:ru` → `direct`; зарубежный трафик → `de-bridge` (VLESS Vision на `10.50.0.1:7443`); аварийный failover `olcrtc-germany` (`127.0.0.1:10808`).
- **RU egress proxy на DE** (`ru-proxy.service`): Xray client `127.0.0.1:10809` (HTTP) / `10808` (SOCKS) через `10.50.0.2:8443` — для ЮKassa (`api.yookassa.ru`), которая режет TLS с Hetzner. Вызовы через `bot_xui/yookassa_client.py` (socket timeout 10с, `asyncio.wait_for` 15с).
- **Anti-Loop**: RU-шаблон фиксирует `{"user": ["test_awg_user_01"], "outboundTag": "direct"}` (трафик из DE через `8443` выходит в РФ и не возвращается в `de-bridge`); DE-шаблон — `{"user": ["bwd2scof"], "outboundTag": "direct"}` перед правилами пересылки в РФ. На обоих узлах блокируется `udp:443` (фоллбэк браузеров с QUIC на TCP, устранение таймаутов XTLS Vision).
- **Межузловой мост RU→DE (primary)**: Hysteria v2 `hy2-bridge.service` → `344988.snk.wtf:35443`, SOCKS5 `127.0.0.1:10888` (на RU). Backup: olcRTC WebRTC, холодный резерв `olcrtc-client.service` (:10808).
- **Failover-сторож RU**: `/root/scripts/ru_bridge_failover.py` (cron каждую минуту): ping L3-моста → при 2 ошибках поднимает olcRTC и переключает маршрут, при восстановлении возвращает `de-bridge` и гасит olcRTC; уведомления в Telegram; контроль квоты Selectel (тревога при ≥4 ТБ из 5 ТБ, период с 21 числа).
- **Синхронизация клиентов**: `vpn-service/scripts/sync_ru_node.py` + `bot_xui/multi_node.py` (hourly cron): `id (UUID)`, `auth/password`, `subId`, `expiryTime`, `enable`, `limitIp` — 100% сверка DE ⇄ RU. `XUIClient` таймаут 15с. Активный инбаунд — **#4 RU-xHTTP** (`RU_ACTIVE_INBOUND_IDS=4`; #1/#2 выключены). Состояние — через `/panel/api/clients/list` (`inboundIds`); рестарта Xray нет (hot-apply), флаг `--restart-xray` для форсирования.

### Сторож на DE: `scripts/de_bridge_failover.py` (каждую минуту)
Проверяет в порядке: L3-мост `10.50.0.2` (ping) → `yaenergo-proxy` (`127.0.0.1:10808`) → `ru-hy2-check` (`127.0.0.1:10887`). Порог `FAIL_THRESHOLD = 3`, при обрыве поднимает `olcrtc.service` как аварийный канал.

### Dual DNS (AdGuard Home кластер)
- **Domestic (RU, `10.50.0.2:53`)**: upstream Яндекс DoH `77.88.8.8`; мгновенное разрешение RU-доменов (~14 мс).
- **Remote (NL, `2.26.76.210:53`)**: Cloudflare/Google/Quad9 DoH + AdBlock (430k+ правил).
- Профиль подписок 3x-ui `RU-Direct_DualDNS`: Domestic DNS `139.100.207.18` (резерв `77.88.8.8`), Remote DNS `2.26.76.210` (резерв `1.1.1.1`), базы `runetfreedom` (geoip/geosite.dat).
- **DE-fallback**: outbound `de-to-ru-node` → `10.50.0.2:8443` (VLESS-WS-TLS, mux concurrency 8); правило routing заворачивает `geosite:category-ru`, `domain:ru`, `geoip:ru` через RU-ноду при глобальном VPN-режиме клиента.

### Матрица форматов подписок (`api/sub_proxy.py`)
| Клиент | Формат | Доставка роутинга | Критично |
|---|---|---|---|
| **Happ** | Base64 + HTTP-заголовки | Заголовки `routing: happ://routing/add/...`, `routing-enable: true`; deep link в тело только если `"happ" in ua.lower()` | Профиль `TIIN • Smart RU Bypass` |
| **Sing-box / Hiddify / Karing** | Sing-box JSON | DNS и правила `direct`/`proxy` внутри JSON | Определяется по UA |
| **Clash Meta / Mihomo / FlClash / Stash** | Clash YAML | Блоки `rules` (`DOMAIN-SUFFIX,DIRECT`, `GEOIP,RU,DIRECT`) | `?format=clash` тоже |
| **INCY** | Base64 plain | Серверный split-tunneling на RU-шлюзе | **НЕ поддерживает Sing-box JSON** — отдавать чистый base64 БЕЗ `happ://` |
| **v2rayNG / Streisand** | Base64 plain | Серверный split-tunneling / тумблер «Обход РФ» | Посторонние схемы ломают парсинг всей подписки |
| **Санитаризация токенов** | URL-параметры | `token.strip()` в `/sub/{token}`, `/go-connect/`, `db.get_user_by_web_token` | Защита от `%20`/пробелов при копировании |

### Монитор переходов между инбаундами
- `inbound-switch-monitor.service` (root, 24/7) читает `/var/log/x-ui/access.log`; лог переходов `vpn-service/logs/inbound_switches.log`; состояние/матрица `vpn-service/data/inbound_switches.json`; отчёт: `python3 vpn-service/scripts/inbound_switch_monitor.py --report`. Операторы: МТС, МегаФон, Билайн, Т2, Ростелеком, Сахателеком, Уфанет.

### 3x-ui панель (DE)
- Путь `/usr/local/x-ui/`, БД `/etc/x-ui/x-ui.db`, сервис `x-ui.service`, версия **3.2.7** (на 09.07.2026; тест миграции на 3.4.2 на копии БД пройден: `x-ui migrate` exit 0, integrity ok; бэкапы `/root/xui.db.backup.3.2.7`, `/root/config.json.backup.3.2.7`). После обновления добавить `XUI_MEMORY_LIMIT=150` в `/etc/default/x-ui`.
- Порт панели 5096, webBasePath `/XhU5cXVfMCyHzAXlrT/`, SSL включён. Inbounds DE: #1 VLESS Reality :7443 (SNI `tile.openstreetmap.org`, `ftp.fau.de`, `www.uni-frankfurt.de`), #2 VLESS xHTTP :47447, #4 Hysteria :4443, #9 VLESS WS-TLS :14715, #10 Reality :53151 (`www.amazon.com`), #14 xHTTP :38745, #15 xHTTP :48745 (`update.microsoft.com`), #16 Hysteria2 :35443, #17 AWG :443, #18 HTTP :7445 (disabled), #5 VLESS-testing :8081.
- **Архитектура привязки клиентов (3x-ui 3.4+)**: ключи/трафик в JSON `settings.clients` инбаунда; привязка — через таблицу `client_inbounds` (`client_id`, `inbound_id`, `flow_override`). Без записи в `client_inbounds` соединение считается неавторизованным и сбрасывается (Hysteria2 в ряде случаев пускала по userpass). Для инбаундов `#1`/`#10` обязателен `flow_override = "xtls-rprx-vision"` (иначе «account is not able to use the flow xtls-rprx-vision»).
- **Кэш подписок**: `api/sub_proxy.py` держит in-memory кэш (`SUB_CACHE_TTL`). После изменения состава инбаундов/привязок — `sudo systemctl restart api.service`.
- **Синхронизация сроков (критично)**: фоновый демон 3x-ui отключает клиентов строго по `client_traffics.expiry_time`. Скрипты продления обязаны обновлять **обе** таблицы (`clients` и `client_traffics`) + JSON инбаунда, иначе демон вызовет `Remove Inbound User ... due to expiration` и отключит клиента. Диагностика: `journalctl -u x-ui -g "due to expiration or traffic limit"`; исправление: `python3 vpn-service/scripts/fix_client_traffics_expiry.py` (бэкап БД + синхронизация + рестарт x-ui).
- 64 клиента на inbound #1, 50 с limitIp (14×1, 36×10).

### olcRTC — WebRTC-туннель (обход white-listing)
- Бинарник `/home/alvik/olcrtc/olcrtc`, исходники `/home/alvik/olcrtc-src/` (Go 1.26+, сборка `mage`).
- Сервер: `olcrtc.service`, конфиг `/home/alvik/olcrtc/server.yaml`; клиент `client.yaml`, локальный SOCKS5 :10808.
- Провайдер `jitsi` (failover-пул): `https://jitsi.member.fsf.org/tiinvpn_olcrtc_ru_8725`, `https://jitsi.riot.im/tiinvpn_olcrtc_ru_8725`; транспорт `datachannel`; XChaCha20-Poly1305 (`key: 87252e12af0be5447cadb55a7824438b7c2682b92f98f06ad82cd35d06268c54`).
- Режим: on-demand (не держать 24/7 сессию в одной Jitsi-комнате), ротация профилей через failover-конфигурацию.

### Анализ блокировок ТСПУ (осень 2026)
- Тренды: «белые списки» у мобильных операторов, изоляция «белых» IP-подсетей провайдерами (нельзя размещать VPN-узлы на соседних IP), Strict SNI-to-IP matching (SNI Apple/Microsoft на IP европейского VPS → сброс).
- Стратегия: двухуровневая топология RU Relay ➔ Exit Node; RU-точка входа проходит как domestic; транзит RU↔DE защищён VLESS-XHTTP / Shadowsocks-2022 / olcRTC; ротация Reality SNI (`data/active_sni.json`, валидация `check_reality_sni.py` каждые 30 мин); серверный split-tunneling.
