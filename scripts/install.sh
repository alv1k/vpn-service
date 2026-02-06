#!/bin/bash

set -e

echo "=== VPN Service Installation Script ==="
echo ""

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Check if running as root
if [[ $EUID -eq 0 ]]; then
   echo -e "${RED}This script should NOT be run as root${NC}"
   exit 1
fi

# Variables
INSTALL_DIR="$HOME/vpn-service"
PYTHON_VERSION="3.10"

echo -e "${GREEN}Step 1: Update system${NC}"
sudo apt update
sudo apt upgrade -y

echo -e "${GREEN}Step 2: Install dependencies${NC}"
sudo apt install -y \
    git \
    docker.io \
    docker-compose \
    python3 \
    python3-pip \
    python3-venv \
    nginx \
    certbot \
    python3-certbot-nginx \
    apache2-utils \
    curl \
    wget \
    ufw

echo -e "${GREEN}Step 3: Configure Docker${NC}"
sudo usermod -aG docker $USER
sudo systemctl enable docker
sudo systemctl start docker

echo -e "${GREEN}Step 4: Configure firewall${NC}"
sudo ufw allow 22/tcp
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw allow 51888/udp
sudo ufw --force enable

echo -e "${GREEN}Step 5: Enable IP forwarding${NC}"
echo "net.ipv4.ip_forward=1" | sudo tee -a /etc/sysctl.conf
echo "net.ipv4.conf.all.src_valid_mark=1" | sudo tee -a /etc/sysctl.conf
sudo sysctl -p

echo -e "${GREEN}Step 6: Clone repository${NC}"
if [ -d "$INSTALL_DIR" ]; then
    echo -e "${YELLOW}Directory already exists. Updating...${NC}"
    cd $INSTALL_DIR
    git pull
else
    git clone https://github.com/YOUR_USERNAME/vpn-service.git $INSTALL_DIR
    cd $INSTALL_DIR
fi

echo -e "${GREEN}Step 7: Setup environment files${NC}"
# Docker environment
if [ ! -f docker-config/.env ]; then
    cp docker-config/.env.example docker-config/.env
    echo -e "${YELLOW}Edit docker-config/.env with your settings${NC}"
fi

# Bot environment
if [ ! -f bot/.env ]; then
    cp bot/.env.example bot/.env
    echo -e "${YELLOW}Edit bot/.env with your settings${NC}"
fi

echo -e "${GREEN}Step 8: Setup Python environment${NC}"
cd $INSTALL_DIR/bot
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

echo -e "${GREEN}Step 9: Setup systemd services${NC}"
sudo cp systemd/vpn-bot.service /etc/systemd/system/
sudo cp systemd/vpn-webhook.service /etc/systemd/system/

# Update paths in systemd files
sudo sed -i "s|/home/your_user|$HOME|g" /etc/systemd/system/vpn-bot.service
sudo sed -i "s|/home/your_user|$HOME|g" /etc/systemd/system/vpn-webhook.service
sudo sed -i "s|your_user|$USER|g" /etc/systemd/system/vpn-bot.service
sudo sed -i "s|your_user|$USER|g" /etc/systemd/system/vpn-webhook.service

sudo systemctl daemon-reload

echo -e "${GREEN}Step 10: Start Docker containers${NC}"
cd $INSTALL_DIR
docker-compose up -d

echo -e "${GREEN}Step 11: Wait for MySQL to be ready${NC}"
sleep 10

echo ""
echo -e "${GREEN}Installation completed!${NC}"
echo ""
echo "Next steps:"
echo "1. Edit environment files:"
echo "   - $INSTALL_DIR/docker-config/.env"
echo "   - $INSTALL_DIR/bot/.env"
echo ""
echo "2. Configure domain and SSL:"
echo "   sudo bash $INSTALL_DIR/scripts/setup-ssl.sh yourdomain.com"
echo ""
echo "3. Start the bot:"
echo "   sudo systemctl start vpn-bot vpn-webhook"
echo "   sudo systemctl enable vpn-bot vpn-webhook"
echo ""
echo "4. Check status:"
echo "   sudo systemctl status vpn-bot"
echo "   docker ps"
echo ""
echo -e "${YELLOW}IMPORTANT: You need to logout and login again for docker group to take effect!${NC}"