# VPN Service - AmneziaWG + Telegram Bot

Автоматизированный сервис продажи VPN подписок на базе AmneziaWG с Telegram ботом и интеграцией YooKassa.

## Возможности

- 🔐 VPN на базе AmneziaWG с обфускацией
- 🤖 Telegram бот для продажи подписок
- 💳 Автоматический прием платежей через YooKassa
- 📊 Web-интерфейс управления (AmneziaWG UI)
- 🗄️ MySQL база данных с phpMyAdmin
- 📈 Статистика и аналитика
- 🔄 Автоматическое продление подписок

## Требования

- VPS с Ubuntu 22.04 (минимум 1GB RAM, 20GB SSD)
- Домен с настроенными DNS записями
- Telegram Bot Token
- YooKassa аккаунт

## Быстрая установка
```bash
# 1. Скачать и запустить установщик
curl -sSL https://raw.githubusercontent.com/YOUR_USERNAME/vpn-service/main/scripts/install.sh | bash

# 2. Настроить переменные окружения
nano ~/vpn-service/docker-config/.env
nano ~/vpn-service/bot/.env

# 3. Настроить SSL
sudo bash ~/vpn-service/scripts/setup-ssl.sh yourdomain.com

# 4. Запустить сервисы
sudo systemctl start vpn-bot vpn-webhook
sudo systemctl enable vpn-bot vpn-webhook
```

## Подробная документация

- [Установка](docs/INSTALLATION.md)
- [Конфигурация](docs/CONFIGURATION.md)
- [Решение проблем](docs/TROUBLESHOOTING.md)
- [API документация](docs/API.md)

## Архитектура
```
┌─────────────────────────────────────┐
│           VPS Ubuntu 22.04          │
├─────────────────────────────────────┤
│  Docker:                            │
│  - AmneziaWG (UDP 51888)           │
│  - MySQL + phpMyAdmin              │
│  - Redis                           │
├─────────────────────────────────────┤
│  Services:                          │
│  - Telegram Bot                    │
│  - Payment Webhook                 │
│  - Nginx (SSL)                     │
└─────────────────────────────────────┘
```

## Управление

### Проверка статуса
```bash
# Сервисы
sudo systemctl status vpn-bot
sudo systemctl status vpn-webhook

# Docker
docker ps
docker logs amneziawg
docker logs vpn_mysql
```

### Обновление
```bash
cd ~/vpn-service
bash scripts/deploy.sh
```

### Бэкап
```bash
bash ~/vpn-service/scripts/backup.sh
```

## Тарифы

Настраиваются в `bot/payments.py`:
- 1 месяц - 150₽
- 3 месяца - 400₽
- 6 месяцев - 750₽
- 1 год - 1400₽

## Безопасность

- SSL/TLS шифрование
- Firewall (UFW)
- Защита phpMyAdmin паролем
- Безопасное хранение ключей в .env
- Логирование всех действий

## Поддержка

- Telegram: @your_support
- Email: support@yourdomain.com
- Issues: GitHub Issues

## Лицензия

MIT License - см. [LICENSE](LICENSE)

## Авторы

- Ваше имя (@username)

## Благодарности

- AmneziaVPN Team
- python-telegram-bot
- YooKassa API