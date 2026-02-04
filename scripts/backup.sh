#!/bin/bash

set -e

BACKUP_DIR="$HOME/vpn-backups"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
INSTALL_DIR="$HOME/vpn-service"

echo "=== Creating backup at $TIMESTAMP ==="

# Create backup directory
mkdir -p $BACKUP_DIR

# Backup MySQL database
echo "Backing up MySQL database..."
docker exec vpn_mysql mysqldump -u vpn_admin -p$(grep MYSQL_PASSWORD $INSTALL_DIR/docker/amneziawg/.env | cut -d '=' -f2) vpn_service > $BACKUP_DIR/db_backup_$TIMESTAMP.sql

# Backup WireGuard configs
echo "Backing up WireGuard configs..."
tar -czf $BACKUP_DIR/wg_configs_$TIMESTAMP.tar.gz -C $INSTALL_DIR/docker/amneziawg config/

# Backup environment files
echo "Backing up environment files..."
tar -czf $BACKUP_DIR/env_files_$TIMESTAMP.tar.gz \
    $INSTALL_DIR/docker/amneziawg/.env \
    $INSTALL_DIR/bot/.env

# Remove backups older than 30 days
find $BACKUP_DIR -type f -mtime +30 -delete

echo "Backup completed: $BACKUP_DIR"
echo "Files:"
ls -lh $BACKUP_DIR/*$TIMESTAMP*