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


# Test 9: x-ui container status
# if docker ps --format '{{.Names}} {{.Status}}' | grep -q "^x-ui Up"; then
#     log "✅ x-ui container running"
#     RESULTS+="✅ x-ui container: UP\n"
# else
#     try_restart "x-ui" "sudo docker restart x-ui" "docker ps --format '{{.Names}} {{.Status}}' | grep -q '^x-ui Up'"
#     if [ "$TRY_RESTART_OK" -eq 1 ]; then
#         RESULTS+="✅ x-ui container: UP [auto-recovered]\n"
#         RECOVERED+="x-ui "
#     else
#         log "❌ x-ui container DOWN"
#         RESULTS+="❌ x-ui container: DOWN [auto-restart failed]\n"
#         FAILED=1
#     fi
# fi

# Test 9: x-ui service status (systemd)
log "Test 9: x-ui service status"
if systemctl is-active --quiet x-ui; then
    XUI_PID=$(pgrep -f "/usr/local/x-ui/x-ui" | head -1)
    log "✅ x-ui service running (PID: ${XUI_PID:-unknown})"
    RESULTS+="✅ x-ui service: UP (PID: ${XUI_PID:-unknown})\n"
else
    try_restart "x-ui" "sudo systemctl restart x-ui" "systemctl is-active --quiet x-ui"
    if [ "$TRY_RESTART_OK" -eq 1 ]; then
        RESULTS+="✅ x-ui service: UP [auto-recovered]\n"
        RECOVERED+="x-ui "
    else
        log "❌ x-ui service DOWN"
        RESULTS+="❌ x-ui service: DOWN [auto-restart failed]\n"
        FAILED=1
    fi
fi

# Test 9a: Xray process status (check if running)
log "Test 9a: Xray process status"
if pgrep -f "bin/xray-linux-amd64" > /dev/null; then
    XRAY_PID=$(pgrep -f "bin/xray-linux-amd64" | head -1)
    log "✅ Xray process running (PID: $XRAY_PID)"
    RESULTS+="✅ Xray process: UP (PID: $XRAY_PID)\n"
else
    # Check if x-ui is running but xray not started
    if systemctl is-active --quiet x-ui; then
        log "⚠️ Xray process not found but x-ui is running"
        log "   Xray may be starting or using different binary name"
        RESULTS+="⚠️ Xray process: NOT FOUND (check x-ui status)\n"
    else
        log "⚠️ Xray process not found (x-ui service is down)"
        RESULTS+="⚠️ Xray process: NOT FOUND (x-ui service down)\n"
    fi
fi

