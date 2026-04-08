#!/usr/bin/env python3
"""Отправка запланированных постов в канал @tiin_service."""

import sys
import os
import json
import requests
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import TELEGRAM_BOT_TOKEN

CHANNEL_ID = "@tiin_service"
POSTS_FILE = Path(__file__).resolve().parent / "channel_posts.json"


def load_posts():
    if not POSTS_FILE.exists():
        return []
    with open(POSTS_FILE) as f:
        return json.load(f)


def save_posts(posts):
    with open(POSTS_FILE, "w") as f:
        json.dump(posts, f, ensure_ascii=False, indent=2)


def send_post(text: str) -> bool:
    resp = requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
        json={
            "chat_id": CHANNEL_ID,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        },
        timeout=15,
    )
    return resp.ok


def main():
    posts = load_posts()
    if not posts:
        print("No posts to send")
        return

    post = posts[0]
    text = post["text"]

    if send_post(text):
        print(f"Sent: {text[:60]}...")
        posts.pop(0)
        save_posts(posts)
    else:
        print("Failed to send post")
        sys.exit(1)


if __name__ == "__main__":
    main()
