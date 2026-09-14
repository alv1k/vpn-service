#!/bin/bash
# RU Bridge Failover (Selectel -> Hetzner Germany)
# Checks if the primary VLESS XHTTP Ultra bridge (Inbound 14, 344988.snk.wtf:38745) is healthy.
# If down, automatically switches traffic to olcRTC WebRTC tunnel (127.0.0.1:10808).
# Restores XHTTP Ultra when it comes back.

set -uo pipefail

DB_FILE="/etc/x-ui/x-ui.db"
STATE_FILE="/root/data/ru_bridge_state"
LOG_FILE="/root/logs/ru-bridge-failover.log"
LOCK_FILE="/tmp/ru_bridge_failover.lock"

BOT_TOKEN="8075947163:AAEQ5A4rmMLRXjynOiNH3lXWQV-EHwCkdn8"
ADMIN_CHAT_ID="364224373"

DE_HOST="344988.snk.wtf"
DE_PORT="38745"
DE_UUID="b86aa203-4628-4495-a588-72a173f76caa"
DE_PUBKEY="9wRNvGnNzqoXnWJ5ilKFMKzDCbKc-NjD7krMBBVaHy4"
DE_SNI="www.microsoft.com"
DE_SHORTID="585a38bfee5c"
DE_PATH="/api/v1/updates"

FAIL_THRESHOLD=2
RECOVER_THRESHOLD=2

mkdir -p "$(dirname "$LOG_FILE")"
mkdir -p "$(dirname "$STATE_FILE")"

exec 9>"$LOCK_FILE"
if ! flock -n 9; then
    exit 0
fi

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" >> "$LOG_FILE"
}

notify() {
    local text="$1"
    if [ -n "$BOT_TOKEN" ]; then
        curl -s -X POST "https://api.telegram.org/bot${BOT_TOKEN}/sendMessage"             -H "Content-Type: application/json"             -d "{"chat_id": ${ADMIN_CHAT_ID}, "parse_mode": "HTML", "text": "$text"}" > /dev/null 2>&1 || true
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

check_xhttp_bridge() {
    local tmpdir
    tmpdir=$(mktemp -d)
    local client_config="${tmpdir}/check.json"

    cat > "$client_config" <<CLIENT_EOF
{
  "inbounds": [{"listen": "127.0.0.1", "port": 19998, "protocol": "socks", "settings": {"udp": true}}],
  "outbounds": [
    {
      "protocol": "vless",
      "settings": {
        "vnext": [{
          "address": "",
          "port": ,
          "users": [{
            "id": "",
            "encryption": "none",
            "flow": ""
          }]
        }]
      },
      "tag": "xhttp-check",
      "streamSettings": {
        "network": "xhttp",
        "security": "reality",
        "realitySettings": {
          "serverName": "",
          "fingerprint": "firefox",
          "show": false,
          "publicKey": "",
          "shortId": "",
          "spiderX": "/"
        },
        "xhttpSettings": {
          "path": "",
          "host": "",
          "mode": "stream-one"
        }
      }
    }
  ]
}
CLIENT_EOF

    local xray_bin
    xray_bin=$(find /usr/local/x-ui/bin/ -name 'xray-linux-*' -type f 2>/dev/null | head -1)
    if [ -z "$xray_bin" ]; then
        log "ERROR: xray binary not found"
        rm -rf "$tmpdir"
        return 1
    fi

    "$xray_bin" run -c "$client_config" > /dev/null 2>&1 &
    local xray_pid=$!
    sleep 2

    local result=1
    if timeout 6 curl -s --socks5-hostname 127.0.0.1:19998 --connect-timeout 4 --max-time 5 -o /dev/null -w "%{http_code}" https://www.google.com 2>/dev/null | grep -qE '^[23]'; then
        result=0
    fi

    kill -9 "$xray_pid" 2>/dev/null || true
    wait "$xray_pid" 2>/dev/null || true
    rm -rf "$tmpdir"
    return $result
}

switch_outbound_routing() {
    local target_tag="$1"
    python3 -c "
import sqlite3, json
conn = sqlite3.connect('$DB_FILE')
c = conn.cursor()
cfg = json.loads(c.execute('SELECT value FROM settings WHERE key=\"xrayTemplateConfig\"').fetchone()[0])
changed = False
for rule in cfg['routing']['rules']:
    if rule.get('network') in ('tcp,udp', 'tcp') and 'inboundTag' not in rule and 'port' not in rule and 'domain' not in rule and 'ip' not in rule:
        if rule.get('outboundTag') != '$target_tag':
            rule['outboundTag'] = '$target_tag'
            changed = True
if changed:
    c.execute('UPDATE settings SET value=? WHERE key=\"xrayTemplateConfig\"', (json.dumps(cfg),))
    conn.commit()
    print('CHANGED')
else:
    print('NOOP')
"
}

main() {
    local current_state
    current_state=$(read_state)
    local status
    status=$(get_state_field "$current_state" 1)
    local fail_count
    fail_count=$(get_state_field "$current_state" 2)
    local recover_count
    recover_count=$(get_state_field "$current_state" 3)

    if check_xhttp_bridge; then
        recover_count=$((recover_count + 1))
        fail_count=0

        if [ "$status" = "down" ] && [ "$recover_count" -ge "$RECOVER_THRESHOLD" ]; then
            log "RECOVERED: XHTTP Ultra bridge is healthy again (consecutive successes: $recover_count)"
            local res
            res=$(switch_outbound_routing "xhttp-germany")
            if [ "$res" = "CHANGED" ]; then
                systemctl restart x-ui
                log "Switched global routing back to xhttp-germany"
                notify '🟢 <b>RU-Bridge: Основной канал восстановлен</b>

✅ <b>Inbound 14 (VLESS XHTTP Ultra)</b> снова в строю.
🚀 Трафик переведен в гигабитный режим. Резерв <code>olcRTC</code> переведен в режим ожидания.'
            fi
            status="up"
            recover_count=0
        fi
    else
        fail_count=$((fail_count + 1))
        recover_count=0

        if [ "$status" = "up" ] && [ "$fail_count" -ge "$FAIL_THRESHOLD" ]; then
            log "FAILOVER: XHTTP Ultra bridge failed (consecutive failures: $fail_count). Switching to olcRTC..."
            local res
            res=$(switch_outbound_routing "olcrtc-germany")
            if [ "$res" = "CHANGED" ]; then
                systemctl restart x-ui
                log "Switched global routing to olcrtc-germany"
                notify '🚨 <b>RU-Bridge: Сработал аварийный Failover!</b>

❌ <b>Основной мост (Inbound 14 XHTTP Ultra)</b> временно недоступен.
🔄 <b>Действие:</b> Трафик автоматически переведен на резервный WebRTC-видеомост <code>olcRTC</code>.
👤 Пользователи продолжают работать без обрыва соединения.'
            fi
            status="down"
            fail_count=0
        fi
    fi

    write_state "${status}:${fail_count}:${recover_count}"
}

main
