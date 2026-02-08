#!/bin/bash
# Простой скрипт для проверки API AmneziaWG
# Использует только curl и стандартные утилиты

echo "=== Проверка API AmneziaWG ==="

# Загрузка переменных из .env файла (если существует)
if [ -f ../docker-config/.env ]; then
    source ../docker-config/.env
fi

# Настройки по умолчанию
API_URL=${AMNEZIA_WG_API_URL:-"http://localhost:51821"}
PASSWORD=${WG_UI_PASSWORD:-"vtnfvjhajp03"}

echo "API URL: $API_URL"
echo "Пароль: ***${PASSWORD: -3}"

# Временные файлы
COOKIE_JAR="/tmp/amneziawg_cookies_$$"
RESPONSE_FILE="/tmp/amneziawg_response_$$"

# Функция очистки
cleanup() {
    rm -f "$COOKIE_JAR" "$RESPONSE_FILE"
}
trap cleanup EXIT

echo ""
echo "1. Проверка доступности API..."

# Проверка доступности API
if curl -s --connect-timeout 5 "$API_URL/api/session" >/dev/null 2>&1; then
    echo "✓ API доступен"
else
    echo "✗ API недоступен по адресу $API_URL"
    exit 1
fi

echo ""
echo "2. Авторизация..."

# Авторизация
HTTP_CODE=$(curl -s -w "%{http_code}" -o "$RESPONSE_FILE" \
    -X POST \
    -H "Content-Type: application/json" \
    -d "{\"password\":\"$PASSWORD\"}" \
    -c "$COOKIE_JAR" \
    "$API_URL/api/session")

if [ "$HTTP_CODE" -eq 200 ]; then
    echo "✓ Авторизация успешна"
else
    echo "✗ Ошибка авторизации (HTTP $HTTP_CODE)"
    cat "$RESPONSE_FILE"
    exit 1
fi

echo ""
echo "3. Создание тестового клиента..."

# Генерация уникального имени
CLIENT_NAME="test_$(date +%s)_$$"

# Создание клиента
HTTP_CODE=$(curl -s -w "%{http_code}" -o "$RESPONSE_FILE" \
    -X POST \
    -H "Content-Type: application/json" \
    -b "$COOKIE_JAR" \
    -d "{\"name\":\"$CLIENT_NAME\"}" \
    "$API_URL/api/wireguard/client")

if [ "$HTTP_CODE" -eq 200 ]; then
    echo "✓ Клиент '$CLIENT_NAME' создан"
    # Извлечение ID клиента
    CLIENT_ID=$(grep -o '"id":"[^"]*"' "$RESPONSE_FILE" | head -1 | cut -d'"' -f4)
    echo "ID клиента: $CLIENT_ID"
else
    echo "✗ Ошибка создания клиента (HTTP $HTTP_CODE)"
    cat "$RESPONSE_FILE"
    exit 1
fi

echo ""
echo "4. Получение конфигурации клиента..."

# Получение конфигурации
HTTP_CODE=$(curl -s -w "%{http_code}" -o "$RESPONSE_FILE" \
    -X GET \
    -b "$COOKIE_JAR" \
    "$API_URL/api/wireguard/client/$CLIENT_ID/configuration")

if [ "$HTTP_CODE" -eq 200 ]; then
    echo "✓ Конфигурация получена"
    echo "Пример содержимого (первые 5 строк):"
    head -5 "$RESPONSE_FILE"
else
    echo "✗ Ошибка получения конфигурации (HTTP $HTTP_CODE)"
    cat "$RESPONSE_FILE"
    exit 1
fi

echo ""
echo "5. Удаление тестового клиента..."

# Удаление клиента
HTTP_CODE=$(curl -s -w "%{http_code}" -o "$RESPONSE_FILE" \
    -X DELETE \
    -b "$COOKIE_JAR" \
    "$API_URL/api/wireguard/client/$CLIENT_ID")

if [ "$HTTP_CODE" -eq 204 ]; then
    echo "✓ Клиент '$CLIENT_NAME' удален"
else
    echo "✗ Ошибка удаления клиента (HTTP $HTTP_CODE)"
    cat "$RESPONSE_FILE"
    exit 1
fi

echo ""
echo "=== Тестирование завершено успешно ==="
echo "✓ Все операции с API прошли успешно"
echo "✓ Создание клиента: OK"
echo "✓ Получение конфигурации: OK"
echo "✓ Удаление клиента: OK"