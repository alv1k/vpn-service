cat > ~/vpn-service/scripts/reset-amneziawg.sh << 'EOF'
#!/bin/bash
set -e

echo "🔍 Checking for multiple docker-compose.yml files..."

# Проверка на дубликаты
COMPOSE_FILES=$(find ~/vpn-service -name "docker-compose.yml" -type f)
COUNT=$(echo "$COMPOSE_FILES" | wc -l)

if [ $COUNT -gt 1 ]; then
    echo "⚠️  Found multiple docker-compose.yml files:"
    echo "$COMPOSE_FILES"
    echo ""
    echo "Please keep only one in: ~/vpn-service/docker/amneziawg/"
    exit 1
fi

# Переход в правильную директорию
COMPOSE_DIR="$HOME/vpn-service/docker/amneziawg"

if [ ! -f "$COMPOSE_DIR/docker-compose.yml" ]; then
    echo "❌ docker-compose.yml not found in $COMPOSE_DIR"
    exit 1
fi

cd "$COMPOSE_DIR"

echo "🛑 Stopping containers from correct location..."
docker-compose down

# Принудительное удаление контейнеров (на случай если они остались)
echo "🗑️  Force removing old containers..."
docker rm -f amneziawg vpn_mysql phpmyadmin vpn_redis 2>/dev/null || true

echo "🧹 Cleaning WireGuard config..."
sudo rm -rf ./config/*

echo "🚀 Starting containers..."
docker-compose up -d

echo "⏳ Waiting for startup..."
sleep 5

echo ""
echo "📋 Checking status..."
docker logs amneziawg --tail 20

echo ""
echo "🌐 WireGuard interface:"
ip addr show wg0 2>/dev/null || echo "⚠️  wg0 not found yet (wait a few seconds)"

echo ""
echo "📊 Container status:"
docker ps --filter "name=amneziawg" --filter "name=vpn_mysql"

echo ""
echo "✅ Done! Web UI: http://91.132.161.112:51821"
EOF

chmod +x ~/vpn-service/scripts/reset-amneziawg.sh