#!/bin/bash
# VPN Health Check — runs every 3 hours via cron
# Tests inbound #5 (VLESS-Reality) connectivity through a local xray client

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ENV_FILE="/home/alvik/vpn-service/.env"
source <(grep -E '^(TELEGRAM_BOT_TOKEN|ADMIN_TG_ID|MYSQL_USER|MYSQL_PASSWORD)=' "$ENV_FILE")
LOG_FILE="/home/alvik/vpn-service/logs/vpn-health.log"
BOT_TOKEN="$TELEGRAM_BOT_TOKEN"
ADMIN_CHAT_ID="${ADMIN_TG_ID:-364224373}"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1"
}

notify() {
    curl -s -X POST "https://api.telegram.org/bot${BOT_TOKEN}/sendMessage" \
        -H "Content-Type: application/json" \
        -d "{\"chat_id\": ${ADMIN_CHAT_ID}, \"parse_mode\": \"HTML\", \"text\": \"$1\"}" > /dev/null 2>&1
}

# Attempt to restart a service, wait 5s, run a recheck command.
# Usage: try_restart "service_label" "restart_cmd" "recheck_cmd"
# Sets TRY_RESTART_OK=1 if recovered, 0 if still failed.
try_restart() {
    local label="$1"
    local restart_cmd="$2"
    local recheck_cmd="$3"
    TRY_RESTART_OK=0
    log "🔄 Attempting restart: $restart_cmd"
    eval "$restart_cmd" >/dev/null 2>&1
    sleep 5
    if eval "$recheck_cmd" >/dev/null 2>&1; then
        TRY_RESTART_OK=1
        log "✅ $label auto-recovered after restart"
    else
        log "❌ $label still down after restart"
    fi
}



log "=== Health Check Start ==="

RESULTS=""
FAILED=0
RECOVERED=""

# Test 1: Check xray is listening on 7443
if ss -tlnp | grep -q ':7443'; then
    log "✅ Xray listening on 7443"
    RESULTS+="✅ Xray port 7443: OK\n"
else
    try_restart "Xray" "sudo docker restart x-ui" "ss -tlnp | grep -q ':7443'"
    if [ "$TRY_RESTART_OK" -eq 1 ]; then
        RESULTS+="✅ Xray port 7443: OK [auto-recovered]\n"
        RECOVERED+="Xray "
    else
        log "❌ Xray NOT listening on 7443"
        RESULTS+="❌ Xray port 7443: DOWN [auto-restart failed]\n"
        FAILED=1
    fi
fi

# Test 2: Check nginx stream on 443
if ss -tlnp | grep -q ':443'; then
    log "✅ Nginx stream on 443"
    RESULTS+="✅ Nginx port 443: OK\n"
else
    try_restart "Nginx" "sudo systemctl restart nginx" "ss -tlnp | grep -q ':443'"
    if [ "$TRY_RESTART_OK" -eq 1 ]; then
        RESULTS+="✅ Nginx port 443: OK [auto-recovered]\n"
        RECOVERED+="Nginx "
    else
        log "❌ Nginx NOT on 443"
        RESULTS+="❌ Nginx port 443: DOWN [auto-restart failed]\n"
        FAILED=1
    fi
fi

# Test 3: DNS resolution
DNS_START=$(date +%s%N)
if host youtube.com > /dev/null 2>&1; then
    DNS_TIME=$(( ($(date +%s%N) - DNS_START) / 1000000 ))
    log "✅ DNS resolve youtube.com: ${DNS_TIME}ms"
    RESULTS+="✅ DNS youtube.com: ${DNS_TIME}ms\n"
else
    log "❌ DNS resolve youtube.com FAILED"
    RESULTS+="❌ DNS youtube.com: FAIL\n"
    FAILED=1
fi

# Test 3a: DNS servers accessibility (used by xray and SoftEther)
for DNS_SERVER in 1.1.1.1 8.8.8.8; do
    DNS_START=$(date +%s%N)
    if host youtube.com "$DNS_SERVER" > /dev/null 2>&1; then
        DNS_TIME=$(( ($(date +%s%N) - DNS_START) / 1000000 ))
        log "✅ DNS $DNS_SERVER: ${DNS_TIME}ms"
        RESULTS+="✅ DNS $DNS_SERVER: ${DNS_TIME}ms\n"
    else
        log "❌ DNS $DNS_SERVER: UNREACHABLE"
        RESULTS+="❌ DNS $DNS_SERVER: UNREACHABLE\n"
        FAILED=1
    fi
