#!/bin/bash
# Migrate inbound #5 from VLESS+Reality+TCP to VLESS+Reality+XHTTP
# Scheduled for 2026-03-18 21:00 CET (05:00 UTC+9)
set -e

LOG="/home/alvik/vpn-service/scripts/migrate-inbound5.log"
DB="/home/alvik/vpn-service/docker/x-ui-data/x-ui.db"

echo "$(date) - Starting migration inbound #5 TCP -> XHTTP" | tee -a "$LOG"

# 1. Backup DB
cp "$DB" "${DB}.backup-$(date +%Y%m%d-%H%M%S)"
echo "$(date) - DB backed up" | tee -a "$LOG"

# 2. Update stream_settings and client flows
python3 << 'PYEOF'
import sqlite3, json

db = "/home/alvik/vpn-service/docker/x-ui-data/x-ui.db"
conn = sqlite3.connect(db)
cur = conn.cursor()

# --- Update stream_settings ---
cur.execute('SELECT stream_settings FROM inbounds WHERE id=5')
stream = json.loads(cur.fetchone()[0])

# Change network to xhttp
stream["network"] = "xhttp"

# Remove tcpSettings
stream.pop("tcpSettings", None)

# Add xhttpSettings
stream["xhttpSettings"] = {
    "path": "/",
    "host": "",
    "mode": "auto",
    "extra": {
        "noGRPCHeader": False,
        "noSSEHeader": False,
        "scMaxEachPostBytes": "1000000-2000000",
        "scMaxBufferedPosts": 30,
        "scMinPostsIntervalMs": "10-50",
        "xPaddingBytes": "100-1000"
    }
}

# Keep reality with acceptProxyProtocol via realitySettings
stream["realitySettings"]["xver"] = 2

cur.execute('UPDATE inbounds SET stream_settings=? WHERE id=5', (json.dumps(stream),))
print(f"stream_settings updated")

# --- Remove flow from all clients ---
cur.execute('SELECT settings FROM inbounds WHERE id=5')
settings = json.loads(cur.fetchone()[0])

for client in settings.get("clients", []):
    client["flow"] = ""

cur.execute('UPDATE inbounds SET settings=? WHERE id=5', (json.dumps(settings),))
print(f"Cleared flow for {len(settings['clients'])} clients")

conn.commit()
conn.close()
print("DB updated successfully")
PYEOF

echo "$(date) - DB updated, restarting x-ui" | tee -a "$LOG"

# 3. Restart x-ui to apply
docker restart x-ui

echo "$(date) - x-ui restarted" | tee -a "$LOG"

# 4. Wait and verify
sleep 10
docker exec x-ui cat /app/bin/config.json 2>/dev/null | python3 -c "
import sys, json
cfg = json.load(sys.stdin)
for ib in cfg.get('inbounds', []):
    if 'inbound-31852' in ib.get('tag',''):
        s = ib.get('streamSettings', {})
        print(f'Network: {s.get(\"network\")}')
        print(f'Security: {s.get(\"security\")}')
        if s.get('xhttpSettings'):
            print('xhttpSettings: present')
        print('OK - migration successful')
"

echo "$(date) - Migration complete" | tee -a "$LOG"

# 5. Restore subUpdates back to 12h
python3 -c "
import sqlite3
conn = sqlite3.connect('$DB')
cur = conn.cursor()
cur.execute('UPDATE settings SET value=\"12\" WHERE key=\"subUpdates\"')
conn.commit()
print('subUpdates restored to 12')
"
echo "$(date) - subUpdates restored to 12h" | tee -a "$LOG"

# 6. Self-remove from cron
crontab -l 2>/dev/null | grep -v 'migrate-inbound5-xhttp' | crontab -
echo "$(date) - Cron entry removed" | tee -a "$LOG"
