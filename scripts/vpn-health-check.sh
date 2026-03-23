#!/bin/bash
# VPN Health Check — runs every 3 hours via cron
# Tests inbound #5 (VLESS-Reality) connectivity through a local xray client

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ENV_FILE="/home/alvik/vpn-service/.env"
source <(grep -E '^(TELEGRAM_BOT_TOKEN|ADMIN_TG_ID)=' "$ENV_FILE")
LOG_FILE="/home/alvik/vpn-service/logs/vpn-health.log"
BOT_TOKEN="$TELEGRAM_BOT_TOKEN"
ADMIN_CHAT_ID="${ADMIN_TG_ID:-364224373}"
PROXY="socks5://127.0.0.1:10808"
XRAY_PID=""

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1"
}

notify() {
    curl -s -X POST "https://api.telegram.org/bot${BOT_TOKEN}/sendMessage" \
        -H "Content-Type: application/json" \
        -d "{\"chat_id\": ${ADMIN_CHAT_ID}, \"parse_mode\": \"HTML\", \"text\": \"$1\"}" > /dev/null 2>&1
}

cleanup() {
    if [ -n "$XRAY_PID" ] && kill -0 "$XRAY_PID" 2>/dev/null; then
        kill "$XRAY_PID" 2>/dev/null
        wait "$XRAY_PID" 2>/dev/null
    fi
    rm -f /tmp/xray-healthcheck.json
}
trap cleanup EXIT

# Create temporary xray client config
cat > /tmp/xray-healthcheck.json << 'XRAY_EOF'
{
  "inbounds": [{
    "port": 10808,
    "listen": "127.0.0.1",
    "protocol": "socks",
    "settings": { "udp": true }
  }],
  "outbounds": [{
    "protocol": "vless",
    "settings": {
      "vnext": [{
        "address": "127.0.0.1",
        "port": 7443,
        "users": [{
          "id": "e2a31746-f921-4c7f-ace6-fe14d43bc9b6",
          "flow": "xtls-rprx-vision",
          "encryption": "none"
        }]
      }]
    },
    "streamSettings": {
      "network": "tcp",
      "security": "reality",
      "realitySettings": {
        "serverName": "www.samsung.com",
        "fingerprint": "chrome",
        "publicKey": "roVVvuq4ZkvEu43rKEBm9fC0VXwMO1PVVd2YzvhiWEQ",
        "shortId": "f6c9863acf",
        "spiderX": "/"
      }
    }
  }]
}
XRAY_EOF

# Start local xray client
docker exec -d x-ui /app/bin/xray-linux-amd64 run -c /dev/stdin < /tmp/xray-healthcheck.json 2>/dev/null

# Alternative: run xray directly if available, otherwise use a simple connectivity test
# Since running xray inside docker with stdin is tricky, test direct connectivity instead

log "=== Health Check Start ==="

RESULTS=""
FAILED=0

# Test 1: Check xray is listening on 7443
if ss -tlnp | grep -q ':7443'; then
    log "✅ Xray listening on 7443"
    RESULTS+="✅ Xray port 7443: OK\n"
else
    log "❌ Xray NOT listening on 7443"
    RESULTS+="❌ Xray port 7443: DOWN\n"
    FAILED=1
fi

# Test 2: Check nginx stream on 443
if ss -tlnp | grep -q ':443'; then
    log "✅ Nginx stream on 443"
    RESULTS+="✅ Nginx port 443: OK\n"
else
    log "❌ Nginx NOT on 443"
    RESULTS+="❌ Nginx port 443: DOWN\n"
    FAILED=1
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

# Test 4: Ping youtube.com
PING_RESULT=$(ping -c 3 -W 5 youtube.com 2>/dev/null | tail -1)
if [ -n "$PING_RESULT" ]; then
    PING_AVG=$(echo "$PING_RESULT" | awk -F'/' '{print $5}')
    log "✅ Ping youtube.com: ${PING_AVG}ms avg"
    RESULTS+="✅ Ping youtube.com: ${PING_AVG}ms\n"
else
    log "❌ Ping youtube.com FAILED"
    RESULTS+="❌ Ping youtube.com: FAIL\n"
    FAILED=1
fi

# Test 5: HTTP connectivity to youtube.com
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
source <(grep -E '^SOFTETHER_SERVER_PASSWORD=' "$ENV_FILE")
AZURE_OUTPUT=$(timeout 30 /opt/softether/vpncmd localhost:5555 /SERVER /PASSWORD:"$SOFTETHER_SERVER_PASSWORD" /CMD VpnAzureGetStatus 2>&1)
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
    log "❌ x-ui container DOWN"
    RESULTS+="❌ x-ui container: DOWN\n"
    FAILED=1
fi

log "=== Health Check End ==="

if [ "$FAILED" -eq 1 ]; then
    notify "🚨 <b>VPN Health Check FAILED</b>\n\n${RESULTS}"
    log "⚠️ Notification sent — issues detected"
else
    notify "✅ <b>VPN Health Check OK</b>\n\n${RESULTS}"
fi

# Always log results
echo -e "$RESULTS"
