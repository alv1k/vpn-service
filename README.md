# TIIN VPN Service

Subscription-based VPN service with Telegram bot, web portal, admin panel, multi-protocol support, and automated payments via YooKassa.

## Supported VPN Protocols

| Protocol | Transport | Obfuscation | Use Case |
|----------|-----------|-------------|----------|
| **VLESS Reality** | TCP/443 | Reality (XTLS) | Primary, best for bypassing DPI |
| **AmneziaWG 2.0** | UDP/51888 | Junk packets + header masking | Fast, native WireGuard-based |
| **SoftEther** | TCP/443 | VPN Azure relay | Legacy clients (Windows XP/7), L2TP/IPsec |
| **MTProto Proxy** | TCP | Telegram protocol | Telegram access without VPN |

## Features

### Telegram Bot

- **Registration** via `/start` with optional referral deep link
- **Free trial** — 3-day test period per protocol (VLESS, AWG, SoftEther)
- **Tariff selection** with per-diem cost display and popular/best-value badges
- **Config delivery** with QR codes, copy-to-clipboard, and app deep links
- **Subscription reminders** at 3 days, 1 day, same day, and 1 day after expiry (Telegram + email)
- **Autopay** — automatic renewal via saved payment method, toggleable per user
- **Referral system** — personal deep link + QR code, bonus days for both parties
- **Promo codes** — days, one-time discount, or permanent discount; usage/expiry limits
- **MTProto Proxy** — one-click Telegram proxy setup without VPN
- **Happ integration** — split tunneling rules auto-import via deep link
- **Support** — in-bot feedback with admin reply forwarding
- **Admin commands** — `/broadcast`, `/send`, `/testmode`, `/promo`, `/addpromo`, `/delpromo`

### Web Portal (`/my/{token}`)

- Personal VPN dashboard accessible without Telegram
- Active configs with expiry dates, QR codes, deep links (`vpn://`, `happ://`)
- Protocol-specific setup instructions
- Email-based registration with 6-digit code verification
- Rate-limited API endpoints

### Website (Landing Page)

- Hero section with feature highlights
- Tariff display and order flow: email verification -> promo code -> payment
- FAQ section, mobile-responsive design

### Payment System (YooKassa)

- Production + test mode (admin-switchable via `/testmode`)
- Idempotent payment creation with webhook-driven activation
- Payment flow: payment -> VPN config creation -> subscription start
- Payment method saving for autopay
- Concurrent request deduplication
- Telegram + email receipts

### Autopay

- Charges saved payment method 1 day before expiry
- Per-user enable/disable via `/autopay`
- Automatic tariff renewal with same VPN type
- Failure logging, user notification, auto-disable on repeated failures

### Referral System

- Deep link referral (`/start {tg_id}`)
- Configurable reward days for referrer and newcomer (default: 3 days each)
- Auto-extends existing config or creates new VPN
- Referral count tracking and display with QR code

### Promo Codes

| Type | Effect |
|------|--------|
| `days` | Free VPN days added |
| `discount` | One-time percentage off |
| `permanent_discount` | Permanent percentage reduction on all purchases |

Per-code and per-user usage limits, expiry dates, admin CRUD via bot commands.

### Winback System

- Automated re-engagement for churned/inactive users
- 9 scenarios based on user behavior (expired, never activated, trial-only, etc.)
- Traffic-based scoring for targeting
- Conversion tracking and effectiveness analytics
- Admin log in admin panel

### Admin Panel (`/tiin_admin_panel/`)

- **Dashboard** — total users, active subscribers, revenue, new users today
- **Live monitoring** — real-time online users via SSE, per-client traffic/speed (Mbit/s)
- **Client management** — AWG, VLESS (with global traffic stats), SoftEther tabs
- **User search** — by name, tg_id, referral; per-user keys, payments, referral network
- **Finance** — monthly revenue breakdown, test vs. production, failed/pending payments
- **Promo codes** — active codes list, usage stats
- **Winback log** — campaign results, conversion by scenario
- **Autopay** — failure analysis, discount distribution
- **Test payments** — admin test payment tab
- Password-protected, 7-day session expiry, mobile-responsive

