#!/bin/bash

# Скрипт проверки доступности OpenVPN
# Аналог check_wg.sh для OpenVPN сервера

# Настройки
OVPN_INTERFACE="tun0"
OVPN_PORT=993
OVPN_SERVICE="openvpn@server"
EXT_IF="ens3"
STATUS_LOG="/etc/openvpn/openvpn-status.log"
ENV_FILE="/home/alvik/vpn-service/.env"

# Загружаем переменные из .env
if [ -f "$ENV_FILE" ]; then
    TELEGRAM_BOT_TOKEN=$(grep '^TELEGRAM_BOT_TOKEN=' "$ENV_FILE" | cut -d'=' -f2-)
    ADMIN_TG_ID=$(grep '^ADMIN_TG_ID=' "$ENV_FILE" | cut -d'=' -f2-)
fi
ADMIN_TG_ID="${ADMIN_TG_ID:-364224373}"

CRITICAL_FAIL=0

echo "$(date '+%Y-%m-%d %H:%M:%S') === Проверка OpenVPN ==="

# --- Проверка 1: Интерфейс tun0 ---
echo
echo "🔹 Проверка интерфейса: $OVPN_INTERFACE"
if ip a show "$OVPN_INTERFACE" &>/dev/null; then
    echo "✔ Интерфейс $OVPN_INTERFACE существует"
else
    echo "❌ Интерфейс $OVPN_INTERFACE не найден"
    CRITICAL_FAIL=1
fi

# --- Проверка 2: Порт 993/TCP ---
echo
echo "🔹 Проверка, слушает ли OpenVPN TCP порт $OVPN_PORT"
if ss -tlpn | grep ":$OVPN_PORT " &>/dev/null; then
    echo "✔ Порт $OVPN_PORT слушает OpenVPN"
else
    echo "❌ Порт $OVPN_PORT не слушает (OpenVPN не поднят или порт заблокирован)"
    CRITICAL_FAIL=1
fi

# --- Проверка 3: UFW ---
echo
echo "🔹 Проверка правил UFW для порта $OVPN_PORT"
if sudo ufw status | grep "$OVPN_PORT" &>/dev/null; then
    echo "✔ Порт $OVPN_PORT разрешён в UFW"
else
    echo "❌ Порт $OVPN_PORT не найден в правилах UFW, открываем..."
    sudo ufw allow "$OVPN_PORT/tcp"
    echo "✔ Порт $OVPN_PORT теперь открыт в UFW"
fi

# --- Проверка 4: NAT/masquerade ---
echo
echo "🔹 Проверка NAT для выхода в интернет через $EXT_IF"
if sudo iptables -t nat -L POSTROUTING -n -v | grep "$EXT_IF" &>/dev/null; then
    echo "✔ NAT настроен через $EXT_IF"
else
    echo "❌ NAT не найден. Добавим правило..."
    sudo iptables -t nat -A POSTROUTING -o "$EXT_IF" -j MASQUERADE
    echo "✔ NAT добавлен"
fi

# --- Проверка 5: Сервис активен ---
echo
echo "🔹 Проверка статуса сервиса $OVPN_SERVICE"
if systemctl is-active --quiet "$OVPN_SERVICE"; then
    echo "✔ Сервис $OVPN_SERVICE активен"
else
    echo "❌ Сервис $OVPN_SERVICE не активен"
    CRITICAL_FAIL=1
fi

# --- Обработка критических ошибок ---
if [ "$CRITICAL_FAIL" -eq 1 ]; then
    echo
    echo "⚠️ Обнаружена критическая ошибка. Перезапуск $OVPN_SERVICE..."
    sudo systemctl restart "$OVPN_SERVICE"
    sleep 5

    # Повторная проверка
    RESTART_OK=1
    if ! ip a show "$OVPN_INTERFACE" &>/dev/null; then
        RESTART_OK=0
    fi
    if ! ss -tlpn | grep ":$OVPN_PORT " &>/dev/null; then
        RESTART_OK=0
    fi
    if ! systemctl is-active --quiet "$OVPN_SERVICE"; then
        RESTART_OK=0
    fi

    if [ "$RESTART_OK" -eq 1 ]; then
        echo "✔ Перезапуск успешен. OpenVPN работает."
        MSG="✅ OpenVPN: сервис был перезапущен автоматически и работает."
    else
        echo "❌ Перезапуск не помог. OpenVPN всё ещё недоступен!"
        MSG="🚨 OpenVPN: сервис НЕ удалось восстановить после автоматического перезапуска! Требуется ручное вмешательство."
    fi

    # Отправка уведомления в Telegram
    if [ -n "$TELEGRAM_BOT_TOKEN" ] && [ -n "$ADMIN_TG_ID" ]; then
        curl -s -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
            -d chat_id="$ADMIN_TG_ID" \
            -d text="$MSG" \
            -d parse_mode="HTML" &>/dev/null
        echo "📨 Уведомление отправлено в Telegram"
    else
        echo "⚠️ Не удалось отправить уведомление: отсутствует TELEGRAM_BOT_TOKEN или ADMIN_TG_ID"
    fi
fi

# --- Проверка 6: Подключённые клиенты ---
echo
echo "🔹 Подключённые клиенты OpenVPN"
STATUS_DATA=$(sudo cat "$STATUS_LOG" 2>/dev/null)
if [ -n "$STATUS_DATA" ]; then
    CLIENT_COUNT=$(echo "$STATUS_DATA" | awk '/^CLIENT LIST/,/^ROUTING TABLE/' | grep -c '^[^C].*,.*,.*,')
    echo "✔ Подключено клиентов: $CLIENT_COUNT"
    if [ "$CLIENT_COUNT" -gt 0 ]; then
        echo "$STATUS_DATA" | awk '/^CLIENT LIST/,/^ROUTING TABLE/' | grep -v '^CLIENT LIST' | grep -v '^Common Name' | grep -v '^ROUTING TABLE' | head -20
    fi
else
    echo "⚠️ Файл статуса $STATUS_LOG не найден"
fi

echo
echo "🔹 Скрипт завершён. Если все галочки ✔, OpenVPN работает корректно."
