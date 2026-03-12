#!/bin/bash

# Скрипт создания клиентского конфига OpenVPN (.ovpn)
# Использование: ./create_ovpn_client.sh <имя_клиента>

set -e

CLIENT_NAME="$1"
if [ -z "$CLIENT_NAME" ]; then
    echo "❌ Использование: $0 <имя_клиента>"
    echo "   Пример: $0 ivan"
    exit 1
fi

# Настройки
SERVER_IP="91.132.161.112"
SERVER_PORT=1194
PROTO="udp"
CIPHER="AES-256-GCM"
AUTH="SHA256"

EASYRSA="/usr/share/easy-rsa/easyrsa"
PKI_DIR="/etc/openvpn/server/pki"
CA_CERT="/etc/openvpn/server/keys/ca.crt"
CA_KEY="/home/alvik/easy-rsa/pki/private/ca.key"
OUTPUT_DIR="/home/alvik/vpn-service/clients"

mkdir -p "$OUTPUT_DIR"

# Инициализация PKI если не существует
if [ ! -d "$PKI_DIR" ]; then
    echo "🔹 Инициализация PKI..."
    EASYRSA_PKI="$PKI_DIR" $EASYRSA init-pki

    # Копируем существующий CA в PKI
    cp "$CA_CERT" "$PKI_DIR/ca.crt"

    # Проверяем наличие CA ключа
    if [ -f "$CA_KEY" ]; then
        mkdir -p "$PKI_DIR/private"
        cp "$CA_KEY" "$PKI_DIR/private/ca.key"
    fi

    # Создаём файлы, которые easy-rsa ожидает от build-ca
    mkdir -p "$PKI_DIR/issued" "$PKI_DIR/certs_by_serial" "$PKI_DIR/reqs"
    touch "$PKI_DIR/index.txt"
    touch "$PKI_DIR/index.txt.attr"
    echo "01" > "$PKI_DIR/serial"
    echo "✔ PKI инициализирован"
fi

# Проверка — не существует ли уже клиент
if [ -f "$PKI_DIR/issued/${CLIENT_NAME}.crt" ]; then
    echo "⚠️ Сертификат для '$CLIENT_NAME' уже существует."
    read -p "Пересоздать? (y/n): " CONFIRM
    if [ "$CONFIRM" != "y" ]; then
        echo "Отмена."
        exit 0
    fi
    # Отзываем старый сертификат
    EASYRSA_PKI="$PKI_DIR" $EASYRSA --batch revoke "$CLIENT_NAME" 2>/dev/null || true
fi

# Генерация ключа и запроса клиента
echo "🔹 Генерация ключа и запроса для '$CLIENT_NAME'..."
EASYRSA_PKI="$PKI_DIR" $EASYRSA --batch gen-req "$CLIENT_NAME" nopass

# Подписание сертификата клиента
echo "🔹 Подписание сертификата..."
EASYRSA_PKI="$PKI_DIR" $EASYRSA --batch sign-req client "$CLIENT_NAME"

# Пути к сгенерированным файлам
CLIENT_CERT="$PKI_DIR/issued/${CLIENT_NAME}.crt"
CLIENT_KEY="$PKI_DIR/private/${CLIENT_NAME}.key"

# Проверка файлов
for f in "$CA_CERT" "$CLIENT_CERT" "$CLIENT_KEY"; do
    if [ ! -f "$f" ]; then
        echo "❌ Файл не найден: $f"
        exit 1
    fi
done

# Создание .ovpn файла
OVPN_FILE="$OUTPUT_DIR/${CLIENT_NAME}.ovpn"

cat > "$OVPN_FILE" << EOF
client
dev tun
proto $PROTO
remote $SERVER_IP $SERVER_PORT
resolv-retry infinite
nobind
persist-key
persist-tun
remote-cert-tls server
cipher $CIPHER
auth $AUTH
verb 3

<ca>
$(cat "$CA_CERT")
</ca>

<cert>
$(sed -n '/-----BEGIN CERTIFICATE-----/,/-----END CERTIFICATE-----/p' "$CLIENT_CERT")
</cert>

<key>
$(cat "$CLIENT_KEY")
</key>
EOF

echo
echo "✔ Конфиг создан: $OVPN_FILE"
echo "  Передайте этот файл клиенту для подключения."
echo
echo "📋 Список клиентов:"
ls -1 "$OUTPUT_DIR"/*.ovpn 2>/dev/null | sed 's|.*/||'
