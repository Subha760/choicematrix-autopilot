"""Real-time Telegram Approval & Interaction Daemon.
Supports:
1. Inline buttons with native modal alerts (show_alert=True) for mobile phones.
2. Bottom persistent reply keyboard buttons for 1-tap mobile phone approval.
3. Natural language text commands (approve, recreate, reject, status).
4. Full approval_hash synchronization for publication gating.
5. Automatic Git sync so GitHub Actions respects local approvals.
"""
import os
import json
import time
import requests
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(__file__).resolve().parent
STATE = ROOT / "state" / "posts.json"
TG_STATE = ROOT / "state" / "telegram.json"

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
        res = r.json()
        if not res.get("ok"):
            print(f"⚠️ Telegram API response for {method}: {res}", flush=True)
        return res
    except Exception as e:
        print(f"❌ Telegram API error {method}: {e}", flush=True)
        return {"ok": False}

def git_push_state():
    try:
        mingit = r"C:\Users\bongo\.mingit\cmd\git.exe"
        git_bin = mingit if Path(mingit).exists() else "git"
        subprocess.run([git_bin, "-C", str(ROOT), "add", "state/posts.json"], check=True, timeout=15, capture_output=True)
        subprocess.run([git_bin, "-C", str(ROOT), "commit", "-m", "chore: sync approval state [skip ci]"], check=True, timeout=15, capture_output=True)
        subprocess.run([git_bin, "-C", str(ROOT), "push", "origin", "main"], check=True, timeout=25, capture_output=True)
        print("📡 Synced approval state to GitHub main!", flush=True)
    except Exception as exc:
        print(f"Git push state warning: {exc}", flush=True)

def get_mobile_keyboard():
    return {
        "keyboard": [
            [{"text": "✅ Approve Afternoon"}, {"text": "✅ Approve Evening"}],
            [{"text": "🔄 Recreate Afternoon"}, {"text": "🔄 Recreate Evening"}],
            [{"text": "📊 Check Status"}, {"text": "❌ Reject"}]
        ],
        "resize_keyboard": True,
        "is_persistent": True
    }

def approve_pending_post(posts, approver_name="Subha", target_key=None):
    import autopilot
    entry = None
    if target_key and target_key in posts:
        entry = posts[target_key]
    else:
        for k in sorted(posts.keys(), reverse=True):
            if posts[k].get("status") in ("pending", "needs_attention", "expired"):
                entry = posts[k]
                target_key = k
                break

    if not entry:
        return False, "⚠️ No post found to approve."
    if entry.get("status") == "approved":
        slot_name = entry.get("slot", "post").capitalize()
        return True, f"ℹ️ {slot_name} post ({target_key}) is already approved and scheduled! 🚀"

    entry["status"] = "approved"
    entry["approved_at"] = datetime.now(timezone.utc).isoformat()
    entry["approval_hash"] = autopilot.approval_digest(entry)
    slot_name = entry.get("slot", "post").capitalize()
    pub_time = entry.get("publish_time_ist", "14:00")
    write_json(STATE, posts)
    git_push_state()
    return True, f"✅ {slot_name.upper()} APPROVED by {approver_name}!\n\n📅 Date: {entry.get('clean_date', target_key)}\n⏰ Scheduled for {pub_time} IST\n🌐 Platforms: Facebook, Instagram, Threads 🚀"

def reject_pending_post(posts, approver_name="Subha", target_key=None):
    entry = None
    if target_key and target_key in posts:
        entry = posts[target_key]
    else:
        for k in sorted(posts.keys(), reverse=True):
            if posts[k].get("status") in ("pending", "needs_attention", "expired"):
                entry = posts[k]
                target_key = k
                break
    if not entry:
        return False, "⚠️ No pending post found to reject."

    entry["status"] = "rejected"
    entry["approved_at"] = None
    entry["approval_hash"] = None
    slot_name = entry.get("slot", "post").capitalize()
    write_json(STATE, posts)
    git_push_state()
    return True, f"❌ {slot_name.upper()} REJECTED by {approver_name}. This post will not be published. (Tap Recreate if you want a fresh draft!)"

