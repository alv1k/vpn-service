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

TEST_SUITES=(
    "tests/test_db.py|DB"
    "tests/test_payments.py|Payments"
    "tests/test_promocodes.py|Promocodes"
    "tests/test_referrals.py|Referrals"
    "tests/test_subscriptions.py|Subscriptions"
    "tests/test_webhook.py|Webhook"
    "tests/test_awg.py|AWG"
    "tests/test_softether.py|SoftEther"
)

ALL_OK=1
REPORT=""

for entry in "${TEST_SUITES[@]}"; do
    FILE="${entry%%|*}"
    LABEL="${entry##*|}"

    OUTPUT=$(python3 -m pytest "$FILE" -v 2>&1)
    RC=$?
    SUMMARY=$(echo "$OUTPUT" | tail -1)

    if [ $RC -eq 0 ]; then
        REPORT+="✅ <b>${LABEL}</b>: ${SUMMARY}
"
    else
        ALL_OK=0
        FAILURES=$(echo "$OUTPUT" | grep "FAILED" | head -5)
        REPORT+="❌ <b>${LABEL}</b>: ${SUMMARY}
<pre>${FAILURES}</pre>
"
    fi
done

if [ $ALL_OK -eq 1 ]; then
    HEADER="✅ All tests OK"
else
    HEADER="❌ Some tests FAILED"
fi

send_tg "<b>${HEADER}</b> — ${TIMESTAMP}

${REPORT}"
