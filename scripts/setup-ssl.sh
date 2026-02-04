#!/bin/bash

set -e

if [ -z "$1" ]; then
    echo "Usage: $0 <domain>"
    exit 1
fi

DOMAIN=$1
INSTALL_DIR="$HOME/vpn-service"

echo "=== Setting up SSL for $DOMAIN ==="

# Check if Nginx is installed
if ! command -v nginx &> /dev/null; then
    echo "Nginx is not installed. Installing..."
    sudo apt install -y nginx
fi

# Copy Nginx configuration
sudo cp $INSTALL_DIR/nginx/sites-available/vpn-bot.conf /etc/nginx/sites-available/$DOMAIN

# Update domain in config
sudo sed -i "s/yourdomain.com/$DOMAIN/g" /etc/nginx/sites-available/$DOMAIN

# Enable site
sudo ln -sf /etc/nginx/sites-available/$DOMAIN /etc/nginx/sites-enabled/

# Test Nginx config
sudo nginx -t

# Reload Nginx
sudo systemctl reload nginx

# Setup SSL with Certbot
echo "Setting up SSL certificate..."
sudo certbot --nginx -d $DOMAIN

# Setup auto-renewal
sudo systemctl enable certbot.timer

# Create .htpasswd for phpMyAdmin
echo "Create password for phpMyAdmin access:"
sudo htpasswd -c /etc/nginx/.htpasswd admin

echo ""
echo "SSL setup completed!"
echo "Your site is now available at: https://$DOMAIN"
echo "phpMyAdmin: https://$DOMAIN/phpmyadmin"