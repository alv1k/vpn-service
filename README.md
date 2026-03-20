# TIIN VPN Service

Subscription-based VPN service with a Telegram bot frontend, multi-protocol support, and automated payment processing via YooKassa.

## Supported VPN Protocols

| Protocol | Transport | Obfuscation | Use Case |
|----------|-----------|-------------|----------|
| **VLESS Reality** | TCP/443 | Reality (XTLS) | Primary, best for bypassing DPI |
| **AmneziaWG 2.0** | UDP/51888 | Junk packets + header masking | Fast, native WireGuard-based |
| **SoftEther** | TCP/443 | VPN Azure relay | Legacy clients (Windows XP), L2TP/IPsec |

## Architecture

```
┌───────────────────────────────────────────────────────────────┐
│  VPS (Ubuntu 22.04)                                           │
│                                                               │
│  Systemd services:                                            │
│  ┌──────────────┐  ┌──────────────────┐  ┌──────────────────┐ │
│  │ Telegram Bot  │  │ Webhook API      │  │ AWG 2.0 API      │ │
│  │ (bot_xui/)    │  │ (api/webhook.py) │  │ (awg_api/main.py)│ │
│  │ python-tg-bot │  │ FastAPI+Uvicorn  │  │ FastAPI+Uvicorn  │ │
│  └──────┬───────┘  └────────┬─────────┘  └────────┬─────────┘ │
│         │                   │                      │           │
│  Docker containers:         │                      │           │
│  ┌──────────────┐  ┌───────┴────────┐  ┌─────────┴────────┐  │
│  │ 3x-UI (VLESS)│  │ MySQL 8.0      │  │ AmneziaWG        │  │
│  │ :51999       │  │ :3306          │  │ :51888 (VPN)     │  │
│  └──────────────┘  │ + phpMyAdmin   │  │ :51821 (legacy)  │  │
│                    │   :8080 (local)│  └──────────────────┘  │
│  Native:           └───────────────┘                         │
│  ┌──────────────┐                                            │
│  │ SoftEther    │                                            │
│  │ vpnserver    │                                            │
│  │ :5555        │                                            │
│  └──────────────┘                                            │
└───────────────────────────────────────────────────────────────┘
```

## Project Structure

```
vpn-service/
├── bot_xui/               # Telegram bot (python-telegram-bot 20.7)
│   ├── bot.py             #   Entry point, handlers, scheduler
│   ├── payment.py         #   YooKassa payment creation
│   ├── vpn_factory.py     #   VPN config creation (AWG, VLESS)
│   ├── softether.py       #   SoftEther vpncmd wrapper
│   ├── tariffs.py         #   Tariff definitions
│   └── views.py           #   Telegram message templates
├── api/                   # FastAPI webhook & web portal
│   ├── webhook.py         #   YooKassa webhook + payment processing
│   ├── web_portal.py      #   Personal page /my/{token}
│   ├── db.py              #   MySQL queries (users, payments, keys)
│   └── subscriptions.py   #   Subscription management
├── awg_api/               # AmneziaWG 2.0 REST API
│   ├── main.py            #   FastAPI app, session auth, client CRUD
│   ├── awg_manager.py     #   awg/awg-quick CLI wrapper
│   ├── admin_api.py       #   Admin panel API (dashboard, online, finance)
│   ├── admin_db.py        #   Admin-specific DB queries
│   ├── db.py              #   AWG client/server DB schema
│   └── static/admin.html  #   Admin panel SPA
├── scripts/               # Deployment, migration, maintenance
├── tests/                 # pytest test suites
├── docker-compose.yml     # MySQL, 3x-UI, AmneziaWG, phpMyAdmin
├── config.py              # Central config (loads .env)
└── restart.sh             # Service restart helper
```

## Requirements

- Ubuntu 22.04+ (VPS, min 1 GB RAM / 20 GB SSD)
- Domain with DNS configured
- Telegram Bot Token
- YooKassa merchant account
- AmneziaWG kernel module installed

## Installation

```bash
# 1. Clone and install dependencies
git clone <repo> ~/vpn-service
cd ~/vpn-service
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# 2. Configure environment
cp .env.example .env
nano .env

# 3. Start infrastructure
docker compose up -d

# 4. Set up SSL
sudo bash scripts/setup-ssl.sh yourdomain.com

# 5. Start services
sudo systemctl start bot api
sudo systemctl enable bot api
```

See [docs/INSTALLATION.md](docs/INSTALLATION.md) for detailed instructions.

## Configuration

All settings are loaded from `.env` via `config.py`:

