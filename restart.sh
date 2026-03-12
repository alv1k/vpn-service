#!/bin/bash
# Перезапуск сервисов VPN-бота
# Использование:
#   ./restart.sh        — перезапустить оба сервиса
#   ./restart.sh bot    — только бот
#   ./restart.sh api    — только API
#   ./restart.sh status — показать статус

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

show_status() {
    for svc in bot api; do
        if systemctl is-active --quiet "$svc"; then
            echo -e "  ${GREEN}●${NC} $svc — active"
        else
            echo -e "  ${RED}●${NC} $svc — inactive"
        fi
    done
}

restart_service() {
    local svc=$1
    echo -e "${YELLOW}↻${NC} Restarting $svc..."
    sudo systemctl restart "$svc"
    sleep 1
    if systemctl is-active --quiet "$svc"; then
        echo -e "  ${GREEN}●${NC} $svc — OK"
    else
        echo -e "  ${RED}●${NC} $svc — FAILED"
        echo "  Last logs:"
        journalctl -u "$svc" -n 5 --no-pager
        return 1
    fi
}

case "${1:-all}" in
    bot)
        restart_service bot
        ;;
    api)
        restart_service api
        ;;
    all)
        restart_service bot
        restart_service api
        ;;
    status|s)
        echo "Service status:"
        show_status
        ;;
    logs)
        svc="${2:-bot}"
        journalctl -u "$svc" -f --no-pager
        ;;
    *)
        echo "Usage: $0 {bot|api|all|status|logs [bot|api]}"
        exit 1
        ;;
esac
