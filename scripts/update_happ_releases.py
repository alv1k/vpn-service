#!/usr/bin/env python3
"""
Скрипт проверки и автообновления дистрибутивов Happ Proxy.
Проверяет последние релизы на GitHub:
- Android: Happ-proxy/happ-android -> Happ.apk
- Windows: Happ-proxy/happ-desktop -> setup-Happ.x64.exe
- macOS:   Happ-proxy/happ-desktop -> Happ.macOS.universal.dmg

Сохраняет в /home/alvik/vpn-service/static/apps/ и синхронизирует на ru-server:/var/www/apps/
"""

import os
import sys
import json
import logging
import subprocess
import urllib.request
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("update_happ")

APPS_DIR = Path("/home/alvik/vpn-service/static/apps")
STATE_FILE = Path("/home/alvik/vpn-service/data/happ_versions.json")
RU_SERVER_DEST = "ru-server:/var/www/apps/"

TARGETS = [
    {
        "repo": "Happ-proxy/happ-android",
        "asset_match": lambda name: name == "Happ.apk",
        "output_name": "Happ.apk",
        "platform": "android",
    },
    {
        "repo": "Happ-proxy/happ-desktop",
        "asset_match": lambda name: name == "setup-Happ.x64.exe",
        "output_name": "setup-Happ.x64.exe",
        "platform": "windows",
    },
    {
        "repo": "Happ-proxy/happ-desktop",
        "asset_match": lambda name: name == "Happ.macOS.universal.dmg",
        "output_name": "Happ.macOS.universal.dmg",
        "platform": "macos",
    },
]


def get_latest_release(repo: str) -> dict:
    url = f"https://api.github.com/repos/{repo}/releases/latest"
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "TIIN-VPN-Updater/1.0",
            "Accept": "application/vnd.github.v3+json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        logger.error(f"Failed to fetch {repo} latest release: {e}")
        return {}


def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            pass
    return {}


def save_state(state: dict):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2))


def download_file(url: str, dest_path: Path) -> bool:
    tmp_path = dest_path.with_suffix(dest_path.suffix + ".tmp")
    logger.info(f"Downloading {url} -> {tmp_path}")
    try:
        cmd = ["curl", "-L", "-s", "--max-time", "300", "-o", str(tmp_path), url]
        res = subprocess.run(cmd, check=True)
        if tmp_path.exists() and tmp_path.stat().st_size > 1024 * 1024:
            tmp_path.replace(dest_path)
            logger.info(f"Successfully saved {dest_path} ({dest_path.stat().st_size} bytes)")
            return True
        else:
            logger.error(f"Downloaded file {tmp_path} is too small or missing")
            if tmp_path.exists():
                tmp_path.unlink()
            return False
    except Exception as e:
        logger.error(f"Download failed for {url}: {e}")
        if tmp_path.exists():
            tmp_path.unlink()
        return False


def sync_to_ru():
    logger.info(f"Syncing {APPS_DIR}/ to {RU_SERVER_DEST}...")
    try:
        subprocess.run(
            ["rsync", "-avz", "--timeout=30", f"{APPS_DIR}/", RU_SERVER_DEST],
            check=True,
            capture_output=True,
            text=True,
        )
        logger.info("Sync to ru-server completed successfully")
    except Exception as e:
        logger.error(f"Failed to sync to ru-server: {e}")


def main():
    APPS_DIR.mkdir(parents=True, exist_ok=True)
    state = load_state()
    updated = False

    releases_cache = {}

    for target in TARGETS:
        repo = target["repo"]
        out_name = target["output_name"]
        dest = APPS_DIR / out_name

        if repo not in releases_cache:
            rel = get_latest_release(repo)
            releases_cache[repo] = rel
        else:
            rel = releases_cache[repo]

        if not rel:
            continue

        tag = rel.get("tag_name")
        assets = rel.get("assets", [])

        # Check if asset matches
        matched_asset = next((a for a in assets if target["asset_match"](a.get("name", ""))), None)
        if not matched_asset:
            logger.warning(f"No matching asset found for {out_name} in {repo} {tag}")
            continue

        download_url = matched_asset.get("browser_download_url")
        last_tag = state.get(out_name, {}).get("tag")

        need_download = not dest.exists() or last_tag != tag or "--force" in sys.argv

        if need_download:
            logger.info(f"Updating {out_name}: current={last_tag}, latest={tag}")
            if download_file(download_url, dest):
                state[out_name] = {
                    "tag": tag,
                    "updated_at": rel.get("published_at"),
                    "size": dest.stat().st_size,
                    "url": download_url,
                }
                updated = True
        else:
            logger.info(f"{out_name} is up to date ({tag})")

    if updated or "--sync" in sys.argv:
        save_state(state)
        sync_to_ru()


if __name__ == "__main__":
    main()