def recreate_and_send_post(posts, approver_name="Subha", target_key=None):
    approver_id = str(os.getenv("TG_APPROVER_ID", ""))
    entry = None
    if target_key and target_key in posts:
        entry = posts[target_key]
    else:
        for k in sorted(posts.keys(), reverse=True):
            if posts[k].get("status") in ("pending", "needs_attention", "expired", "rejected"):
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
        "text": f"🔄 RECREATING {slot_label} POST (Revision {new_rev}) with fresh live feed & Unsplash visuals... Generating now (15-20s) ⏳",
        "reply_markup": get_mobile_keyboard()
    })

    import autopilot
    settings = read_json(ROOT / "settings.json", {})
    try:
        new_entry = autopilot.create_post(target_key, settings, posts, revision=new_rev, slot=slot)
        write_json(STATE, posts)
        git_push_state()
        return True, f"✨ New {slot.capitalize()} Carousel Revision {new_rev} generated and sent above! Tap [Approve] when ready. 🚀"
    except Exception as e:
        return False, f"⚠️ Error regenerating post: {e}"

def poll_once(offset=0):
    token = os.getenv("TG_BOT_TOKEN")
    approver_id = str(os.getenv("TG_APPROVER_ID", "")).strip()

    try:
        r = requests.get(f"https://api.telegram.org/bot{token}/getUpdates",
                         params={"offset": offset, "timeout": 8, "allowed_updates": json.dumps(["callback_query", "message"])},
                         timeout=12)
        res = r.json()
        if not res.get("ok"):
            return offset
    except Exception as e:
        print(f"Polling warning: {e}", flush=True)
        time.sleep(2)
        return offset

    updates = res.get("result", [])
    posts = read_json(STATE, {})

    for u in updates:
        offset = max(offset, int(u["update_id"]) + 1)
        # Persist offset immediately
        write_json(TG_STATE, {"offset": offset})

        # 1. Handle Inline Button Clicks
        if "callback_query" in u:
            cb = u["callback_query"]
            cb_id = cb["id"]
            user_id = str(cb.get("from", {}).get("id", "")).strip()
            first_name = cb.get("from", {}).get("first_name", "Subha")
            data = cb.get("data", "")
            print(f"📥 Inline button clicked by {first_name} ({user_id}): data='{data}'", flush=True)

            # Parse action and post_id from callback_data (e.g. approve:2026-10-24_afternoon:1)
            parts = data.split(":")
            action = parts[0]
            target_key = parts[1] if len(parts) > 1 else None
            rev_val = parts[2] if len(parts) > 2 else None

            if action in ("approve", "test_approve"):
                success, msg = approve_pending_post(posts, first_name, target_key=target_key)
                # Show native modal alert dialog on phone screen!
                slot_txt = ("Afternoon" if target_key and "afternoon" in target_key else ("Evening" if target_key and "evening" in target_key else "Post"))
                alert_text = f"✅ {slot_txt} Post Approved!\nScheduled for publication."
                tg_api("answerCallbackQuery", {"callback_query_id": cb_id, "text": alert_text, "show_alert": True})

                if "message" in cb:
                    chat_id = cb["message"]["chat"]["id"]
                    msg_id = cb["message"]["message_id"]
                    tg_api("editMessageReplyMarkup", {
                        "chat_id": chat_id,
                        "message_id": msg_id,
                        "reply_markup": {"inline_keyboard": [[{"text": f"✅ {slot_txt} Approved & Scheduled", "callback_data": "done"}]]}
                    })
                tg_api("sendMessage", {"chat_id": approver_id, "text": msg, "reply_markup": get_mobile_keyboard()})
                print(f"[APPROVED via button] {target_key} by {first_name}", flush=True)

            elif action in ("reject", "test_reject"):
                success, msg = reject_pending_post(posts, first_name, target_key=target_key)
                tg_api("answerCallbackQuery", {"callback_query_id": cb_id, "text": "❌ Post Rejected.", "show_alert": True})
                if "message" in cb:
                    chat_id = cb["message"]["chat"]["id"]
                    msg_id = cb["message"]["message_id"]
                    tg_api("editMessageReplyMarkup", {
                        "chat_id": chat_id,
                        "message_id": msg_id,
                        "reply_markup": {"inline_keyboard": [[{"text": "❌ Rejected", "callback_data": "done"}]]}
                    })
                tg_api("sendMessage", {"chat_id": approver_id, "text": msg, "reply_markup": get_mobile_keyboard()})
                print(f"[REJECTED via button] {target_key} by {first_name}", flush=True)

            elif action in ("regenerate", "recreate", "regen"):
                tg_api("answerCallbackQuery", {"callback_query_id": cb_id, "text": "🔄 Recreating fresh draft...", "show_alert": True})
                success, msg = recreate_and_send_post(posts, first_name, target_key=target_key)
                tg_api("sendMessage", {"chat_id": approver_id, "text": msg, "reply_markup": get_mobile_keyboard()})
                print(f"[RECREATED via button] {target_key} by {first_name}", flush=True)

            else:
                tg_api("answerCallbackQuery", {"callback_query_id": cb_id, "text": "Action received! ✅", "show_alert": False})

        # 2. Handle Text Messages and Mobile Keyboard Taps
        elif "message" in u:
            m = u["message"]
            user_id = str(m.get("from", {}).get("id", "")).strip()
            first_name = m.get("from", {}).get("first_name", "Subha")
            text = m.get("text", "").strip()
            print(f"📥 Message received from {first_name} ({user_id}): '{text}'", flush=True)

            lower_text = text.lower()
            today = datetime.now().date().isoformat()

            # Identify target slot
            target_slot = None
            if "afternoon" in lower_text or "2pm" in lower_text:
                target_slot = "afternoon"
            elif "evening" in lower_text or "8pm" in lower_text:
                target_slot = "evening"

            target_key = None
            if target_slot:
                target_key = f"{today}_{target_slot}"
            else:
                for k in sorted(posts.keys(), reverse=True):
                    if posts[k].get("status") in ("pending", "needs_attention", "expired", "rejected"):
                        target_key = k
                        break

            # Handle Actions
            if any(w in lower_text for w in ("approve", "yes", "ok", "publish", "1")):
                success, msg = approve_pending_post(posts, first_name, target_key=target_key)
                tg_api("sendMessage", {"chat_id": approver_id, "text": msg, "reply_markup": get_mobile_keyboard()})
                print(f"[APPROVED via text] {target_key} by {first_name}", flush=True)

            elif any(w in lower_text for w in ("recreate", "regenerate", "new", "retry", "regen", "2")):
                success, msg = recreate_and_send_post(posts, first_name, target_key=target_key)
                tg_api("sendMessage", {"chat_id": approver_id, "text": msg, "reply_markup": get_mobile_keyboard()})
                print(f"[RECREATED via text] {target_key} by {first_name}", flush=True)

            elif any(w in lower_text for w in ("reject", "no", "cancel", "3")):
                success, msg = reject_pending_post(posts, first_name, target_key=target_key)
                tg_api("sendMessage", {"chat_id": approver_id, "text": msg, "reply_markup": get_mobile_keyboard()})
                print(f"[REJECTED via text] {target_key} by {first_name}", flush=True)

            elif any(w in lower_text for w in ("status", "check", "info")):
                afternoon_st = posts.get(f"{today}_afternoon", {}).get("status", "not generated").upper()
                evening_st = posts.get(f"{today}_evening", {}).get("status", "not generated").upper()
                msg = (f"📊 <b>DAILY SCHEDULE STATUS ({today})</b>\n\n"
                       f"🌞 <b>Afternoon Post (2:00 PM IST)</b>: {afternoon_st}\n"
                       f"🌙 <b>Evening Post (8:00 PM IST)</b>: {evening_st}\n\n"
                       "Tap a button below to approve or manage:")
                tg_api("sendMessage", {"chat_id": approver_id, "text": msg, "parse_mode": "HTML", "reply_markup": get_mobile_keyboard()})

            else:
                msg = ("👋 Hello! Use the mobile buttons below to approve or manage today's carousels:")
                tg_api("sendMessage", {"chat_id": approver_id, "text": msg, "reply_markup": get_mobile_keyboard()})

    return offset

def main():
    load_env()
    print("🚀 Telegram Real-Time Approval Daemon started.", flush=True)
    tg_state = read_json(TG_STATE, {"offset": 0})
    offset = tg_state.get("offset", 0)
    print(f"Listening for approvals on offset {offset}...", flush=True)
    while True:
        offset = poll_once(offset)
        time.sleep(1)

if __name__ == "__main__":
    main()
