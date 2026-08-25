import base64
from datetime import datetime, timedelta

# Эмулируем вызов функции proxy_subscription (логику)
def test_remark_logic():
    # Эмуляция данных подписки
    raw_sub = "dmxlc3M6Ly91dWlkQGxvY2FsaG9zdDo0NDM/c2VjdXJpdHk9cmVhbGl0eSN2bGVzc191c2VyCmh5c3RlcmlhMjovL3Rva2VuQGxvY2FsaG9zdDo0NDQzI2h5c3RlcmlhX3VzZXI="
    decoded_sub = base64.b64decode(raw_sub).decode('utf-8')
    
    expires_at = datetime.utcnow() + timedelta(days=1)
    now = datetime.utcnow()
    status = "✅Active" if expires_at and expires_at > now else "❌Ended"
    remark = f"🐿️ TIIN vpn | {status}"
    
    new_lines = []
    for line in decoded_sub.splitlines():
        if '#' in line:
            parts = line.split('#')
            new_lines.append(f"{'#'.join(parts[:-1])}#{remark}")
        else:
            new_lines.append(line)
    new_sub = "\n".join(new_lines)
    
    print("Original decoded:", decoded_sub)
    print("New decoded:", new_sub)

test_remark_logic()
