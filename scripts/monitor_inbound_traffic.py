#!/usr/bin/env python3
"""
Скрипт для мониторинга скорости и объема трафика по инбаундам 3x-UI.
Записывает снимки раз в минуту в MySQL БД `vpn`, таблицу `inbound_traffic_stats`.
"""
import os
import sqlite3
import pymysql
import time
import logging
from datetime import datetime

# Настройки базы данных
XUI_DB_PATH = "/etc/x-ui/x-ui.db"
ENV_PATH = "/home/alvik/vpn-service/.env"

logging.basicConfig(
    filename="/tmp/traffic_monitor.log",
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)

def get_mysql_password():
    with open(ENV_PATH) as f:
        for line in f:
            if line.startswith("MYSQL_PASSWORD="):
                return line.strip().split("=", 1)[1]
    raise ValueError("MYSQL_PASSWORD not found in .env")

def get_mysql_conn():
    password = get_mysql_password()
    return pymysql.connect(
        host="127.0.0.1",
        user="alvik",
        password=password,
        database="vpn",
        autocommit=True,
        cursorclass=pymysql.cursors.DictCursor
    )

def collect_and_store():
    # 1. Получаем данные из x-ui.db
    inbounds = []
    if os.path.exists(XUI_DB_PATH):
        conn_sqlite = sqlite3.connect(XUI_DB_PATH)
        cur_sqlite = conn_sqlite.cursor()
        cur_sqlite.execute("SELECT id, remark, port, protocol, up, down FROM inbounds WHERE enable = 1")
        inbounds = cur_sqlite.fetchall()
        conn_sqlite.close()

    # 2. Добавляем статистику awg0 если интерфейс существует
    rx_path = "/sys/class/net/awg0/statistics/rx_bytes"
    tx_path = "/sys/class/net/awg0/statistics/tx_bytes"
    if os.path.exists(rx_path) and os.path.exists(tx_path):
        try:
            with open(rx_path) as f:
                down_awg = int(f.read().strip())
            with open(tx_path) as f:
                up_awg = int(f.read().strip())
            inbounds.append((100, "AmneziaWG-awg0", 51888, "amneziawg", up_awg, down_awg))
        except Exception as e:
            logging.error(f"Error reading awg0 stats: {e}")

    mysql_conn = get_mysql_conn()
    with mysql_conn.cursor() as cur_mysql:
        for ib in inbounds:
            ib_id, remark, port, protocol, up, down = ib
            total = (up or 0) + (down or 0)

            # Вычисляем скорость относительно предыдущей записи
            cur_mysql.execute(
                "SELECT up_bytes, down_bytes, created_at FROM inbound_traffic_stats "
                "WHERE inbound_id = %s ORDER BY id DESC LIMIT 1",
                (ib_id,)
            )
            prev = cur_mysql.fetchone()
            
            up_speed_bps = 0
            down_speed_bps = 0

            if prev:
                prev_up = prev['up_bytes']
                prev_down = prev['down_bytes']
                prev_time = prev['created_at']
                dt = (datetime.now() - prev_time).total_seconds()
                if dt > 0:
                    if up >= prev_up:
                        up_speed_bps = int((up - prev_up) * 8 / dt)
                    if down >= prev_down:
                        down_speed_bps = int((down - prev_down) * 8 / dt)

            cur_mysql.execute(
                "INSERT INTO inbound_traffic_stats "
                "(inbound_id, remark, port, protocol, up_bytes, down_bytes, total_bytes, up_speed_bps, down_speed_bps) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (ib_id, remark or f"Inbound-{ib_id}", port, protocol, up or 0, down or 0, total, up_speed_bps, down_speed_bps)
            )

    mysql_conn.close()
    logging.info(f"Collected traffic snapshot for {len(inbounds)} inbounds (including awg0).")

if __name__ == "__main__":
    collect_and_store()
