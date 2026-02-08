#!/bin/bash

# Настройки
WG_INTERFACE="wg0"
WG_PORT=51822
EXT_IF="ens3"  # внешний интерфейс сервера

echo "🔹 Перезапуск и проверка WireGuard сервиса $WG_INTERFACE …"

# Удаляем старый интерфейс, если остался
ip a show $WG_INTERFACE &>/dev/null
if [ $? -eq 0 ]; then
    echo "⚠ Интерфейс $WG_INTERFACE уже существует. Удаляем..."
    sudo ip link delete $WG_INTERFACE
    sleep 1
fi

# Перезапуск через systemd
sudo systemctl restart wg-quick@$WG_INTERFACE
sudo systemctl status wg-quick@$WG_INTERFACE --no-pager

echo
echo "🔹 Проверка интерфейса и порта"
ip a show $WG_INTERFACE &>/dev/null && echo "✔ Интерфейс $WG_INTERFACE поднят" || echo "❌ Интерфейс не найден"
sudo ss -ulpn | grep $WG_PORT &>/dev/null && echo "✔ Порт $WG_PORT слушает" || echo "❌ Порт не слушает"

echo
echo "🔹 Проверка UFW"
sudo ufw status | grep $WG_PORT &>/dev/null || { echo "❌ Порт $WG_PORT не открыт. Открываем..."; sudo ufw allow $WG_PORT/udp; }
echo "✔ Порт $WG_PORT проверен"

echo
echo "🔹 Проверка NAT через $EXT_IF"
iptables -t nat -L POSTROUTING -n -v | grep $EXT_IF &>/dev/null || { 
    echo "❌ NAT не найден. Добавляем правило..."; 
    sudo iptables -t nat -A POSTROUTING -o $EXT_IF -j MASQUERADE; 
}
echo "✔ NAT проверен"

echo
echo "🔹 Подключённые клиенты WireGuard"
if sudo wg show $WG_INTERFACE &>/dev/null; then
    echo "PK (публичный ключ)           IP VPN            Endpoint               RX       TX"
    echo "---------------------------------------------------------------"
    sudo wg show $WG_INTERFACE dump | awk 'NR>1 {printf "%-32s %-15s %-21s %-8s %-8s\n", $1, $3, $5, $6, $7}'
else
    echo "❌ WireGuard интерфейс $WG_INTERFACE не поднят, клиенты не доступны."
fi

echo
echo "✅ Скрипт завершён. Все проверки проведены."
