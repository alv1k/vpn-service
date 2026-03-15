#!/bin/bash
# Nightly test runner — sends results to Telegram
set -uo pipefail

cd /home/alvik/vpn-service

TG_BOT_TOKEN="8075947163:AAHrp6lZZP0SOSzNMl9VOEJgl4mOHwpNfv4"
TG_CHAT_ID="364224373"
TIMESTAMP=$(date +%Y-%m-%d_%H-%M-%S)

send_tg() {
    curl -s -X POST "https://api.telegram.org/bot${TG_BOT_TOKEN}/sendMessage" \
        -d chat_id="${TG_CHAT_ID}" \
        -d text="$1" \
        -d parse_mode="HTML" > /dev/null 2>&1
}

OUTPUT=$(python3 -m pytest tests/ -v 2>&1)
EXIT_CODE=$?

# Extract summary line (e.g. "62 passed in 0.61s")
SUMMARY=$(echo "$OUTPUT" | tail -1)

if [ $EXIT_CODE -eq 0 ]; then
    send_tg "<b>✅ Tests OK</b> — ${TIMESTAMP}
${SUMMARY}"
else
    FAILURES=$(echo "$OUTPUT" | grep "FAILED" | head -10)
    send_tg "<b>❌ Tests FAILED</b> — ${TIMESTAMP}
${SUMMARY}

<pre>${FAILURES}</pre>"
fi
