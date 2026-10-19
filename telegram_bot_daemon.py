"""Real-time Telegram Approval & Interaction Daemon.
Listens for both inline button clicks (answering instantly) and text messages
so approving posts on Telegram Web or mobile works effortlessly.
"""
import os
import json
import time
import requests
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
STATE = ROOT / "state" / "posts.json"

def load_env():
    env_file = ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())

def read_json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default

def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)

def tg_api(method, payload=None):
    token = os.getenv("TG_BOT_TOKEN")
    url = f"https://api.telegram.org/bot{token}/{method}"
    try:
        r = requests.post(url, json=payload or {}, timeout=30)
        return r.json()
    except Exception as e:
        print(f"Telegram API error {method}: {e}")
        return {"ok": False}

def approve_pending_post(posts, approver_name="Subha"):
    today = datetime.now().date().isoformat()
    entry = posts.get(today)
    if not entry:
        for k in sorted(posts.keys(), reverse=True):
            if posts[k].get("status") == "pending":
                entry = posts[k]
                today = k
                break
    if not entry or entry.get("status") != "pending":
        return False, "No pending post found to approve."
    
    entry["status"] = "approved"
    entry["approved_at"] = datetime.now(timezone.utc).isoformat()
    write_json(STATE, posts)
    return True, f"✅ APPROVED by {approver_name}! Scheduled to publish across Facebook, Instagram, and Threads at 19:00 IST. 🚀"

def reject_pending_post(posts, approver_name="Subha"):
    today = datetime.now().date().isoformat()
    entry = posts.get(today)
    if not entry:
        for k in sorted(posts.keys(), reverse=True):
            if posts[k].get("status") == "pending":
                entry = posts[k]
                break
    if not entry:
        return False, "No pending post found to reject."
    entry["status"] = "rejected"
    write_json(STATE, posts)
    return True, f"❌ REJECTED by {approver_name}. This post will not be published."

def poll_once(offset=0):
    token = os.getenv("TG_BOT_TOKEN")
    approver_id = str(os.getenv("TG_APPROVER_ID", ""))
    
    try:
        r = requests.get(f"https://api.telegram.org/bot{token}/getUpdates",
                         params={"offset": offset, "timeout": 10}, timeout=15)
        res = r.json()
        if not res.get("ok"):
            return offset
    except Exception as e:
        print(f"Polling error: {e}")
        time.sleep(2)
        return offset

    updates = res.get("result", [])
    posts = read_json(STATE, {})

    for u in updates:
        offset = max(offset, int(u["update_id"]) + 1)
        
        # 1. Handle Inline Button Clicks
        if "callback_query" in u:
            cb = u["callback_query"]
            cb_id = cb["id"]
            user_id = str(cb.get("from", {}).get("id", ""))
            first_name = cb.get("from", {}).get("first_name", "Subha")
            data = cb.get("data", "")
            
            # Instantly acknowledge click to avoid Telegram Web loading spinner
            tg_api("answerCallbackQuery", {"callback_query_id": cb_id, "text": "Action received! ✅"})
            
            if user_id != approver_id:
                continue
                
            action = data.split(":")[0] if ":" in data else data
            if action in ("approve", "test_approve"):
                success, msg = approve_pending_post(posts, first_name)
                # Edit original message if possible
                if "message" in cb:
                    chat_id = cb["message"]["chat"]["id"]
                    msg_id = cb["message"]["message_id"]
                    tg_api("editMessageText", {
                        "chat_id": chat_id,
                        "message_id": msg_id,
                        "text": f"✅ APPROVED BY {first_name.upper()}\n\nPost is locked in for publication across Facebook, Instagram, and Threads at 19:00 IST. 🚀"
                    })
                tg_api("sendMessage", {"chat_id": approver_id, "text": msg})
                print(f"[APPROVED] by button from {first_name}")
                
            elif action in ("reject", "test_reject"):
                success, msg = reject_pending_post(posts, first_name)
                if "message" in cb:
                    chat_id = cb["message"]["chat"]["id"]
                    msg_id = cb["message"]["message_id"]
                    tg_api("editMessageText", {
                        "chat_id": chat_id,
                        "message_id": msg_id,
                        "text": f"❌ REJECTED BY {first_name.upper()}\n\nThis draft will not publish."
                    })
                tg_api("sendMessage", {"chat_id": approver_id, "text": msg})
                print(f"[REJECTED] by button from {first_name}")
                
        # 2. Handle Direct Text Replies (approve, yes, ok, regen, reject)
        elif "message" in u:
            m = u["message"]
            user_id = str(m.get("from", {}).get("id", ""))
            first_name = m.get("from", {}).get("first_name", "Subha")
            text = m.get("text", "").strip().lower()
            
            if user_id != approver_id or not text:
                continue
                
            if text in ("approve", "/approve", "yes", "y", "ok", "publish", "1"):
                success, msg = approve_pending_post(posts, first_name)
                tg_api("sendMessage", {"chat_id": approver_id, "text": msg})
                print(f"[APPROVED] via text '{text}' from {first_name}")
                
            elif text in ("reject", "/reject", "no", "cancel", "stop", "3"):
                success, msg = reject_pending_post(posts, first_name)
                tg_api("sendMessage", {"chat_id": approver_id, "text": msg})
                print(f"[REJECTED] via text '{text}' from {first_name}")
                
            elif text in ("/status", "status"):
                today = datetime.now().date().isoformat()
                st = posts.get(today, {}).get("status", "none")
                tg_api("sendMessage", {"chat_id": approver_id, "text": f"ℹ️ Today's status ({today}): {st.upper()}"})

    return offset

def main():
    load_env()
    print("Telegram Approval Listener started. Press Ctrl+C to stop.")
    offset = 0
    while True:
        offset = poll_once(offset)
        time.sleep(1)

if __name__ == "__main__":
    main()
