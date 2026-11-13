#!/usr/bin/env bash
# ==============================================================================
# TIIN VPN — Emergency Disaster Recovery & Auto-Deployment Script
# Usage on fresh Ubuntu 22.04 VPS:
#   sudo bash emergency_restore.sh [--source nl|ru|local] [--backup-dir /path]
# ==============================================================================

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

log() { echo -e "${CYAN}[$(date '+%H:%M:%S')]${NC} $*"; }
success() { echo -e "${GREEN}✔ $*${NC}"; }
warn() { echo -e "${YELLOW}⚠ $*${NC}"; }
error() { echo -e "${RED}✖ $*${NC}"; exit 1; }

SOURCE="${1:-nl}"
BACKUP_TMP="/tmp/tiin_restore"
TARGET_DIR="/home/alvik/vpn-service"
NL_HOST="alvik@2.26.76.210"
NL_DIR="/home/alvik/backups-offsite"
RU_HOST="ru-server"
RU_DIR="/root/backups-offsite"

if [[ $EUID -ne 0 ]]; then
    error "Этот скрипт должен быть запущен с правами root (sudo)."
fi

NEW_IP=$(curl -4 -s --max-time 5 https://ifconfig.me || echo "UNKNOWN_IP")
log "Запуск экстренного восстановления на сервере с IP: ${NEW_IP}"

# 1. Установка системных зависимостей
log "[1/7] Установка системных пакетов..."
apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
    curl wget git jq sqlite3 ufw fail2ban \
    dkms linux-headers-$(uname -r) wireguard-tools \
    python3 python3-venv python3-pip nginx docker.io docker-compose-v2 > /dev/null
systemctl enable --now docker nginx

# 2. Получение свежего бэкапа
log "[2/7] Получение архива бэкапа из источника: ${SOURCE}..."
mkdir -p "${BACKUP_TMP}"
if [[ "$SOURCE" == "nl" ]]; then
    log "Поиск последнего бэкапа на офсайт-ноде NL (${NL_HOST})..."
    LATEST=$(ssh -o ConnectTimeout=10 "${NL_HOST}" "ls -1t ${NL_DIR} | head -n 1")
    [[ -z "$LATEST" ]] && error "Не удалось найти бэкап на NL хосте."
    log "Скачивание бэкапа: ${LATEST}..."
    scp -r "${NL_HOST}:${NL_DIR}/${LATEST}/*" "${BACKUP_TMP}/"
elif [[ "$SOURCE" == "ru" ]]; then
    log "Поиск последнего бэкапа на RU ноде (${RU_HOST})..."
    LATEST=$(ssh -o ConnectTimeout=10 "${RU_HOST}" "ls -1t ${RU_DIR} | head -n 1")
    [[ -z "$LATEST" ]] && error "Не удалось найти бэкап на RU хосте."
    scp -r "${RU_HOST}:${RU_DIR}/${LATEST}/*" "${BACKUP_TMP}/"
elif [[ "$SOURCE" == "local" ]]; then
    LOCAL_PATH="${2:-/tmp/backup}"
    [[ ! -d "$LOCAL_PATH" ]] && error "Локальная папка бэкапа не найдена: $LOCAL_PATH"
    cp -r "${LOCAL_PATH}/"* "${BACKUP_TMP}/"
fi

# 3. Восстановление конфигураций и секретов
log "[3/7] Восстановление файлов сервиса и .env..."
mkdir -p "${TARGET_DIR}"
[[ -f "${BACKUP_TMP}/dot-env" ]] && cp "${BACKUP_TMP}/dot-env" "${TARGET_DIR}/.env"
[[ -f "${BACKUP_TMP}/docker-compose.yml" ]] && cp "${BACKUP_TMP}/docker-compose.yml" "${TARGET_DIR}/"

# Сертификаты Let's Encrypt
if [[ -f "${BACKUP_TMP}/letsencrypt.tar.gz" ]]; then
    tar -xzf "${BACKUP_TMP}/letsencrypt.tar.gz" -C /
    success "SSL-сертификаты восстановлены."
fi

# 4. Восстановление MySQL
log "[4/7] Развертывание MySQL и импорт базы данных..."
cd "${TARGET_DIR}"
docker compose up -d mysql
log "Ожидание готовности MySQL (15 сек)..."
sleep 15

MYSQL_PASS=$(grep -E '^MYSQL_PASSWORD=' .env | cut -d= -f2 | tr -d '"'\''')
MYSQL_USER=$(grep -E '^MYSQL_USER=' .env | cut -d= -f2 | tr -d '"'\''')
[[ -z "$MYSQL_USER" ]] && MYSQL_USER="alvik"

if [[ -f "${BACKUP_TMP}/mysql_vpn.sql.gz" ]]; then
    gunzip < "${BACKUP_TMP}/mysql_vpn.sql.gz" | docker exec -i vpn_mysql mysql -u"${MYSQL_USER}" -p"${MYSQL_PASS}" vpn
    success "База данных пользователей 'vpn' успешно импортирована."
fi

# 5. Установка и запуск 3x-ui
log "[5/7] Развертывание 3x-ui и подстановка базы клиентов..."
if ! command -v x-ui &> /dev/null; then
    log "Установка 3x-ui..."
    bash <(curl -Ls https://raw.githubusercontent.com/mhsanaei/3x-ui/master/install.sh) <<EOF
n
admin
$(openssl rand -hex 8)
2053
y
EOF
    systemctl stop x-ui
fi

if [[ -f "${BACKUP_TMP}/x-ui_de.db" ]]; then
    cp "${BACKUP_TMP}/x-ui_de.db" /etc/x-ui/x-ui.db
elif [[ -f "${BACKUP_TMP}/x-ui.db" ]]; then
    cp "${BACKUP_TMP}/x-ui.db" /etc/x-ui/x-ui.db
fi
chown root:root /etc/x-ui/x-ui.db
chmod 660 /etc/x-ui/x-ui.db
systemctl restart x-ui
success "3x-ui запущен с оригинальной базой клиентов."

# 6. Сеть, AmneziaWG L3-мост и Nginx
log "[6/7] Настройка сети, AmneziaWG и Nginx..."
if [[ -f "${BACKUP_TMP}/network-security.tar.gz" ]]; then
    tar -xzf "${BACKUP_TMP}/network-security.tar.gz" -C /
    systemctl enable --now awg-quick@awg-ru-bridge 2>/dev/null || warn "AWG модуль ядра требует ручной компиляции."
fi

if [[ -f "${BACKUP_TMP}/nginx.tar.gz" ]]; then
    tar -xzf "${BACKUP_TMP}/nginx.tar.gz" -C /
    nginx -t && systemctl reload nginx
    success "Конфигурация Nginx восстановлена."
fi

# 7. Python виртуальное окружение и сервисы
log "[7/7] Подготовка Python окружения и запуск служб..."
python3 -m venv "${TARGET_DIR}/venv"
"${TARGET_DIR}/venv/bin/pip" install --quiet -r "${TARGET_DIR}/requirements.txt" || true

mkdir -p "${TARGET_DIR}/bot_xui"
python3 -m venv "${TARGET_DIR}/bot_xui/venv"
"${TARGET_DIR}/bot_xui/venv/bin/pip" install --quiet -r "${TARGET_DIR}/requirements.txt" || true

if [[ -f "${BACKUP_TMP}/systemd-services.tar.gz" ]]; then
    tar -xzf "${BACKUP_TMP}/systemd-services.tar.gz" -C /
    systemctl daemon-reload
    systemctl enable --now api bot admin-panel ru-proxy 2>/dev/null || true
fi

success "==========================================================="
success " Восстановление завершено!"
success " IP нового сервера: ${NEW_IP}"
success " Чеклист действий:"
echo -e " 1. В панели Cloudflare смените IP для ${CYAN}de-gateway.tiinservice.online${NC} на: ${GREEN}${NEW_IP}${NC}"
echo -e " 2. На российском сервере Selectel обновите Endpoint моста при необходимости:"
echo -e "    ${YELLOW}ssh ru-server \"sed -i 's/Endpoint = .*/Endpoint = ${NEW_IP}:51850/' /etc/amnezia/amneziawg/awg-ru-bridge.conf && systemctl restart awg-quick@awg-ru-bridge\"${NC}"
echo -e " 3. Проверьте запуск: ${CYAN}systemctl status api bot x-ui${NC}"
success "==========================================================="
