#!/bin/bash

# Примеры curl-команд для проверки API AmneziaWG

# Настройки (замените на ваши значения)
API_URL="http://localhost:51821"  # URL AmneziaWG Web UI
PASSWORD="vtnfvjhajp03"            # Пароль от Web UI

# Имя клиента для теста
CLIENT_NAME="test_client_$(date +%s)"

echo "=== Проверка API AmneziaWG ==="
echo "API URL: $API_URL"
echo "Клиент: $CLIENT_NAME"
echo ""

# 1. Авторизация
echo "1. Авторизация в API..."
RESPONSE=$(curl -s -X POST \
  -H "Content-Type: application/json" \
  -d "{\"password\":\"$PASSWORD\"}" \
  -c cookies.txt \
  "$API_URL/api/session" \
  -w "\n%{http_code}")

STATUS=$(echo "$RESPONSE" | tail -n1)
BODY=$(echo "$RESPONSE" | head -n-1)

if [ "$STATUS" -eq 200 ]; then
    echo "✓ Авторизация успешна"
else
    echo "✗ Ошибка авторизации: $STATUS"
    echo "$BODY"
    exit 1
fi

echo ""

# 2. Создание клиента
echo "2. Создание клиента '$CLIENT_NAME'..."
RESPONSE=$(curl -s -X POST \
  -H "Content-Type: application/json" \
  -b cookies.txt \
  -d "{\"name\":\"$CLIENT_NAME\"}" \
  "$API_URL/api/wireguard/client" \
  -w "\n%{http_code}")

STATUS=$(echo "$RESPONSE" | tail -n1)
BODY=$(echo "$RESPONSE" | head -n-1)

if [ "$STATUS" -eq 200 ]; then
    echo "✓ Клиент создан успешно"
    CLIENT_ID=$(echo "$BODY" | grep -o '"id":"[^"]*"' | head -1 | cut -d'"' -f4)
    echo "ID клиента: $CLIENT_ID"
else
    echo "✗ Ошибка создания клиента: $STATUS"
    echo "$BODY"
    exit 1
fi

echo ""

# 3. Получение списка клиентов
echo "3. Получение списка клиентов..."
curl -s -X GET \
  -b cookies.txt \
  "$API_URL/api/wireguard/client" \
  -w "\n%{http_code}" | head -n-1 | python -m json.tool

echo ""

# 4. Получение конфигурации клиента
echo "4. Получение конфигурации клиента..."
RESPONSE=$(curl -s -X GET \
  -b cookies.txt \
  "$API_URL/api/wireguard/client/$CLIENT_ID/configuration" \
  -w "\n%{http_code}")

STATUS=$(echo "$RESPONSE" | tail -n1)
BODY=$(echo "$RESPONSE" | head -n-1)

if [ "$STATUS" -eq 200 ]; then
    echo "✓ Конфигурация получена"
    echo "Пример содержимого (первые 10 строк):"
    echo "$BODY" | head -10
else
    echo "✗ Ошибка получения конфигурации: $STATUS"
    echo "$BODY"
    exit 1
fi

echo ""

# 5. Удаление клиента
echo "5. Удаление клиента '$CLIENT_NAME'..."
RESPONSE=$(curl -s -X DELETE \
  -b cookies.txt \
  "$API_URL/api/wireguard/client/$CLIENT_ID" \
  -w "\n%{http_code}")

STATUS=$(echo "$RESPONSE" | tail -n1)

if [ "$STATUS" -eq 204 ]; then
    echo "✓ Клиент удален успешно"
else
    echo "✗ Ошибка удаления клиента: $STATUS"
    echo "$(echo "$RESPONSE" | head -n-1)"
    exit 1
fi

echo ""

# 6. Получение списка клиентов после удаления
echo "6. Получение списка клиентов после удаления..."
curl -s -X GET \
  -b cookies.txt \
  "$API_URL/api/wireguard/client" \
  -w "\n%{http_code}" | head -n-1 | python -m json.tool

# Очистка
rm -f cookies.txt

echo ""
echo "=== Тестирование завершено ==="