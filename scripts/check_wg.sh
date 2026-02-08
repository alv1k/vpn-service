#!/bin/bash

# Настройки
WG_INTERFACE="wg0"
WG_PORT=51822
EXT_IF="ens3"  # внешний интерфейс сервера, через который идёт интернет

echo "🔹 Проверка интерфейса WireGuard: $WG_INTERFACE"
ip a show $WG_INTERFACE &>/dev/null
if [ $? -eq 0 ]; then
    echo "✔ Интерфейс $WG_INTERFACE существует"
else
    echo "❌ Интерфейс $WG_INTERFACE не найден"
fi

echo
echo "🔹 Проверка, слушает ли WireGuard UDP порт $WG_PORT"
sudo ss -ulpn | grep $WG_PORT &>/dev/null
if [ $? -eq 0 ]; then
    echo "✔ Порт $WG_PORT слушает WireGuard"
else
    echo "❌ Порт $WG_PORT не слушает (WireGuard не поднят или порт заблокирован)"
fi

echo
echo "🔹 Проверка правил UFW для порта $WG_PORT"
sudo ufw status | grep $WG_PORT &>/dev/null
if [ $? -eq 0 ]; then
    echo "✔ Порт $WG_PORT разрешён в UFW"
else
    echo "❌ Порт $WG_PORT не найден в правилах UFW, открываем..."
    sudo ufw allow $WG_PORT/udp
    echo "✔ Порт $WG_PORT теперь открыт в UFW"
fi

echo
echo "🔹 Проверка NAT для выхода в интернет через $EXT_IF"
iptables -t nat -L POSTROUTING -n -v | grep $EXT_IF &>/dev/null
if [ $? -eq 0 ]; then
    echo "✔ NAT настроен через $EXT_IF"
else
    echo "❌ NAT не найден. Добавим правило..."
    sudo iptables -t nat -A POSTROUTING -o $EXT_IF -j MASQUERADE
    echo "✔ NAT добавлен"
fi

echo
echo "🔹 Проверка подключённых клиентов WireGuard"
if sudo wg show $WG_INTERFACE &>/dev/null; then
    sudo wg show $WG_INTERFACE | grep peer -A 2
    echo "✔ Проверка завершена. Клиенты показаны выше."
else
    echo "❌ WireGuard интерфейс $WG_INTERFACE не поднят, клиенты не доступны."
fi

echo
echo "🔹 Скрипт завершён. Если все галочки ✔, VPN работает корректно."