done

# Test 4: HTTP connectivity to youtube.com
HTTP_START=$(date +%s%N)
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" --connect-timeout 10 --max-time 15 https://www.youtube.com 2>/dev/null)
HTTP_TIME=$(( ($(date +%s%N) - HTTP_START) / 1000000 ))
if [ "$HTTP_CODE" -ge 200 ] && [ "$HTTP_CODE" -lt 400 ]; then
    log "✅ HTTP youtube.com: ${HTTP_CODE} (${HTTP_TIME}ms)"
    RESULTS+="✅ HTTP youtube.com: ${HTTP_CODE} (${HTTP_TIME}ms)\n"
else
    log "❌ HTTP youtube.com: ${HTTP_CODE} (${HTTP_TIME}ms)"
    RESULTS+="❌ HTTP youtube.com: ${HTTP_CODE}\n"
    FAILED=1
fi

# Test 6: Download speed (small file)
SPEED=$(curl -s -o /dev/null -w "%{speed_download}" --connect-timeout 10 --max-time 20 https://speed.cloudflare.com/__down?bytes=1048576 2>/dev/null)
SPEED_MBPS=$(echo "$SPEED" | awk '{printf "%.2f", $1/1048576*8}')
log "📊 Download speed: ${SPEED_MBPS} Mbps"
RESULTS+="📊 Speed: ${SPEED_MBPS} Mbps\n"

# Test 7: SoftEther VPN server on port 5555
if ss -tlnp | grep -q ':5555'; then
    log "✅ SoftEther listening on 5555"
    RESULTS+="✅ SoftEther port 5555: OK\n"
else
    log "❌ SoftEther NOT listening on 5555"
    RESULTS+="❌ SoftEther port 5555: DOWN\n"
    FAILED=1
fi

# Test 8: SoftEther VPN Azure relay
# SoftEther: vpncmd requires /PASSWORD in args (no stdin option).
# Minimize exposure: read from .env only when needed, unset after.
source <(grep -E '^SOFTETHER_SERVER_PASSWORD=' "$ENV_FILE")
AZURE_OUTPUT=$(timeout 30 /opt/softether/vpncmd localhost:5555 /SERVER /PASSWORD:"$SOFTETHER_SERVER_PASSWORD" /CMD VpnAzureGetStatus 2>&1)
unset SOFTETHER_SERVER_PASSWORD
if echo "$AZURE_OUTPUT" | grep -q "Connection to VPN Azure Cloud Server is Established|Yes"; then
    AZURE_HOST=$(echo "$AZURE_OUTPUT" | grep "Hostname.*VPN Azure" | awk -F'|' '{print $2}' | xargs)
    log "✅ VPN Azure connected: $AZURE_HOST"
    RESULTS+="✅ VPN Azure: $AZURE_HOST\n"
else
    log "❌ VPN Azure NOT connected"
    RESULTS+="❌ VPN Azure: DOWN\n"
    FAILED=1
fi

# Test 9: x-ui container status
if docker ps --format '{{.Names}} {{.Status}}' | grep -q "^x-ui Up"; then
    log "✅ x-ui container running"
    RESULTS+="✅ x-ui container: UP\n"
else
    try_restart "x-ui" "sudo docker restart x-ui" "docker ps --format '{{.Names}} {{.Status}}' | grep -q '^x-ui Up'"
    if [ "$TRY_RESTART_OK" -eq 1 ]; then
        RESULTS+="✅ x-ui container: UP [auto-recovered]\n"
        RECOVERED+="x-ui "
    else
        log "❌ x-ui container DOWN"
        RESULTS+="❌ x-ui container: DOWN [auto-restart failed]\n"
        FAILED=1
    fi
fi

# Test 10: AmneziaWG API on port 51821
AWG_CODE=$(curl -s -o /dev/null -w "%{http_code}" --connect-timeout 5 http://127.0.0.1:51821/ 2>/dev/null)
if [ "$AWG_CODE" -ge 200 ] && [ "$AWG_CODE" -lt 500 ]; then
    log "✅ AWG API on 51821: HTTP $AWG_CODE"
    RESULTS+="✅ AWG API 51821: OK\n"
else
    try_restart "AWG API" "sudo systemctl restart awg-api" "curl -s -o /dev/null -w '%{http_code}' --connect-timeout 5 http://127.0.0.1:51821/ 2>/dev/null | grep -qE '^[2-4]'"
    if [ "$TRY_RESTART_OK" -eq 1 ]; then
        RESULTS+="✅ AWG API 51821: OK [auto-recovered]\n"
        RECOVERED+="AWG-API "
    else
        log "❌ AWG API on 51821: DOWN"
        RESULTS+="❌ AWG API 51821: DOWN\n"
        FAILED=1
    fi
fi

# Test 11: Telegram Bot service
if systemctl is-active --quiet bot.service; then
    log "✅ Bot service running"
    RESULTS+="✅ Bot service: OK\n"
else
    try_restart "Bot" "sudo systemctl restart bot.service" "systemctl is-active --quiet bot.service"
    if [ "$TRY_RESTART_OK" -eq 1 ]; then
        RESULTS+="✅ Bot service: OK [auto-recovered]\n"
        RECOVERED+="Bot "
    else
        log "❌ Bot service DOWN"
        RESULTS+="❌ Bot service: DOWN\n"
        FAILED=1
    fi
fi

# Test 12: FastAPI service (webhook + web portal)
API_CODE=$(curl -s -o /dev/null -w "%{http_code}" --connect-timeout 5 http://127.0.0.1:8000/docs 2>/dev/null)
if [ "$API_CODE" -ge 200 ] && [ "$API_CODE" -lt 500 ]; then
    log "✅ API service on 8000: HTTP $API_CODE"
    RESULTS+="✅ API service 8000: OK\n"
else
    try_restart "API" "sudo systemctl restart api.service" "curl -s -o /dev/null -w '%{http_code}' --connect-timeout 5 http://127.0.0.1:8000/docs 2>/dev/null | grep -qE '^[2-4]'"
    if [ "$TRY_RESTART_OK" -eq 1 ]; then
        RESULTS+="✅ API service 8000: OK [auto-recovered]\n"
        RECOVERED+="API "
    else
        log "❌ API service on 8000: DOWN"
        RESULTS+="❌ API service 8000: DOWN\n"
        FAILED=1
    fi
fi

# Test 13: MySQL
MYSQL_PING="docker exec -e MYSQL_PWD=${MYSQL_PASSWORD} vpn_mysql mysqladmin ping -h 127.0.0.1 -u${MYSQL_USER} --silent"
if $MYSQL_PING 2>/dev/null | grep -q "alive"; then
    log "✅ MySQL alive"
    RESULTS+="✅ MySQL: OK\n"
else
    try_restart "MySQL" "docker restart vpn_mysql" "$MYSQL_PING 2>/dev/null | grep -q 'alive'"
    if [ "$TRY_RESTART_OK" -eq 1 ]; then
        RESULTS+="✅ MySQL: OK [auto-recovered]\n"
        RECOVERED+="MySQL "
    else
        log "❌ MySQL DOWN"
        RESULTS+="❌ MySQL: DOWN\n"
        FAILED=1
    fi
fi

log "=== Health Check End ==="

if [ "$FAILED" -eq 1 ] && [ -n "$RECOVERED" ]; then
    notify "🚨 <b>VPN Health Check FAILED</b>\n⚠️ Auto-recovered: ${RECOVERED}\n\n${RESULTS}"
    log "⚠️ Notification sent — issues detected (some auto-recovered)"
elif [ "$FAILED" -eq 1 ]; then
    notify "🚨 <b>VPN Health Check FAILED</b>\n\n${RESULTS}"
    log "⚠️ Notification sent — issues detected"
elif [ -n "$RECOVERED" ]; then
    notify "⚠️ <b>VPN Health Check OK</b> [auto-recovered]\n\n${RESULTS}"
    log "✅ All OK after auto-recovery"
else
    notify "✅ <b>VPN Health Check OK</b>\n\n${RESULTS}"
fi

# Always log results
echo -e "$RESULTS"
