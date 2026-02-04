#!/bin/bash

set -e

INSTALL_DIR="$HOME/vpn-service"

echo "=== Deploying VPN Service Update ==="

cd $INSTALL_DIR

# Pull latest changes
echo "Pulling latest changes from GitHub..."
git pull

# Update Docker containers
echo "Updating Docker containers..."
cd docker/amneziawg
docker-compose pull
docker-compose up -d

# Update Python dependencies
echo "Updating Python dependencies..."
cd $INSTALL_DIR/bot
source venv/bin/activate
pip install --upgrade pip
pip install -r ./requirements.txt

# Restart services
echo "Restarting services..."
sudo systemctl restart bot
sudo systemctl restart api

# Check status
echo ""
echo "Checking service status..."
sudo systemctl status bot --no-pager
sudo systemctl status api --no-pager
docker ps

echo ""
echo "Deployment completed!"