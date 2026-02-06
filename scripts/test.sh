#!/bin/bash

API_URL="http://localhost:51821"
PASSWORD="vtnfvjhajp03"

echo "=== Testing AmneziaWG API ==="
echo ""

# 1. Login
echo "1. Login..."
LOGIN_RESPONSE=$(curl -s -c cookies.txt -w "\n%{http_code}" "$API_URL/api/session" \
  -X POST \
  -H "Content-Type: application/json" \
  -d "{\"password\":\"$PASSWORD\"}")

HTTP_CODE=$(echo "$LOGIN_RESPONSE" | tail -n1)
BODY=$(echo "$LOGIN_RESPONSE" | head -n-1)

echo "HTTP Code: $HTTP_CODE"
echo "Response: $BODY"

if [ "$HTTP_CODE" != "200" ]; then
    echo "❌ Login failed!"
    exit 1
fi

echo "✅ Login successful"
echo ""

# 2. Create client
echo "2. Creating test client..."
CLIENT_NAME="test_$(date +%s)"

CREATE_RESPONSE=$(curl -s -b cookies.txt -w "\n%{http_code}" "$API_URL/api/wireguard/client" \
  -X POST \
  -H "Content-Type: application/json" \
  -d "{\"name\":\"$CLIENT_NAME\"}")

HTTP_CODE=$(echo "$CREATE_RESPONSE" | tail -n1)
BODY=$(echo "$CREATE_RESPONSE" | head -n-1)

echo "HTTP Code: $HTTP_CODE"
echo "Response: $BODY"

if [ "$HTTP_CODE" != "200" ]; then
    echo "❌ Client creation failed!"
    echo ""
    echo "Full response:"
    echo "$BODY" | jq . 2>/dev/null || echo "$BODY"
    exit 1
fi

echo "✅ Client created successfully"
echo ""

# Извлекаем ID клиента
CLIENT_ID=$(echo "$BODY" | jq -r '.id' 2>/dev/null)

if [ -z "$CLIENT_ID" ] || [ "$CLIENT_ID" = "null" ]; then
    echo "⚠️ Could not extract client ID"
else
    echo "Client ID: $CLIENT_ID"
    
    # 3. Get config
    echo ""
    echo "3. Getting client config..."
    
    CONFIG_RESPONSE=$(curl -s -b cookies.txt -w "\n%{http_code}" "$API_URL/api/wireguard/client/$CLIENT_ID/configuration")
    
    HTTP_CODE=$(echo "$CONFIG_RESPONSE" | tail -n1)
    CONFIG=$(echo "$CONFIG_RESPONSE" | head -n-1)
    
    echo "HTTP Code: $HTTP_CODE"
    
    if [ "$HTTP_CODE" = "200" ]; then
        echo "✅ Config retrieved"
        echo ""
        echo "Config preview:"
        echo "$CONFIG" | head -n 5
    else
        echo "❌ Failed to get config"
    fi
fi

# Cleanup
rm -f cookies.txt

echo ""
echo "=== Test complete ==="