#!/bin/bash
set -e

echo "=== FULL RESET AmneziaWG ==="
echo "⚠️  This will delete ALL VPN clients and configs!"
echo ""
read -p "Continue? (yes/no): " confirm

if [ "$confirm" != "yes" ]; then
    echo "Cancelled."
    exit 0
fi

cd ~/vpn-service/docker/amneziawg

echo "🛑 Stopping containers..."
docker-compose down -v

echo "🗑️  Removing old container..."
docker rm -f amneziawg 2>/dev/null || true

echo "🧹 Cleaning config directory..."
sudo rm -rf ./config
mkdir -p ./config

echo "🔌 Removing wg0 interface (if exists)..."
sudo ip link delete wg0 2>/dev/null || true

echo "🚀 Starting fresh containers..."
docker-compose up -d

echo "⏳ Waiting 10 seconds for startup..."
sleep 10

echo ""
echo "📋 Checking logs..."
docker logs amneziawg --tail 30

echo ""
echo "🌐 Checking wg0 interface..."
if ip addr show wg0 &>/dev/null; then
    echo "✓ wg0 created successfully"
    ip addr show wg0 | grep "inet "
else
    echo "✗ wg0 NOT found!"
fi

echo ""
echo "🔍 Checking for peers..."
sudo awg show wg0 2>/dev/null || echo "awg command failed"

echo ""
echo "✅ Full reset completed!"
echo "Web UI: http://91.132.161.112:51821"
echo "Password: your_amnezia_api_password_here"