# Test 10: Admin Panel API on port 51821
AWG_CODE=$(curl -s -o /dev/null -w "%{http_code}" --connect-timeout 5 http://127.0.0.1:51821/ 2>/dev/null)
if [ "$AWG_CODE" -ge 200 ] && [ "$AWG_CODE" -lt 500 ]; then
    log "✅ Admin Panel API on 51821: HTTP $AWG_CODE"
    RESULTS+="✅ Admin Panel API 51821: OK\n"
else
    try_restart "Admin Panel API" "sudo systemctl restart admin-panel" "curl -s -o /dev/null -w '%{http_code}' --connect-timeout 5 http://127.0.0.1:51821/ 2>/dev/null | grep -qE '^[2-4]'"
    if [ "$TRY_RESTART_OK" -eq 1 ]; then
        RESULTS+="✅ Admin Panel API 51821: OK [auto-recovered]\n"
        RECOVERED+="Admin-Panel "
    else
        log "❌ Admin Panel API on 51821: DOWN"
        RESULTS+="❌ Admin Panel API 51821: DOWN\n"
        FAILED=1
    fi
fi

# Test 10a: MTProto proxy (mtg) on port 3129
MTG_REPLAY_STATE="/tmp/mtg_replay_attacks_last"
if ss -tlnp | grep -q ':3129'; then
    MTG_METRICS=$(curl -s --connect-timeout 3 http://127.0.0.1:3129/metrics 2>/dev/null)
    if [ -n "$MTG_METRICS" ]; then
        MTG_CONN=$(echo "$MTG_METRICS" | grep "^mtg_client_connections{ip_family="ipv4"}" | awk '{print $2}')
        MTG_TG_CONN=$(echo "$MTG_METRICS" | grep "^mtg_telegram_connections{" | awk '{sum += $2} END {print sum}')
        MTG_REPLAY=$(echo "$MTG_METRICS" | grep "^mtg_replay_attacks " | awk '{print $2}')
        MTG_REPLAY=${MTG_REPLAY:-0}

        # Check replay attack rate (threshold: 100 per hour)
        CURRENT_TIME=$(date +%s)
        if [ -f "$MTG_REPLAY_STATE" ]; then
            read PREV_REPLAY PREV_TIME < "$MTG_REPLAY_STATE"
            TIME_DELTA=$((CURRENT_TIME - PREV_TIME))
            REPLAY_DELTA=$((MTG_REPLAY - PREV_REPLAY))
            if [ "$TIME_DELTA" -gt 0 ]; then
                REPLAY_PER_HOUR=$((REPLAY_DELTA * 3600 / TIME_DELTA))
                if [ "$REPLAY_PER_HOUR" -gt 100 ]; then
                    log "⚠️ MTProto replay attacks HIGH: ${REPLAY_DELTA} in ${TIME_DELTA}s (~${REPLAY_PER_HOUR}/h)"
                    RESULTS+="⚠️ MTProto replay: ${REPLAY_PER_HOUR}/h (clients: ${MTG_CONN:-0})\n"
                else
                    log "✅ MTProto proxy: client_conn=${MTG_CONN:-0}, tg_conn=${MTG_TG_CONN:-0}, replay_rate=${REPLAY_PER_HOUR}/h"
                    RESULTS+="✅ MTProto proxy: OK (clients: ${MTG_CONN:-0}, tg: ${MTG_TG_CONN:-0}, replay: ${REPLAY_PER_HOUR}/h)\n"
                fi
            else
                log "✅ MTProto proxy: client_conn=${MTG_CONN:-0}, tg_conn=${MTG_TG_CONN:-0}"
                RESULTS+="✅ MTProto proxy: OK (clients: ${MTG_CONN:-0}, tg: ${MTG_TG_CONN:-0})\n"
            fi
        else
            log "✅ MTProto proxy: client_conn=${MTG_CONN:-0}, tg_conn=${MTG_TG_CONN:-0}, replay=${MTG_REPLAY}"
            RESULTS+="✅ MTProto proxy: OK (clients: ${MTG_CONN:-0}, tg: ${MTG_TG_CONN:-0}, replay: ${MTG_REPLAY})\n"
        fi
        echo "$MTG_REPLAY $CURRENT_TIME" > "$MTG_REPLAY_STATE"
    else
        log "⚠️ MTProto proxy listening but metrics empty"
        RESULTS+="⚠️ MTProto proxy: metrics empty\n"
    fi
else
    try_restart "MTProto" "sudo systemctl restart mtg" "ss -tlnp | grep -q ':3129'"
    if [ "$TRY_RESTART_OK" -eq 1 ]; then
        RESULTS+="✅ MTProto proxy: OK [auto-recovered]\n"
        RECOVERED+="MTProto "
    else
        log "❌ MTProto proxy NOT listening on 3129"
        RESULTS+="❌ MTProto proxy: DOWN\n"
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

# Capacity & Resources Audit
RESULTS+="\n📊 <b>Server Capacity & Load:</b>\n"

# 1. CPU Load Average & Cores
CORES=$(nproc 2>/dev/null || echo 1)
LOAD_1M=$(awk '{print $1}' /proc/loadavg 2>/dev/null || echo "0")
LOAD_PCT=$(awk -v l="$LOAD_1M" -v c="$CORES" 'BEGIN {printf "%.0f", (l/c)*100}')
if [ "$LOAD_PCT" -ge 90 ]; then
    log "❌ High CPU Load: ${LOAD_1M} (${LOAD_PCT}% across ${CORES} cores)"
    RESULTS+="❌ CPU Load: ${LOAD_1M} / ${CORES} cores (${LOAD_PCT}%)\n"
    FAILED=1
elif [ "$LOAD_PCT" -ge 75 ]; then
    log "⚠️ Elevated CPU Load: ${LOAD_1M} (${LOAD_PCT}% across ${CORES} cores)"
    RESULTS+="⚠️ CPU Load: ${LOAD_1M} / ${CORES} cores (${LOAD_PCT}%)\n"
else
    log "✅ CPU Load: ${LOAD_1M} / ${CORES} cores"
    RESULTS+="✅ CPU Load: ${LOAD_1M} / ${CORES} cores\n"
fi

# 2. RAM Capacity
RAM_TOTAL_MB=$(free -m | awk '/Mem:/{print $2}')
RAM_AVAIL_MB=$(free -m | awk '/Mem:/{print $7}')
RAM_USED_MB=$((RAM_TOTAL_MB - RAM_AVAIL_MB))
RAM_PCT=$((RAM_USED_MB * 100 / RAM_TOTAL_MB))
RAM_AVAIL_HUMAN=$(awk -v m="$RAM_AVAIL_MB" 'BEGIN {if (m >= 1024) printf "%.1fG", m/1024; else printf "%dM", m}')
RAM_TOTAL_HUMAN=$(awk -v m="$RAM_TOTAL_MB" 'BEGIN {printf "%.1fG", m/1024}')
if [ "$RAM_PCT" -ge 90 ]; then
    log "❌ RAM Capacity CRITICAL: ${RAM_PCT}% used (${RAM_AVAIL_HUMAN} free)"
    RESULTS+="❌ RAM: ${RAM_PCT}% used (${RAM_AVAIL_HUMAN} free of ${RAM_TOTAL_HUMAN})\n"
    FAILED=1
elif [ "$RAM_PCT" -ge 80 ]; then
    log "⚠️ RAM Capacity HIGH: ${RAM_PCT}% used (${RAM_AVAIL_HUMAN} free)"
    RESULTS+="⚠️ RAM: ${RAM_PCT}% used (${RAM_AVAIL_HUMAN} free of ${RAM_TOTAL_HUMAN})\n"
else
    log "✅ RAM Capacity: ${RAM_PCT}% used (${RAM_AVAIL_HUMAN} free of ${RAM_TOTAL_HUMAN})"
    RESULTS+="✅ RAM: ${RAM_PCT}% used (${RAM_AVAIL_HUMAN} free of ${RAM_TOTAL_HUMAN})\n"
fi

# 3. Disk Capacity (Root filesystem)
DISK_USE_PCT=$(df / --output=pcent | tail -1 | tr -dc '0-9')
DISK_AVAIL_HUMAN=$(df -h / --output=avail | tail -1 | tr -d ' ')
DISK_TOTAL_HUMAN=$(df -h / --output=size | tail -1 | tr -d ' ')
if [ "$DISK_USE_PCT" -ge 90 ]; then
    log "❌ Disk CRITICAL: ${DISK_USE_PCT}% used (${DISK_AVAIL_HUMAN} free)"
    RESULTS+="❌ Disk: ${DISK_USE_PCT}% used (${DISK_AVAIL_HUMAN} free of ${DISK_TOTAL_HUMAN})\n"
    FAILED=1
elif [ "$DISK_USE_PCT" -ge 80 ]; then
    log "⚠️ Disk HIGH: ${DISK_USE_PCT}% used (${DISK_AVAIL_HUMAN} free)"
    RESULTS+="⚠️ Disk: ${DISK_USE_PCT}% used (${DISK_AVAIL_HUMAN} free of ${DISK_TOTAL_HUMAN})\n"
else
    log "✅ Disk: ${DISK_USE_PCT}% used (${DISK_AVAIL_HUMAN} free of ${DISK_TOTAL_HUMAN})"
    RESULTS+="✅ Disk: ${DISK_USE_PCT}% used (${DISK_AVAIL_HUMAN} free of ${DISK_TOTAL_HUMAN})\n"
fi

# 4. VPN Clients & Capacity (x-ui)
if [ -f "/etc/x-ui/x-ui.db" ]; then
    XUI_STATS=$(sqlite3 /etc/x-ui/x-ui.db "SELECT (SELECT COUNT(*) FROM client_traffics WHERE enable=1), (SELECT COUNT(*) FROM inbounds WHERE enable=1);" 2>/dev/null)
    if [ -n "$XUI_STATS" ]; then
        XUI_CLIENTS=$(echo "$XUI_STATS" | cut -d'|' -f1)
        XUI_INBOUNDS=$(echo "$XUI_STATS" | cut -d'|' -f2)
        log "✅ VPN Clients: ${XUI_CLIENTS} active on ${XUI_INBOUNDS} inbounds"
        RESULTS+="✅ VPN Clients: ${XUI_CLIENTS} active (${XUI_INBOUNDS} inbounds)\n"
    fi
fi

# 5. Nginx Active Connections
NGINX_STATUS=$(curl -s http://127.0.0.1:8881/nginx_status 2>/dev/null)
if [ -n "$NGINX_STATUS" ]; then
    NGINX_ACTIVE=$(echo "$NGINX_STATUS" | awk '/Active connections/{print $3}')
    log "✅ Nginx active connections: ${NGINX_ACTIVE}"
    RESULTS+="✅ Nginx conn: ${NGINX_ACTIVE} active\n"
else
    log "⚠️ Nginx stub_status unavailable"
    RESULTS+="⚠️ Nginx stub_status: unavailable\n"
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
    HOUR=$(date +%H)
    if [ "$HOUR" = "09" ]; then
        notify "✅ <b>VPN Health Check OK</b>\n\n${RESULTS}"
    fi
    log "✅ All checks passed"
fi

# Always log results
echo -e "$RESULTS"
