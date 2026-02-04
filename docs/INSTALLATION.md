# Руководство по установке

## Предварительные требования

### VPS
- Ubuntu 22.04 LTS
- Минимум 1 GB RAM
- Минимум 20 GB SSD
- Статический IP адрес

### Домен
- Зарегистрированный домен
- DNS A-запись указывает на IP вашего VPS

### Сервисы
- [Telegram Bot Token](https://t.me/BotFather)
- [YooKassa аккаунт](https://yookassa.ru)

## Шаг 1: Подготовка VPS
```bash
# Обновление системы
sudo apt update && sudo apt upgrade -y

# Настройка hostname
sudo hostnamectl set-hostname vpn-server

# Создание swap (опционально, для VPS с 1GB RAM)
sudo fallocate -l 2G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```

## Шаг 2: Настройка DNS

Создайте A-запись для вашего домена:
```
Type: A
Name: @
Value: YOUR_VPS_IP
TTL: 3600
```

Проверка:
```bash
dig yourdomain.com +short
# Должен вернуть IP вашего VPS
```

## Шаг 3: Автоматическая установка
```bash
# Скачать установщик
curl -sSL https://raw.githubusercontent.com/YOUR_USERNAME/vpn-service/main/scripts/install.sh -o install.sh

# Проверить скрипт (опционально)
less install.sh

# Запустить установку
bash install.sh
```

## Шаг 4: Настройка переменных окружения

### Docker (.env для AmneziaWG)
```bash
nano ~/vpn-service/docker/amneziawg/.env
```

Заполните:
```env
VPS_IP=91.132.161.112
WG_UI_PASSWORD=your_secure_password
MYSQL_ROOT_PASSWORD=root_password
MYSQL_USER=vpn_admin
MYSQL_PASSWORD=vpn_password
```

### Bot (.env для Telegram бота)
```bash
nano ~/vpn-service/bot/.env
```

Заполните:
```env
TELEGRAM_BOT_TOKEN=1234567890:ABCdefGHIjklMNOpqrsTUVwxyz
ADMIN_TELEGRAM_IDS=123456789
DB_PASSWORD=vpn_password  # Такой же как MYSQL_PASSWORD
YOOKASSA_SHOP_ID=123456
YOOKASSA_SECRET_KEY=live_xxx
WG_API_PASSWORD=your_secure_password  # Такой же как WG_UI_PASSWORD
WG_HOST=91.132.161.112
BASE_URL=https://yourdomain.com
```

## Шаг 5: Запуск Docker контейнеров
```bash
cd ~/vpn-service/docker/amneziawg
docker-compose up -d

# Проверка
docker ps
```

Вы должны увидеть:
- amneziawg
- vpn_mysql
- phpmyadmin
- vpn_redis

## Шаг 6: Настройка SSL
```bash
sudo bash ~/vpn-service/scripts/setup-ssl.sh yourdomain.com
```

Следуйте инструкциям certbot:
- Введите email
- Согласитесь с ToS
- Выберите redirect HTTP -> HTTPS

## Шаг 7: Запуск бота
```bash
# Перезагрузка для применения docker группы
# ИЛИ выйдите и войдите заново
newgrp docker

# Запуск сервисов
sudo systemctl start vpn-bot
sudo systemctl start vpn-webhook

# Автозапуск
sudo systemctl enable vpn-bot
sudo systemctl enable vpn-webhook

# Проверка статуса
sudo systemctl status vpn-bot
sudo systemctl status vpn-webhook
```

## Шаг 8: Проверка установки

### Проверка Docker
```bash
docker ps
# Все контейнеры должны быть в статусе "Up"
```

### Проверка базы данных
```bash
docker exec -it vpn_mysql mysql -u vpn_admin -p vpn_service
# Введите пароль из .env
SHOW TABLES;
# Должны быть таблицы: users, subscriptions, payments и т.д.
```

### Проверка бота
```bash
sudo journalctl -u vpn-bot -f
# Должно быть "Bot started"
```

### Проверка webhook
```bash
curl https://yourdomain.com/health
# Должен вернуть: {"status":"healthy"}
```

### Проверка Telegram бота
Откройте Telegram, найдите своего бота и отправьте `/start`

## Шаг 9: Настройка YooKassa webhook

1. Войдите в [личный кабинет YooKassa](https://yookassa.ru/my)
2. Перейдите в "Настройки" → "Уведомления"
3. Добавьте URL webhook: `https://yourdomain.com/yookassa/webhook`
4. Выберите события: `payment.succeeded`, `payment.canceled`
5. Сохраните

## Шаг 10: Первый запуск

1. Откройте Telegram бота
2. Отправьте `/start`
3. Попробуйте купить тестовую подписку
4. Используйте [тестовую карту YooKassa](https://yookassa.ru/developers/payment-acceptance/testing-and-going-live/testing)

## Troubleshooting

### Docker контейнеры не запускаются
```bash
docker-compose logs
```

### Бот не отвечает
```bash
sudo journalctl -u vpn-bot -n 50
```

### База данных недоступна
```bash
docker logs vpn_mysql
```

### SSL не работает
```bash
sudo nginx -t
sudo systemctl status nginx
sudo certbot renew --dry-run
```

## Следующие шаги

- [Конфигурация](CONFIGURATION.md)
- [Настройка тарифов](CONFIGURATION.md#тарифы)
- [Бэкапы](CONFIGURATION.md#бэкапы)