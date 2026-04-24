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
│  │ :2053       │  │ :3306          │  │ :51888 (VPN)     │  │
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

- Ubuntu 22.04+ (VPS, min 2 vCPU / 4 GB RAM / 20 GB SSD)
- Static public IP address
- Domain with DNS A-record pointing to the server
- Telegram Bot Token (from @BotFather)
- YooKassa merchant account
- Brevo SMTP account (for email notifications)

## Installation

### 1. System Preparation

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y git docker.io docker-compose python3 python3-pip python3-venv \
  nginx certbot python3-certbot-nginx apache2-utils curl wget ufw

# Enable IP forwarding (required for VPN)
echo "net.ipv4.ip_forward=1" | sudo tee -a /etc/sysctl.conf
sudo sysctl -p
```

### 2. Clone and Install Dependencies

```bash
cd ~
git clone <repo-url> vpn-service
cd vpn-service
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 3. Configure Environment

```bash
cp .env.example .env
nano .env
```

Fill in all required variables (see [Configuration](#configuration) section).

### 4. Docker Services

```bash
sudo usermod -aG docker $USER
sudo systemctl enable docker && sudo systemctl start docker
docker compose up -d
```

This starts MySQL 8.0, 3x-UI (VLESS/Xray), phpMyAdmin, and AmneziaWG containers.

### 5. AmneziaWG Kernel Module

```bash
cd amneziawg-linux-kernel-module
make build
sudo make install
sudo modprobe amneziawg
```

Create AWG interface config at `/etc/amnezia/amneziawg/awg0.conf` with server keys and obfuscation parameters (Jc, Jmin, Jmax, H1-H4, S1, S2).

### 6. SoftEther VPN (Optional)

```bash
# Install SoftEther to /opt/softether/
# Download and compile from https://www.softether-download.com
cd /opt/softether
sudo ./vpnserver start
./vpncmd  # Configure server password and VPN hub
```

### 7. SSL Certificates

```bash
sudo certbot --nginx -d yourdomain.com
sudo systemctl enable certbot.timer
```

### 8. Nginx Configuration

Configure reverse proxy in `/etc/nginx/sites-available/vpn-service`:

| Route | Backend | Notes |
|-------|---------|-------|
| `/` | `127.0.0.1:8000` | Web portal + webhook API |
| `/tiin_admin_panel/` | `127.0.0.1:51821` | Admin panel (HTTP auth) |
| `/phpmyadmin/` | `127.0.0.1:8080` | DB admin (HTTP auth) |
| `/webhook/yookassa` | `127.0.0.1:8000` | Payment webhook (IP-restricted) |

```bash
# Create admin panel HTTP auth
sudo htpasswd -c /etc/nginx/.htpasswd_admin admin

sudo ln -s /etc/nginx/sites-available/vpn-service /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
```

### 9. Systemd Services

Create service files in `/etc/systemd/system/`:

**bot.service** — Telegram bot:
```ini
[Unit]
Description=Telegram Bot (tiin service)
After=network.target

[Service]
User=alvik
WorkingDirectory=/home/alvik/vpn-service/bot_xui
Environment="PATH=/home/alvik/vpn-service/venv/bin"
ExecStart=/home/alvik/vpn-service/venv/bin/python3 bot.py
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
```

**api.service** — FastAPI webhook + web portal:
```ini
[Unit]
Description=FastAPI tiin service api
After=network.target

[Service]
User=alvik
Group=alvik
WorkingDirectory=/home/alvik/vpn-service
Environment="PATH=/home/alvik/vpn-service/venv/bin"
ExecStart=/home/alvik/vpn-service/venv/bin/python -m uvicorn api.webhook:app --host 127.0.0.1 --port 8000
Restart=always
RestartSec=3
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
```

**awg-interface.service** — AmneziaWG network interface:
```ini
[Unit]
Description=AmneziaWG Interface awg0
After=network.target

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/bin/awg-quick up awg0
ExecStop=/usr/bin/awg-quick down awg0

[Install]
WantedBy=multi-user.target
```

**awg-api.service** — AmneziaWG management API:
```ini
[Unit]
Description=AmneziaWG 2.0 API Service
After=network.target mysql.service awg-interface.service

[Service]
User=root
WorkingDirectory=/home/alvik/vpn-service
Environment="PATH=/home/alvik/vpn-service/venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin"
EnvironmentFile=/home/alvik/vpn-service/.env
ExecStart=/home/alvik/vpn-service/venv/bin/uvicorn awg_api.main:app --host 127.0.0.1 --port 51821
Restart=always
RestartSec=5
ProtectHome=read-only
ProtectSystem=strict
ReadWritePaths=/home/alvik/vpn-service/logs /tmp /etc/amnezia/amneziawg
PrivateTmp=true
NoNewPrivileges=true

[Install]
WantedBy=multi-user.target
```

Enable and start all services:
```bash
sudo systemctl daemon-reload
sudo systemctl enable bot api awg-interface awg-api
sudo systemctl start awg-interface awg-api bot api
```

### 10. Firewall (UFW)

```bash
sudo ufw allow 22/tcp       # SSH
sudo ufw allow 80/tcp       # HTTP (redirect to HTTPS)
sudo ufw allow 443/tcp      # HTTPS
sudo ufw allow 51888/udp    # AmneziaWG
sudo ufw allow 2053/tcp    # X-UI panel
sudo ufw allow 992/tcp      # SoftEther main
sudo ufw allow 5556/tcp     # SoftEther alternate
sudo ufw allow 500/udp      # IPsec IKE
sudo ufw allow 4500/udp     # IPsec NAT-T
sudo ufw allow 1701/udp     # L2TP
sudo ufw allow 8443/tcp     # MTProto proxy
sudo ufw enable
```

### 11. Cron Jobs

```bash
crontab -e
```

```cron
# Database backup — daily 21:00
0 21 * * *   /home/alvik/scripts/backup.sh >> /home/alvik/logs/backup.log 2>&1

# Test suite — daily 21:00
0 21 * * *   /home/alvik/vpn-service/scripts/run_tests.sh >> /home/alvik/vpn-service/logs/tests.log 2>&1

# VPN health check — every 3 hours
0 */3 * * *  /home/alvik/vpn-service/scripts/vpn-health-check.sh >> /home/alvik/vpn-service/logs/vpn-health.log 2>&1

# AWG client expiry check — every 30 minutes
*/30 * * * * /home/alvik/vpn-service/venv/bin/python3 /home/alvik/vpn-service/scripts/awg_expiry_check.py >> /home/alvik/vpn-service/logs/awg_expiry.log 2>&1

# Update Russian IP/domain routes — Mondays 04:00
0 4 * * 1    /usr/bin/python3 /home/alvik/vpn-service/scripts/update_ru_routes.py >> /home/alvik/vpn-service/logs/ru_routes_update.log 2>&1

# Winback campaign — daily 03:00
0 3 * * *    /home/alvik/vpn-service/venv/bin/python3 /home/alvik/vpn-service/scripts/win_back_users.py --send >> /home/alvik/vpn-service/logs/winback.log 2>&1

# Channel posts — every 2 days at 12:00
0 12 */2 * * cd /home/alvik/vpn-service && venv/bin/python3 scripts/channel_post.py >> logs/channel_posts.log 2>&1
```

### 12. Verify Installation

```bash
# Check all services are running
systemctl status bot api awg-api awg-interface
docker compose ps

# Check ports
ss -tlnp | grep -E "(443|8000|51821)"
ss -ulnp | grep 51888

# Check AWG interface
awg show awg0

# Test API
curl -s http://127.0.0.1:8000/docs

# Test bot
journalctl -u bot -f
```

## Ports Summary

| Port | Protocol | Service | Access |
|------|----------|---------|--------|
| 22 | TCP | SSH | Public |
| 80 | TCP | HTTP (redirect) | Public |
| 443 | TCP | HTTPS (Nginx) | Public |
| 992 | TCP | SoftEther | Public |
| 5556 | TCP | SoftEther alt | Public |
| 500/4500 | UDP | IPsec | Public |
| 1701 | UDP | L2TP | Public |
| 8443 | TCP | MTProto proxy | Public |
| 51888 | UDP | AmneziaWG | Public |
| 51999 | TCP | X-UI panel | Public |
| 3306 | TCP | MySQL | localhost |
| 8000 | TCP | FastAPI | localhost |
| 8080 | TCP | phpMyAdmin | localhost |
| 51821 | TCP | AWG API | localhost |

## Key Configuration Files

| File | Purpose |
|------|---------|
| `.env` | All environment variables |
| `config.py` | Python config loader |
| `docker-compose.yml` | Docker services |
| `/etc/systemd/system/bot.service` | Bot systemd unit |
| `/etc/systemd/system/api.service` | API systemd unit |
| `/etc/systemd/system/awg-api.service` | AWG API systemd unit |
| `/etc/systemd/system/awg-interface.service` | AWG interface unit |
| `/etc/amnezia/amneziawg/awg0.conf` | AWG interface config |
| `/etc/nginx/sites-available/vpn-service` | Nginx reverse proxy |
| `/etc/letsencrypt/live/{domain}/` | SSL certificates |

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

# Backup
bash ~/scripts/backup.sh
```

## Tech Stack

- **Python 3.11+**, FastAPI, Uvicorn, python-telegram-bot 20.7
- **MySQL 8.0**, Docker Compose
- **3x-UI** (VLESS/Xray), **AmneziaWG** (WireGuard fork), **SoftEther VPN**
- **YooKassa** payment gateway
- **APScheduler** for recurring tasks (expiry checks, autopay, winback)
- **Brevo** SMTP for email notifications
- **pytest** for testing (30+ test files)
