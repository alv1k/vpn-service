#!/bin/bash
set -u

REMOTE_HOST="alvik@2.26.76.210"
SSH_KEY="/home/alvik/.ssh/id_ed25519"
SSH_OPTS="-o ConnectTimeout=3 -o BatchMode=yes -o StrictHostKeyChecking=accept-new -i ${SSH_KEY}"
OUTPUT_LOG="/home/alvik/nl_triage_$(date +%Y%m%d_%H%M%S).log"

echo "=== NL Server Emergency Triage Script Armed ==="
echo "Target: ${REMOTE_HOST}"
echo "Output will be written to: ${OUTPUT_LOG}"
echo "Polling SSH connection..."

while true; do
    if ssh ${SSH_OPTS} "${REMOTE_HOST}" "echo 'SSH_CONNECTED'" 2>/dev/null | grep -q 'SSH_CONNECTED'; then
        echo "[!] Connected! Collecting instant forensic snapshot..."
        break
    fi
    sleep 0.5
done

ssh ${SSH_OPTS} "${REMOTE_HOST}" 'bash -s' << 'REMOTE_SCRIPT' | tee "${OUTPUT_LOG}"
echo "==================== [1. SYSTEM STATUS & UPTIME] ===================="
uptime
echo ""
free -h
echo ""
df -h
echo ""

echo "==================== [2. TOP CPU CONSUMING PROCESSES] ===================="
ps aux --sort=-%cpu | head -n 30
echo ""

echo "==================== [3. TOP MEMORY CONSUMING PROCESSES] ===================="
ps aux --sort=-%mem | head -n 30
echo ""

echo "==================== [4. SUSPICIOUS BINARIES IN /tmp, /dev/shm, /var/tmp] ===================="
ls -la /tmp /var/tmp /dev/shm 2>/dev/null
echo ""

echo "==================== [5. LISTENING PORTS & ACTIVE CONNECTIONS] ===================="
ss -tulpn 2>/dev/null || netstat -tulpn 2>/dev/null
echo ""

echo "==================== [6. RUNNING SYSTEMD SERVICES] ===================="
systemctl list-units --type=service --state=running --no-pager
echo ""

echo "==================== [7. CRONTABS & TIMERS] ===================="
echo "--- user crontabs ---"
crontab -l 2>/dev/null || echo "No user crontab"
sudo crontab -l 2>/dev/null || echo "No root crontab"
echo "--- /etc/cron* ---"
ls -la /etc/cron* /etc/crontab 2>/dev/null
echo "--- systemd timers ---"
systemctl list-timers --no-pager
echo ""

echo "==================== [8. AUTH LOGINS & RECENT LOGINS] ===================="
who -a 2>/dev/null
echo "--- last logins ---"
last -n 15 2>/dev/null
echo "--- last failed logins (lastb) ---"
sudo lastb -n 15 2>/dev/null || lastb -n 15 2>/dev/null || true
echo ""

echo "==================== [9. RECENT AUTH LOG ENTRIES] ===================="
sudo tail -n 40 /var/log/auth.log 2>/dev/null || sudo tail -n 40 /var/log/secure 2>/dev/null || journalctl -u ssh -n 40 --no-pager 2>/dev/null
echo ""

echo "==================== [10. DMESG / OOM ERRORS] ===================="
sudo dmesg -T | tail -n 30 2>/dev/null || dmesg | tail -n 30 2>/dev/null
echo ""

echo "==================== [11. PERSISTENT BASH / SSH KEYS] ===================="
ls -la ~/.ssh/ /root/.ssh/ 2>/dev/null || true
tail -n 20 ~/.bashrc /root/.bashrc 2>/dev/null || true

REMOTE_SCRIPT

echo ""
echo "=== Snapshot collection completed! Log saved to ${OUTPUT_LOG} ==="
