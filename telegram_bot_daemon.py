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

def approve_pending_post(posts, approver_name="Subha", target_key=None):
    entry = None
    if target_key and target_key in posts:
        entry = posts[target_key]
    else:
        for k in sorted(posts.keys(), reverse=True):
            if posts[k].get("status") == "pending":
                entry = posts[k]
                target_key = k
                break
    if not entry or entry.get("status") != "pending":
        return False, "No pending post found to approve."

    entry["status"] = "approved"
    entry["approved_at"] = datetime.now(timezone.utc).isoformat()
    slot_name = entry.get("slot", "post").capitalize()
    pub_time = entry.get("publish_time_ist", "14:00")
    write_json(STATE, posts)
    return True, f"✅ {slot_name.upper()} APPROVED by {approver_name}! Scheduled to publish across Facebook, Instagram, and Threads at {pub_time} IST. 🚀"

def reject_pending_post(posts, approver_name="Subha", target_key=None):
    entry = None
    if target_key and target_key in posts:
        entry = posts[target_key]
    else:
        for k in sorted(posts.keys(), reverse=True):
            if posts[k].get("status") == "pending":
                entry = posts[k]
                target_key = k
                break
    if not entry:
        return False, "No pending post found to reject."
    entry["status"] = "rejected"
    slot_name = entry.get("slot", "post").capitalize()
    write_json(STATE, posts)
    return True, f"❌ {slot_name.upper()} REJECTED by {approver_name}. This post will not be published. (Reply 'recreate' if you'd like a fresh version!)"