| Variable | Description |
|----------|-------------|
| `TELEGRAM_BOT_TOKEN` | Telegram bot token |
| `YOO_KASSA_SHOP_ID` / `YOO_KASSA_SECRET_KEY` | YooKassa production credentials |
| `YOO_KASSA_TEST_SHOP_ID` / `YOO_KASSA_TEST_SECRET_KEY` | YooKassa test mode (admin only) |
| `MYSQL_HOST` / `MYSQL_USER` / `MYSQL_PASSWORD` / `MYSQL_DATABASE` | MySQL connection |
| `XUI_HOST` / `XUI_USERNAME` / `XUI_PASSWORD` | 3x-UI panel access |
| `VLESS_DOMAIN` / `VLESS_PBK` / `VLESS_SID` / `VLESS_SNI` | VLESS Reality params |
| `AMNEZIA_WG_API_URL` / `AMNEZIA_WG_API_PASSWORD` | AWG API access |
| `SOFTETHER_SERVER_PASSWORD` / `SOFTETHER_HUB` | SoftEther VPN server |
| `REFERRAL_REWARD_DAYS` / `REFERRAL_NEWCOMER_DAYS` | Referral bonus (default: 3 days each) |
| `ADMIN_TG_ID` | Admin Telegram user ID |

## Tariffs

| Key | Name | Price | Duration | Devices |
|-----|------|-------|----------|---------|
| `test_24h` | Test | Free | 24 hours | 1 |
| `trial_1d` | Trial | 10 RUB | 1 day | 10 |
| `weekly_7d` | Weekly | 50 RUB | 7 days | 10 |
| `monthly_30d` | Monthly | 199 RUB | 30 days | 10 |
| `standard_3m` | Standard | 499 RUB | 90 days | 10 |

Defined in `bot_xui/tariffs.py`. Discounts apply via permanent user discounts or promocodes.

## Payment Flow

```
User selects tariff in Telegram
        │
        ▼
bot creates YooKassa payment (payment.py)
        │
        ▼
User pays via YooKassa checkout
        │
        ▼
YooKassa POSTs webhook → /webhook/yookassa (webhook.py)
        │
        ▼
VPN config created (AWG / VLESS / SoftEther)
        │
        ▼
activate_subscription() — only after VPN creation succeeds
        │
        ▼
Config file + credentials sent to user in Telegram
```

## Admin Panel

Accessible at `/tiin_admin_panel/` (password-protected).

**Dashboard** — client counts, online users (SSE live updates), financials, new users today, promocodes, winback log, monthly revenue breakdown.

**Tabs** — AWG clients, VLESS clients (with global traffic stats), SoftEther users, user search.

Fully mobile-responsive.

## Key Features

- **Multi-protocol VPN** — VLESS Reality, AmneziaWG 2.0, SoftEther
- **Telegram bot** — tariff selection, payment, config delivery, subscription reminders
- **Web portal** — `/my/{token}` personal page for users in regions where Telegram is blocked
- **YooKassa payments** — production + test mode, webhook-driven activation
- **Referral system** — deep links, automatic bonus days for both parties
- **Promocodes** — days-based and percentage discounts, per-user limits, expiry
- **Win-back** — automated re-engagement of churned users with traffic-based scoring
- **Admin panel** — real-time monitoring, client management, financials
- **Health checks** — `scripts/vpn-health-check.sh`
- **Automated tests** — `scripts/run_tests.sh` with Telegram notifications

## Operations

```bash
# Service management
./restart.sh              # restart both services
./restart.sh bot          # restart bot only
./restart.sh api          # restart API only
./restart.sh status       # show service status
./restart.sh logs bot     # tail bot logs

# Docker
docker compose ps
docker compose logs -f x-ui

# Deployment
bash scripts/deploy.sh

# Backup
bash scripts/backup.sh

# Tests
pytest tests/
bash scripts/run_tests.sh   # runs tests + sends report to admin via Telegram
```

## Database

MySQL 8.0 with the following tables:

- **users** — Telegram users, subscription dates, referral counts, discounts, web tokens
- **payments** — YooKassa payment records (pending/paid/failed)
- **vpn_keys** — issued VPN configs (client IDs, keys, links, expiry, vpn_type)
- **promocodes** — discount/bonus codes with usage tracking
- **promocode_usages** — per-user promocode redemption log
- **awg_server** — AmneziaWG server config (keys, obfuscation params)
- **awg_clients** — AmneziaWG client records (keys, addresses)

## Documentation

- [Installation](docs/INSTALLATION.md)
- [System Overview](docs/SYSTEM_OVERVIEW.md)
- [API Testing](docs/API_TESTING.md)
- [User Access & Connection](docs/USER_ACCESS_CONNECTION.md)

## Tech Stack

- **Python 3.11+**, FastAPI, Uvicorn, python-telegram-bot 20.7
- **MySQL 8.0**, Docker Compose
- **3x-UI** (VLESS/xray), **AmneziaWG** (WireGuard fork), **SoftEther VPN**
- **YooKassa** payment gateway
- **APScheduler** for recurring tasks
- **pytest** for testing