### Notifications

| Event | Channel |
|-------|---------|
| Subscription expiry (3d, 1d, today, +1d after) | Telegram + Email |
| Payment success | Telegram + Email |
| Autopay charge / failure | Telegram |
| Referral reward | Telegram |
| Admin broadcast | Telegram |
| Config update | Telegram |

### Security

- Session management with 7-day expiry and sliding window refresh
- Rate limiting per endpoint (auth, payments, test activation)
- Webhook signature verification (YooKassa)
- Web token generation (16 bytes, URL-safe)
- Payment method tokenization
- IP-based sharing detection with user notification

### Monitoring & Scripts

| Script | Purpose |
|--------|---------|
| `vpn-health-check.sh` | Xray, Nginx, DNS, HTTP checks every 3h; auto-restart + Telegram alert |
| `awg_expiry_check.py` | AWG client expiry monitoring and cleanup |
| `awg_handshake_monitor.py` | AWG handshake tracking and traffic accounting |
| `update_ru_routes.py` | Update Russian IP/domain geosite data for split tunneling |
| `channel_post.py` | Telegram channel posting |

### Email System

- Branded HTML email templates
- Subscription reminders, payment confirmations, portal links
- Support form with email verification
- Open tracking
- SMTP via Brevo

## Architecture

```
┌───────────────────────────────────────────────────────────────┐
│  VPS (Ubuntu 22.04)                                           │
│                                                               │
│  Systemd services:                                            │
│  ┌──────────────┐  ┌──────────────────┐  ┌──────────────────┐ │
│  │ Telegram Bot  │  │ Web API          │  │ AWG 2.0 API      │ │
│  │ (bot_xui/)    │  │ (api/)           │  │ (awg_api/)       │ │
│  │ python-tg-bot │  │ FastAPI+Uvicorn  │  │ FastAPI+Uvicorn  │ │
│  └──────┬───────┘  └────────┬─────────┘  └────────┬─────────┘ │
│         │                   │                      │           │
│  Docker containers:         │                      │           │
│  ┌──────────────┐  ┌───────┴────────┐  ┌─────────┴────────┐  │
│  │ 3x-UI (VLESS)│  │ MySQL 8.0      │  │ AmneziaWG        │  │
│  │ :51999       │  │ :3306          │  │ :51888 (VPN)     │  │
│  └──────────────┘  │ + phpMyAdmin   │  └──────────────────┘  │
│                    │   :8080 (local)│                         │
│  Native:           └───────────────┘                         │
│  ┌──────────────┐                                            │
│  │ SoftEther    │                                            │
│  │ vpnserver    │                                            │
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
│   ├── helpers.py         #   Shared utilities
│   ├── test_mode.py       #   Test payment mode logic
│   ├── utils.py           #   Rate limiting, formatting
│   └── views.py           #   Telegram message templates
├── api/                   # FastAPI web services
│   ├── webhook.py         #   YooKassa webhook + payment processing
│   ├── web_portal.py      #   Personal page /my/{token}
│   ├── web_api.py         #   Website order/promo/test API
│   ├── security.py        #   Rate limiting, session management
│   └── db.py              #   MySQL queries (users, payments, keys)
├── admin/                 # Admin panel
│   ├── routes.py          #   Admin API endpoints
│   ├── db.py              #   Admin-specific DB queries
│   └── static/admin.html  #   Admin panel SPA
├── awg_api/               # AmneziaWG 2.0 REST API
│   ├── main.py            #   FastAPI app, client CRUD
│   ├── awg_manager.py     #   awg/awg-quick CLI wrapper
│   └── db.py              #   AWG client/server DB schema
├── scripts/               # Maintenance & deployment
├── tests/                 # pytest test suites (30+ files)
├── website/               # Landing page
│   └── index.html
├── data/                  # GeoIP data for split tunneling
├── config.py              # Central config (loads .env)
├── docker-compose.yml     # MySQL, 3x-UI, AmneziaWG, phpMyAdmin
└── restart.sh             # Service restart helper
```

