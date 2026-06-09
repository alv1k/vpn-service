#!/usr/bin/env python3
"""Simple speed collector - run via cron every 30 seconds."""
import subprocess
import re
import sqlite3
import json
import time
import os

SPEED_DB = "/home/alvik/vpn-service/data/speed_history.db"
XUI_DB = "/etc/x-ui/x-ui.db"
PREV_FILE = "/home/alvik/vpn-service/data/speed_prev.json"

def parse_addr(s):
    m = re.match(r'(?:\[::ffff:([\d.]+)\]|([\d.]+)):(\d+)', s)
    if not m:
        return None, None
    return (m.group(1) or m.group(2)), int(m.group(3))

def get_ip_map():
    ip_to_client = {}
    try:
        conn = sqlite3.connect(XUI_DB, timeout=5)
        for email, ips_json in conn.execute("SELECT client_email, ips FROM inbound_client_ips WHERE ips IS NOT NULL"):
            try:
                for entry in json.loads(ips_json):
                    if isinstance(entry, dict) and entry.get("ip"):
                        ip_to_client[entry["ip"]] = email
            except:
                pass
        conn.close()
    except:
        pass
    return ip_to_client

# Load previous snapshot
prev = {}
if os.path.exists(PREV_FILE):
    try:
        with open(PREV_FILE) as f:
            prev = json.load(f)
    except:
        pass

# Parse ss
out = subprocess.check_output(["ss", "-tipn"], stderr=subprocess.DEVNULL, text=True, timeout=5)
lines = out.strip().split('\n')
now = int(time.time())
snapshot = {}

for i in range(1, len(lines)):
    if 'ESTAB' not in lines[i]:
        continue
    parts = lines[i].split()
    if len(parts) < 5:
        continue
    lip, lport = parse_addr(parts[3])
    if not lport or lport not in {7443, 47447, 8081, 4443}:
        continue
    rip = parse_addr(parts[4])[0]
    if not rip:
        continue
    
    recv = sent = rtt = 0
    if i + 1 < len(lines) and lines[i+1].startswith('\t'):
        d = lines[i+1]
        m = re.search(r'bytes_sent:(\d+)', d)
        if m: sent = int(m.group(1))
        m = re.search(r'bytes_received:(\d+)', d)
        if m: recv = int(m.group(1))
        m = re.search(r'rtt:([\d.]+)/', d)
        if m: rtt = float(m.group(1))
    
    if rip not in snapshot:
        snapshot[rip] = {'rx': 0, 'tx': 0, 'conns': 0, 'rtt_vals': [], 'client': get_ip_map().get(rip, '')}
    snapshot[rip]['rx'] += recv
    snapshot[rip]['tx'] += sent
    snapshot[rip]['conns'] += 1
    if rtt:
        snapshot[rip]['rtt_vals'].append(rtt)

# Store data
conn = sqlite3.connect(SPEED_DB)
for ip, d in snapshot.items():
    rx_spd = tx_spd = 0.0
    if ip in prev and now - prev[ip]['ts'] > 0:
        dt = now - prev[ip]['ts']
        drx = d['rx'] - prev[ip]['rx']
        dtx = d['tx'] - prev[ip]['tx']
        if drx > 0: rx_spd = (drx * 8) / dt / 1e3
        if dtx > 0: tx_spd = (dtx * 8) / dt / 1e3
    
    avg_rtt = sum(d['rtt_vals']) / len(d['rtt_vals']) if d['rtt_vals'] else 0
    
    conn.execute("INSERT INTO speed_history (timestamp, ip, client_name, connections, rx_bytes, tx_bytes, rx_speed, tx_speed, rtt) VALUES (?,?,?,?,?,?,?,?,?)",
                 (now, ip, d['client'], d['conns'], d['rx'], d['tx'], round(rx_spd, 2), round(tx_spd, 2), round(avg_rtt, 1)))

conn.commit()
conn.execute("DELETE FROM speed_history WHERE timestamp < ?", (now - 86400,))
conn.commit()
conn.close()

# Save snapshot
with open(PREV_FILE, 'w') as f:
    json.dump({ip: {'ts': now, 'rx': d['rx'], 'tx': d['tx']} for ip, d in snapshot.items()}, f)

print(f"Collected {len(snapshot)} IPs at {now}")
