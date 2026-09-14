#!/bin/bash
# Nightly test runner — sends results to Telegram
set -uo pipefail

cd /home/alvik/vpn-service

source <(grep -E '^(TELEGRAM_BOT_TOKEN|ADMIN_TG_ID)=' .env)
TG_BOT_TOKEN="$TELEGRAM_BOT_TOKEN"
TG_CHAT_ID="${ADMIN_TG_ID:-364224373}"
TIMESTAMP=$(date +%Y-%m-%d_%H-%M-%S)

send_tg() {
    curl -s -X POST "https://api.telegram.org/bot${TG_BOT_TOKEN}/sendMessage" \
        -d chat_id="${TG_CHAT_ID}" \
        -d text="$1" \
        -d parse_mode="HTML" > /dev/null 2>&1
}

# 1. Run all tests in a single process using venv pytest
START_TIME=$(date +%s)
OUTPUT=$(venv/bin/pytest tests/ -v --tb=short 2>&1)
RC=$?
END_TIME=$(date +%s)
DURATION=$((END_TIME - START_TIME))

# Extract summary line (e.g., "485 passed, 15 warnings in 4.78s" or "1 error in 0.88s")
SUMMARY=$(echo "$OUTPUT" | grep -E "^=.*(passed|failed|error)" | tail -1 | sed 's/=/ /g' | xargs)
[ -z "$SUMMARY" ] && SUMMARY="Exit code $RC"

if [ $RC -eq 0 ]; then
    HEADER="✅ <b>All tests OK</b> (${DURATION}s)"
    MSG="<b>${HEADER}</b> — ${TIMESTAMP}
📊 <code>${SUMMARY}</code>"
else
    HEADER="❌ <b>Tests FAILED</b> (${DURATION}s)"
    FAILURES=$(echo "$OUTPUT" | grep -E "(FAILED|ERROR) tests/" | head -10)
    [ -z "$FAILURES" ] && FAILURES=$(echo "$OUTPUT" | tail -10)
    MSG="<b>${HEADER}</b> — ${TIMESTAMP}
📊 <code>${SUMMARY}</code>

<b>Failures:</b>
<pre>${FAILURES}</pre>"
fi

send_tg "${MSG}"