## Tariffs

| Name | Price | Duration | Devices |
|------|-------|----------|---------|
| Test | Free | 3 days | 1 |
| Weekly | 50 RUB | 7 days | 10 |
| Monthly | 199 RUB | 30 days | 10 |
| Standard (3 months) | 499 RUB | 90 days | 10 |
| Annual | 1,490 RUB | 365 days | 10 |

Discounts apply via permanent user discounts or promo codes. Defined in `bot_xui/tariffs.py`.

## Database

MySQL 8.0:

- **users** — Telegram users, subscriptions, referrals, discounts, web tokens, autopay settings
- **payments** — YooKassa payment records with status tracking
- **vpn_keys** — Issued VPN configs (client IDs, keys, links, expiry, vpn_type)
- **promocodes** — Discount/bonus codes with usage limits
- **promocode_usages** — Per-user promo redemption log
- **autopay_log** — Autopay charge history and errors
- **awg_server** / **awg_clients** — AmneziaWG server and client records

## Configuration

All settings loaded from `.env` via `config.py`:

| Variable | Description |
|----------|-------------|
| `TELEGRAM_BOT_TOKEN` | Telegram bot token |
| `YOO_KASSA_SHOP_ID` / `YOO_KASSA_SECRET_KEY` | YooKassa production credentials |
| `YOO_KASSA_TEST_SHOP_ID` / `YOO_KASSA_TEST_SECRET_KEY` | YooKassa test credentials |
| `MYSQL_HOST` / `MYSQL_USER` / `MYSQL_PASSWORD` / `MYSQL_DATABASE` | MySQL connection |
| `XUI_HOST` / `XUI_USERNAME` / `XUI_PASSWORD` | 3x-UI panel access |
| `VLESS_DOMAIN` / `VLESS_PBK` / `VLESS_SID` / `VLESS_SNI` | VLESS Reality params |
| `AMNEZIA_WG_API_URL` / `AMNEZIA_WG_API_PASSWORD` | AWG API access |
| `SOFTETHER_SERVER_PASSWORD` / `SOFTETHER_HUB` | SoftEther VPN server |
| `REFERRAL_REWARD_DAYS` / `REFERRAL_NEWCOMER_DAYS` | Referral bonus days |
| `ADMIN_TG_ID` | Admin Telegram user ID |

## Requirements

- Ubuntu 22.04+ (VPS, min 2 vCPU / 4 GB RAM)
- Domain with DNS configured
- Telegram Bot Token
- YooKassa merchant account
- AmneziaWG kernel module

## Installation

```bash
# Clone and install
git clone <repo> ~/vpn-service
cd ~/vpn-service
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# Configure
cp .env.example .env
nano .env

# Start infrastructure
docker compose up -d

# Set up SSL
sudo bash scripts/setup-ssl.sh yourdomain.com

# Start services
sudo systemctl start bot api
sudo systemctl enable bot api
```

## Operations

```bash
# Service management
./restart.sh              # Restart all services
./restart.sh bot          # Restart bot only
./restart.sh api          # Restart API only
./restart.sh status       # Show service status
./restart.sh logs bot     # Tail bot logs

# Docker
docker compose ps
docker compose logs -f x-ui

# Tests
pytest tests/
bash scripts/run_tests.sh   # Run tests + send report to admin via Telegram
```

## Tech Stack

- **Python 3.11+**, FastAPI, Uvicorn, python-telegram-bot 20.7
- **MySQL 8.0**, Docker Compose
- **3x-UI** (VLESS/Xray), **AmneziaWG** (WireGuard fork), **SoftEther VPN**
- **YooKassa** payment gateway
- **APScheduler** for recurring tasks (expiry checks, autopay, winback)
- **Brevo** SMTP for email notifications
- **pytest** for testing (30+ test files)
