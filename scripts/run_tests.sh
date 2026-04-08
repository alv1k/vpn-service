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
    "tests/test_website.py|Website"
    "tests/test_notifications.py|Notifications"
    "tests/test_helpers.py|Helpers"
    "tests/test_utils.py|Utils"
    "tests/test_tariffs.py|Tariffs"
    "tests/test_awg_manager.py|AWG-Manager"
    "tests/test_softether_extended.py|SoftEther-Ext"
    "tests/test_payment.py|Payment"
    "tests/test_registration.py|Registration"
    "tests/test_web_portal.py|Web-Portal"
    "tests/test_web_referrals.py|Web-Referrals"
    "tests/test_session.py|Session"
    "tests/test_trial_activation.py|Trial-Activation"
    "tests/test_security.py|Security"
    "tests/test_autopay.py|Autopay"
    "tests/test_vpn_factory.py|VPN-Factory"
    "tests/test_refund.py|Refund"
    "tests/test_create_order.py|Create-Order"
    "tests/test_messaging.py|Messaging"
    "tests/test_views.py|Views"
    "tests/test_sharing_monitor.py|Sharing-Monitor"
    "tests/test_bot_handler.py|Bot-Handler"
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
