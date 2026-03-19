#!/bin/bash
# Migrate from Docker AWG 1.0 to native AWG 2.0
set -e

LOG="/home/alvik/vpn-service/scripts/migrate-awg2.log"
echo "$(date) - Starting AWG 2.0 migration" | tee -a "$LOG"

# 0. Pre-flight checks
echo "=== Pre-flight ===" | tee -a "$LOG"
which awg || { echo "FATAL: awg not found"; exit 1; }
which awg-quick || { echo "FATAL: awg-quick not found"; exit 1; }
lsmod | grep amneziawg || sudo modprobe amneziawg
echo "Kernel module OK" | tee -a "$LOG"

# 1. Create config directory
sudo mkdir -p /etc/amnezia/amneziawg

# 2. Initialize DB tables and seed server config + migrate Root4 client
cd /home/alvik/vpn-service
/home/alvik/vpn-service/venv/bin/python3 << 'PYEOF'
import sys, json
sys.path.insert(0, "/home/alvik/vpn-service")

from awg_api import db
from awg_api.config import LISTEN_PORT

# Init tables
db.init_db()

# Read existing wg0.json
with open("docker/amneziawg-config/wg0.json") as f:
    data = json.load(f)

srv = data["server"]

# Save server config with AWG 2.0 params
server_cfg = {
    "private_key": srv["privateKey"],
    "public_key": srv["publicKey"],
    "listen_port": LISTEN_PORT,
    # Keep existing AWG 1.0 params
    "jc": srv["jc"],
    "jmin": srv["jmin"],
    "jmax": srv["jmax"],
    "s1": srv["s1"],
    "s2": srv["s2"],
    # New AWG 2.0 params
    "s3": 47,
    "s4": 32,
    # Dynamic H ranges (AWG 2.0 style)
    "h1": str(srv["h1"]),
    "h2": str(srv["h2"]),
    "h3": str(srv["h3"]),
    "h4": str(srv["h4"]),
    # CPS params — will be configured later
    "i1": None,
    "i2": None,
    "i3": None,
    "i4": None,
    "i5": None,
}
db.save_server_config(server_cfg)
print(f"Server config saved: pub={srv['publicKey']}")

# Migrate existing clients
for cid, c in data.get("clients", {}).items():
    existing = db.get_client(cid)
    if existing:
        print(f"Client {c['name']} already exists, skipping")
        continue

    import mysql.connector
    conn = mysql.connector.connect(
        host=db.MYSQL_HOST, port=db.MYSQL_PORT,
        user=db.MYSQL_USER, password=db.MYSQL_PASSWORD,
        database=db.MYSQL_DATABASE,
    )
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO awg_clients (id, name, address, private_key, public_key, preshared_key, enabled, created_at, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
    """, (cid, c["name"], c["address"], c["privateKey"], c["publicKey"], c["preSharedKey"], int(c["enabled"])))
    conn.commit()
    cur.close()
    conn.close()
    print(f"Migrated client: {c['name']} ({c['address']})")

print("DB migration complete")
PYEOF

echo "$(date) - DB seeded" | tee -a "$LOG"

# 3. Generate server config file
/home/alvik/vpn-service/venv/bin/python3 -c "
import sys; sys.path.insert(0, '/home/alvik/vpn-service')
from awg_api.awg_manager import write_server_conf
write_server_conf()
print('Server conf written')
"
echo "$(date) - Config generated" | tee -a "$LOG"

# 4. Verify config is valid
sudo cat /etc/amnezia/amneziawg/awg0.conf | head -5
echo "$(date) - Config verified" | tee -a "$LOG"

echo ""
echo "=== READY FOR CUTOVER ==="
echo "Run these commands to switch:"
echo ""
echo "  docker stop amneziawg"
echo "  sudo awg-quick up awg0"
echo "  sudo systemctl start awg-api"
echo "  sudo systemctl enable awg-api awg-interface"
echo ""
echo "To rollback:"
echo "  sudo awg-quick down awg0"
echo "  docker start amneziawg"