def recreate_and_send_post(posts, approver_name="Subha", target_key=None):
    approver_id = str(os.getenv("TG_APPROVER_ID", ""))
    entry = None
    if target_key and target_key in posts:
        entry = posts[target_key]
    else:
        for k in sorted(posts.keys(), reverse=True):
            if posts[k].get("status") in ("pending", "rejected"):
                entry = posts[k]
                target_key = k
                break

    if not target_key:
        today = datetime.now().date().isoformat()
        target_key = f"{today}_afternoon"

    slot = entry.get("slot", "afternoon") if entry else ("evening" if "_evening" in target_key else "afternoon")
    old_rev = entry.get("revision", 1) if entry else 1
    new_rev = old_rev + 1

    if entry:
        entry["status"] = "rejected"
        write_json(STATE, posts)

    slot_label = "🌞 AFTERNOON" if slot == "afternoon" else "🌙 EVENING"
    tg_api("sendMessage", {
        "chat_id": approver_id,
        "text": f"🔄 RECREATING {slot_label} POST (Revision {new_rev}) with fresh live feed & Unsplash visuals... Generating now (15-20s) ⏳"
    })

    import autopilot
    settings = read_json(ROOT / "settings.json", {})
    try:
        new_entry = autopilot.create_post(target_key, settings, posts, revision=new_rev, slot=slot)
        write_json(STATE, posts)
        return True, f"✨ New {slot.capitalize()} Carousel Revision {new_rev} generated and sent above! Tap [Approve] when ready. 🚀"
    except Exception as e:
        return False, f"⚠️ Error regenerating post: {e}"
        return False, f"⚠️ Error regenerating post: {e}"

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
                
            # Parse action and post_id from callback_data (e.g. approve:2026-10-21_afternoon:1)
            parts = data.split(":")
            action = parts[0]
            target_key = parts[1] if len(parts) > 1 else None
            rev_val = parts[2] if len(parts) > 2 else None

            if action in ("approve", "test_approve"):
                success, msg = approve_pending_post(posts, first_name, target_key=target_key)
                if "message" in cb:
                    chat_id = cb["message"]["chat"]["id"]
                    msg_id = cb["message"]["message_id"]
                    slot_txt = ("AFTERNOON" if target_key and "afternoon" in target_key else ("EVENING" if target_key and "evening" in target_key else "POST"))
                    tg_api("editMessageText", {
                        "chat_id": chat_id,
                        "message_id": msg_id,
                        "text": f"✅ {slot_txt} APPROVED BY {first_name.upper()}\n\nPost is locked in for publication across Facebook, Instagram, and Threads! 🚀"
                    })
                tg_api("sendMessage", {"chat_id": approver_id, "text": msg})
                print(f"[APPROVED] by button from {first_name} for {target_key}")

            elif action in ("reject", "test_reject"):
                success, msg = reject_pending_post(posts, first_name, target_key=target_key)
                if "message" in cb:
                    chat_id = cb["message"]["chat"]["id"]
                    msg_id = cb["message"]["message_id"]
                    tg_api("editMessageText", {
                        "chat_id": chat_id,
                        "message_id": msg_id,
                        "text": f"❌ REJECTED BY {first_name.upper()}.\nReply 'recreate' if you would like a fresh draft."
                    })
                tg_api("sendMessage", {"chat_id": approver_id, "text": msg})
                print(f"[REJECTED] by button from {first_name} for {target_key}")

            elif action in ("regenerate", "recreate", "regen"):
                if "message" in cb:
                    chat_id = cb["message"]["chat"]["id"]
                    msg_id = cb["message"]["message_id"]
                    tg_api("editMessageText", {
                        "chat_id": chat_id,
                        "message_id": msg_id,
                        "text": f"🔄 RECREATING DRAFT BY {first_name.upper()}...\nGenerating fresh carousel with new live feed & Unsplash imagery! ⏳"
                    })
                success, msg = recreate_and_send_post(posts, first_name, target_key=target_key)
                tg_api("sendMessage", {"chat_id": approver_id, "text": msg})
                print(f"[RECREATED] via button from {first_name} for {target_key}")

        # 2. Handle Direct Text Replies (approve, yes, ok, regen, reject)
        elif "message" in u:
            m = u["message"]
            user_id = str(m.get("from", {}).get("id", ""))
            first_name = m.get("from", {}).get("first_name", "Subha")
            text = m.get("text", "").strip().lower()

            if user_id != approver_id or not text:
                continue

            target_slot = None
            if "evening" in text or "night" in text or "8pm" in text:
                target_slot = "evening"
            elif "afternoon" in text or "day" in text or "2pm" in text:
                target_slot = "afternoon"

            target_key = None
            if target_slot:
                for k in sorted(posts.keys(), reverse=True):
                    if posts[k].get("slot") == target_slot and posts[k].get("status") in ("pending", "rejected"):
                        target_key = k
                        break

            if any(w in text for w in ("approve", "yes", "ok", "publish", "1")):
                success, msg = approve_pending_post(posts, first_name, target_key=target_key)
                tg_api("sendMessage", {"chat_id": approver_id, "text": msg})
                print(f"[APPROVED] via text '{text}' from {first_name}")

            elif any(w in text for w in ("recreate", "regenerate", "new", "retry", "regen", "2")):
                success, msg = recreate_and_send_post(posts, first_name, target_key=target_key)
                tg_api("sendMessage", {"chat_id": approver_id, "text": msg})
                print(f"[RECREATED] via text '{text}' from {first_name}")

            elif any(w in text for w in ("reject", "no", "cancel", "3")):
                success, msg = reject_pending_post(posts, first_name, target_key=target_key)
                tg_api("sendMessage", {"chat_id": approver_id, "text": msg})
                print(f"[REJECTED] via text '{text}' from {first_name}")

            elif text in ("/status", "status"):
                today = datetime.now().date().isoformat()
                afternoon_st = posts.get(f"{today}_afternoon", {}).get("status", "none")
                evening_st = posts.get(f"{today}_evening", {}).get("status", "none")
                tg_api("sendMessage", {"chat_id": approver_id, "text": f"ℹ️ Status ({today}):\n🌞 Afternoon (2 PM): {afternoon_st.upper()}\n🌙 Evening (8 PM): {evening_st.upper()}"})

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
