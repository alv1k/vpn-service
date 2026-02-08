#!/bin/bash

# Скрипт для проверки API AmneziaWG
# Проверяет создание клиента через API

set -e

# Загрузка переменных из .env файла
if [ -f ../docker-config/.env ]; then
    export $(cat ../docker-config/.env | xargs)
fi

# Настройки по умолчанию
API_URL=${AMNEZIA_WG_API_URL:-"http://localhost:51821"}
PASSWORD=${WG_UI_PASSWORD:-"vtnfvjhajp03"}

echo "=== Тестирование API AmneziaWG ==="
echo "API URL: $API_URL"
echo "Password: ***${PASSWORD: -3}"

# Временные файлы
COOKIES_FILE=$(mktemp)
RESPONSE_FILE=$(mktemp)

# Функция очистки
cleanup() {
    rm -f "$COOKIES_FILE" "$RESPONSE_FILE"
}
trap cleanup EXIT

echo ""
echo "1. Авторизация в API..."

# Авторизация
curl -s -X POST \
  -H "Content-Type: application/json" \
  -d "{\"password\":\"$PASSWORD\"}" \
  -c "$COOKIES_FILE" \
  "$API_URL/api/session" \
  -w "\nHTTP Status: %{http_code}\n" \
  -o "$RESPONSE_FILE"

AUTH_STATUS=$(curl -s -w "%{http_code}" -o /dev/null -X POST \
  -H "Content-Type: application/json" \
  -d "{\"password\":\"$PASSWORD\"}" \
  -c "$COOKIES_FILE" \
  "$API_URL/api/session")

if [ "$AUTH_STATUS" -eq 200 ]; then
    echo "✓ Авторизация успешна"
else
    echo "✗ Ошибка авторизации (HTTP $AUTH_STATUS)"
    cat "$RESPONSE_FILE"
    exit 1
fi

echo ""
echo "2. Получение списка клиентов до создания нового..."

# Получение списка клиентов
curl -s -X GET \
  -b "$COOKIES_FILE" \
  "$API_URL/api/wireguard/client" \
  -o "$RESPONSE_FILE"

echo "Клиенты до создания нового:"
cat "$RESPONSE_FILE" | python3 -m json.tool || echo "Не удалось распарсить JSON"

echo ""
echo "3. Создание нового клиента..."

# Генерация уникального имени для тестового клиента
CLIENT_NAME="test_client_$(date +%s)"

# Создание клиента
CREATE_RESPONSE=$(curl -s -X POST \
  -H "Content-Type: application/json" \
  -b "$COOKIES_FILE" \
  -d "{\"name\":\"$CLIENT_NAME\"}" \
  "http://344988.snk.wtf:51821/api/wireguard/client" \
  -w "\nHTTP Status: %{http_code}" \
  -o "$RESPONSE_FILE")

CREATE_STATUS=$(curl -s -w "%{http_code}" -o /dev/null -X POST \
  -H "Content-Type: application/json" \
  -b "$COOKIES_FILE" \
  -d "{\"name\":\"$CLIENT_NAME\"}" \
  "$API_URL/api/wireguard/client")

if [ "$CREATE_STATUS" -eq 200 ]; then
    echo "✓ Клиент '$CLIENT_NAME' успешно создан"
    CLIENT_ID=$(cat "$RESPONSE_FILE" | python3 -m json.tool | grep -o '"id":[^,}]*' | head -1 | cut -d'"' -f4)
    echo "ID клиента: $CLIENT_ID"
else
    echo "✗ Ошибка создания клиента (HTTP $CREATE_STATUS)"
    cat "$RESPONSE_FILE"
    exit 1
fi

echo ""
echo "4. Получение конфигурации клиента..."

# Получение конфигурации клиента
CONFIG_RESPONSE=$(curl -s -X GET \
  -b "$COOKIES_FILE" \
  "$API_URL/api/wireguard/client/$CLIENT_ID/configuration" \
  -w "\nHTTP Status: %{http_code}" \
  -o "$RESPONSE_FILE")

CONFIG_STATUS=$(curl -s -w "%{http_code}" -o /dev/null -X GET \
  -b "$COOKIES_FILE" \
  "$API_URL/api/wireguard/client/$CLIENT_ID/configuration")

if [ "$CONFIG_STATUS" -eq 200 ]; then
    echo "✓ Конфигурация клиента получена"
    echo "Пример содержимого конфига (первые 10 строк):"
    head -10 "$RESPONSE_FILE"
else
    echo "✗ Ошибка получения конфигурации (HTTP $CONFIG_STATUS)"
    cat "$RESPONSE_FILE"
    exit 1
fi

echo ""
echo "5. Получение списка клиентов после создания..."

# Получение списка клиентов после создания
curl -s -X GET \
  -b "$COOKIES_FILE" \
  "$API_URL/api/wireguard/client" \
  -o "$RESPONSE_FILE"

echo "Клиенты после создания нового:"
cat "$RESPONSE_FILE" | python3 -m json.tool || echo "Не удалось распарсить JSON"

echo ""
echo "6. Удаление тестового клиента..."

# Удаление тестового клиента
DELETE_RESPONSE=$(curl -s -X DELETE \
  -b "$COOKIES_FILE" \
  "$API_URL/api/wireguard/client/$CLIENT_ID" \
  -w "\nHTTP Status: %{http_code}" \
  -o "$RESPONSE_FILE")

DELETE_STATUS=$(curl -s -w "%{http_code}" -o /dev/null -X DELETE \
  -b "$COOKIES_FILE" \
  "$API_URL/api/wireguard/client/$CLIENT_ID")

if [ "$DELETE_STATUS" -eq 204 ]; then
    echo "✓ Клиент '$CLIENT_NAME' успешно удален"
else
    echo "✗ Ошибка удаления клиента (HTTP $DELETE_STATUS)"
    cat "$RESPONSE_FILE"
    exit 1
fi

echo ""
echo "=== Тестирование завершено успешно ==="
echo "- Авторизация: ✓"
echo "- Создание клиента: ✓"
echo "- Получение конфигурации: ✓"
echo "- Удаление клиента: ✓"
echo ""
echo "Клиент был успешно создан и удален через API AmneziaWG"