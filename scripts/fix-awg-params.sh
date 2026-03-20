#!/bin/bash
# Fix AWG config: remove invalid S3/S4, ensure H values are integers
set -e

cd /home/alvik/vpn-service

echo "=== Fixing AWG params in DB ==="
/home/alvik/vpn-service/venv/bin/python3 << 'PYEOF'
import sys
sys.path.insert(0, "/home/alvik/vpn-service")

from awg_api import db

# Check if s3/s4 columns exist and drop them
conn = db._get_conn()
cur = conn.cursor()

# Check columns
cur.execute("SHOW COLUMNS FROM awg_server LIKE 's3'")
has_s3 = cur.fetchone() is not None
cur.execute("SHOW COLUMNS FROM awg_server LIKE 's4'")
has_s4 = cur.fetchone() is not None

if has_s3:
    cur.execute("ALTER TABLE awg_server DROP COLUMN s3")
    print("Dropped s3 column")
if has_s4:
    cur.execute("ALTER TABLE awg_server DROP COLUMN s4")
    print("Dropped s4 column")

# Fix H columns: convert VARCHAR to BIGINT if needed
for col in ("h1", "h2", "h3", "h4"):
    cur.execute(f"SELECT {col} FROM awg_server WHERE id=1")
    row = cur.fetchone()
    if row:
        val = row[0]
        # If it's a range string like "100000-800000", resolve it
        if isinstance(val, str) and "-" in val:
            import random
            lo, hi = val.split("-")
            new_val = random.randint(int(lo), int(hi))
            cur.execute(f"UPDATE awg_server SET {col}=%s WHERE id=1", (new_val,))
            print(f"Fixed {col}: '{val}' -> {new_val}")
        else:
            print(f"{col} OK: {val}")

# Ensure H columns are BIGINT
for col in ("h1", "h2", "h3", "h4"):
    cur.execute(f"ALTER TABLE awg_server MODIFY {col} BIGINT NOT NULL DEFAULT 0")

conn.commit()
cur.close()
conn.close()
print("\nDB fix complete")

# Regenerate server config
from awg_api.awg_manager import write_server_conf
write_server_conf()
print("Server config regenerated")
PYEOF

echo ""
echo "=== New server config ==="
sudo head -25 /etc/amnezia/amneziawg/awg0.conf

echo ""
echo "=== Reloading AWG interface ==="
sudo awg syncconf awg0 <(sudo awg-quick strip awg0) && echo "Interface reloaded" || echo "Interface not up, run: sudo awg-quick up awg0"

echo ""
echo "=== Restarting AWG API ==="
sudo systemctl restart awg-api
echo "Done! New client configs will now be valid."
