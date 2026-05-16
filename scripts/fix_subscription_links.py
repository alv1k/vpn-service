"""
Fix broken vpn_keys.subscription_link entries.
For each VLESS key, looks up the client in 3x-ui and updates subId if wrong.
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx
from bot_xui.utils import XUIClient
from config import XUI_HOST, XUI_USERNAME, XUI_PASSWORD, XUI_SUB_PATH
from api.db import execute_query


def get_correct_sub_url(client_email: str) -> str | None:
    """Look up client in 3x-ui and return correct subscription URL."""
    xui = XUIClient(XUI_HOST, XUI_USERNAME, XUI_PASSWORD)
    info = xui.get_client_by_email(client_email)
    if not info:
        return None
    client = info['client']
    sub_id = client.get('subId')
    if not sub_id:
        return None
    return f"{XUI_SUB_PATH}/sub/{sub_id}"


def check_url(url: str) -> bool:
    """Check if subscription URL returns 200."""
    try:
        r = httpx.get(url, timeout=5, verify=False)
        return r.status_code == 200
    except Exception:
        return False


def main():
    rows = execute_query(
        'SELECT id, tg_id, user_id, client_name, subscription_link, expires_at '
        'FROM vpn_keys WHERE vpn_type="vless"',
        fetch='all',
    )

    fixed = 0
    skipped = 0
    failed = 0

    for r in rows:
        key_id = r['id']
        client_name = r['client_name']
        current_url = r['subscription_link']

        # Check if current URL works
        if current_url and check_url(current_url):
            print(f'  OK   id={key_id} {client_name}')
            skipped += 1
            continue

        print(f'  BROKEN id={key_id} {client_name} url={current_url}')

        # Try to find correct subId in 3x-ui
        correct_url = get_correct_sub_url(client_name)
        if not correct_url:
            # Try with _h suffix (hysteria)
            correct_url = get_correct_sub_url(f"{client_name}_h")

        if correct_url and check_url(correct_url):
            execute_query(
                'UPDATE vpn_keys SET subscription_link = %s WHERE id = %s',
                (correct_url, key_id),
            )
            print(f'  FIXED id={key_id} -> {correct_url}')
            fixed += 1
        else:
            print(f'  FAIL id={key_id} could not find correct subId in panel')
            failed += 1

    print(f'\nDone: {fixed} fixed, {skipped} ok, {failed} failed')


if __name__ == '__main__':
    main()
