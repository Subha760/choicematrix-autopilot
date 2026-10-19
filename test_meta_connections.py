"""Helper utility to verify Facebook Page, Instagram, Threads, and Telegram tokens."""
import os
import sys
import requests
from pathlib import Path

def load_env():
    env_file = Path(".env")
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())

def test_telegram():
    token = os.getenv("TG_BOT_TOKEN")
    if not token:
        print("[SKIP] Telegram: TG_BOT_TOKEN not provided in .env")
        return False
    try:
        r = requests.get(f"https://api.telegram.org/bot{token}/getMe", timeout=10)
        data = r.json()
        if data.get("ok"):
            bot = data["result"]
            print(f"[OK] Telegram: Connected as @{bot.get('username')} ({bot.get('first_name')})")
            return True
        else:
            print(f"[FAIL] Telegram: {data.get('description')}")
            return False
    except Exception as e:
        print(f"[FAIL] Telegram connection error: {e}")
        return False

def test_facebook():
    token = os.getenv("META_PAGE_TOKEN")
    page_id = os.getenv("FB_PAGE_ID")
    if not token or not page_id:
        print("[SKIP] Facebook Page: META_PAGE_TOKEN or FB_PAGE_ID not set")
        return False
    try:
        r = requests.get(f"https://graph.facebook.com/v21.0/{page_id}?fields=name,id&access_token={token}", timeout=10)
        data = r.json()
        if "error" in data:
            print(f"[FAIL] Facebook Page: {data['error'].get('message')}")
            return False
        print(f"[OK] Facebook Page: Connected to '{data.get('name')}' (ID: {data.get('id')})")
        return True
    except Exception as e:
        print(f"[FAIL] Facebook error: {e}")
        return False

def test_instagram():
    token = os.getenv("META_PAGE_TOKEN")
    ig_id = os.getenv("IG_USER_ID")
    if not token or not ig_id:
        print("[SKIP] Instagram: META_PAGE_TOKEN or IG_USER_ID not set")
        return False
    try:
        r = requests.get(f"https://graph.facebook.com/v21.0/{ig_id}?fields=username,name&access_token={token}", timeout=10)
        data = r.json()
        if "error" in data:
            print(f"[FAIL] Instagram: {data['error'].get('message')}")
            return False
        print(f"[OK] Instagram: Connected to @{data.get('username')}")
        return True
    except Exception as e:
        print(f"[FAIL] Instagram error: {e}")
        return False

def test_threads():
    token = os.getenv("THREADS_ACCESS_TOKEN") or os.getenv("META_PAGE_TOKEN")
    threads_id = os.getenv("THREADS_USER_ID", "me")
    if not token or not os.getenv("THREADS_USER_ID"):
        print("[SKIP] Threads: THREADS_USER_ID not set (optional)")
        return False
    try:
        r = requests.get(
            f"https://graph.threads.net/v1.0/{threads_id}?fields=id,username",
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
        )
        data = r.json()
        if "error" in data:
            print(f"[FAIL] Threads: {data['error'].get('message')}")
            return False
        print(f"[OK] Threads: Connected to @{data.get('username')} (ID: {data.get('id')})")
        return True
    except Exception as e:
        print(f"[FAIL] Threads error: {e}")
        return False

if __name__ == "__main__":
    load_env()
    print("=== Testing Social Media API Credentials ===")
    test_telegram()
    test_facebook()
    test_instagram()
    test_threads()
    print("============================================")
