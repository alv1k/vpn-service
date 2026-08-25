#!/bin/bash
# YouTube NL Proxy Failover
# Checks if the Netherlands VLESS proxy (2.26.76.210:15687) is reachable.
# If down, switches YouTube routing to direct. Restores when it comes back.
# Runs every 5 minutes via cron.
#
# Fixes:
#   - flock to prevent parallel runs
#   - sudo for config write and xray reload
#   - hysteresis: 2 consecutive failures before switching to direct,
#                 2 consecutive successes before switching back to NL

set -uo pipefail

CONFIG="/usr/local/x-ui/bin/config.json"
STATE_FILE="/home/alvik/vpn-service/data/yt_nl_proxy_state"
LOG_FILE="/home/alvik/vpn-service/logs/yt-failover.log"
ENV_FILE="/home/alvik/vpn-service/.env"
LOCK_FILE="/tmp/yt_nl_failover.lock"
NL_HOST="2.26.76.210"
NL_PORT="15687"
NL_UUID="a498b8a6-4a13-4f00-8c53-a895320cfc6e"
NL_PUBKEY="Yhi1GGSsXkbS0BpLHnQ8APwKB_QUb4ARYpHdUMuSMmA"
NL_SNI="www.amazon.com"
NL_SHORTID="f0cb56672673"

FAIL_THRESHOLD=2
RECOVER_THRESHOLD=2

mkdir -p "$(dirname "$LOG_FILE")"
mkdir -p "$(dirname "$STATE_FILE")"

source <(grep -E '^(TELEGRAM_BOT_TOKEN|ADMIN_TG_ID)=' "$ENV_FILE" 2>/dev/null || true)
BOT_TOKEN="${TELEGRAM_BOT_TOKEN:-}"
ADMIN_CHAT_ID="${ADMIN_TG_ID:-364224373}"

exec 9>"$LOCK_FILE"
if ! flock -n 9; then
    exit 0
fi

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" >> "$LOG_FILE"
}

notify() {
    if [ -n "$BOT_TOKEN" ]; then
        curl -s -X POST "https://api.telegram.org/bot${BOT_TOKEN}/sendMessage" \
            -H "Content-Type: application/json" \
            -d "{\"chat_id\": ${ADMIN_CHAT_ID}, \"parse_mode\": \"HTML\", \"text\": \"$1\"}" > /dev/null 2>&1 || true
    fi
}

read_state() {
    if [ -f "$STATE_FILE" ]; then
        cat "$STATE_FILE"
    else
        echo "up:0:0"
    fi
}

write_state() {
    echo "$1" > "$STATE_FILE"
}

get_state_field() {
    local state="$1"
    local field="$2"
    echo "$state" | cut -d: -f"$field"
}

check_nl_proxy() {
    local tmpdir
    tmpdir=$(mktemp -d)
    local client_config="${tmpdir}/check.json"

    cat > "$client_config" <<EOF
{
  "inbounds": [{"listen": "127.0.0.1", "port": 19999, "protocol": "socks", "settings": {"udp": false}}],
  "outbounds": [
    {
      "protocol": "vless",
      "settings": {
        "address": "${NL_HOST}",
        "port": ${NL_PORT},
        "id": "${NL_UUID}",
        "flow": "xtls-rprx-vision",
        "encryption": "none"
      },
      "tag": "nl-check",
      "streamSettings": {
        "network": "tcp",
        "security": "reality",
        "realitySettings": {
          "publicKey": "${NL_PUBKEY}",
          "fingerprint": "chrome",
          "serverName": "${NL_SNI}",
          "shortId": "${NL_SHORTID}"
        }
      }
    }
  ]
}
EOF

    local xray_bin
    xray_bin=$(find /usr/local/x-ui/bin/ -name 'xray-linux-*' -type f 2>/dev/null | head -1)
    if [ -z "$xray_bin" ]; then
        log "ERROR: xray binary not found"
        rm -rf "$tmpdir"
        return 1
    fi

    "$xray_bin" run -c "$client_config" > /dev/null 2>&1 &
    local xray_pid=$!
    sleep 3

    local result=1
    if curl -s --socks5 127.0.0.1:19999 --connect-timeout 8 --max-time 15 -o /dev/null -w "%{http_code}" https://www.youtube.com 2>/dev/null | grep -qE '^[23]'; then
        result=0
    fi

    kill "$xray_pid" 2>/dev/null || true
    wait "$xray_pid" 2>/dev/null || true
    rm -rf "$tmpdir"
    return $result
}

switch_youtube() {
    local new_tag="$1"
    local result
    result=$(sudo python3 -c "
import json
with open('$CONFIG', 'r') as f:
    cfg = json.load(f)
changed = False
for rule in cfg['routing']['rules']:
    if any(d in rule.get('domain', []) for d in ['youtube.com', 'googlevideo.com', 'geosite:youtube']):
        if rule.get('outboundTag') != '$new_tag':
            rule['outboundTag'] = '$new_tag'
            changed = True
        break
if changed:
    with open('$CONFIG', 'w') as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
    print('CHANGED')
else:
    print('NO_CHANGE')
" 2>&1)
    echo "$result"
}

reload_xray() {
    local xray_pid
    xray_pid=$(pgrep -f "xray-linux-amd64" | head -1)
    if [ -n "$xray_pid" ]; then
        sudo kill -USR1 "$xray_pid" 2>/dev/null
        sleep 2
        return 0
    fi
    return 1
}

CURRENT_STATE=$(read_state)
STATE=$(get_state_field "$CURRENT_STATE" 1)
DOWN_COUNT=$(get_state_field "$CURRENT_STATE" 2)
UP_COUNT=$(get_state_field "$CURRENT_STATE" 3)

DOWN_COUNT=${DOWN_COUNT:-0}
UP_COUNT=${UP_COUNT:-0}

if check_nl_proxy; then
    UP_COUNT=$((UP_COUNT + 1))
    DOWN_COUNT=0

    if [ "$STATE" = "up" ]; then
        write_state "up:0:$UP_COUNT"
    else
        if [ "$UP_COUNT" -ge "$RECOVER_THRESHOLD" ]; then
            write_state "up:0:0"
            switch_youtube "de-to-nl-youtube"
            reload_xray
            notify "✅ <b>NL Proxy Restored</b>\nYouTube routing switched back to Netherlands proxy."
            log "NL proxy is back UP — restored routing (after ${UP_COUNT} consecutive successes)"
        else
            write_state "down:${DOWN_COUNT}:${UP_COUNT}"
            log "NL proxy check OK (${UP_COUNT}/${RECOVER_THRESHOLD} to recover)"
        fi
    fi
else
    DOWN_COUNT=$((DOWN_COUNT + 1))
    UP_COUNT=0

    if [ "$STATE" = "down" ]; then
        write_state "down:${DOWN_COUNT}:0"
    else
        if [ "$DOWN_COUNT" -ge "$FAIL_THRESHOLD" ]; then
            write_state "down:0:0"
            switch_youtube "direct"
            reload_xray
            notify "⚠️ <b>NL Proxy DOWN</b>\nYouTube traffic now goes DIRECT (no proxy).\nWill auto-restore when proxy is back."
            log "NL proxy is DOWN — switched YouTube to direct (after ${DOWN_COUNT} consecutive failures)"
        else
            write_state "up:${DOWN_COUNT}:${UP_COUNT}"
            log "NL proxy check FAILED (${DOWN_COUNT}/${FAIL_THRESHOLD} to failover)"
        fi
    fi
fi
