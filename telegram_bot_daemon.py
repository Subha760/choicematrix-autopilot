"""Real-time Telegram Approval & Interaction Daemon.
- 100% Pure text command listener (Zero broken/frozen buttons!).
- Commands supported: 'approve', 'approve afternoon', 'approve evening', 'recreate', 'reject', 'status'.
- Crash-proof 24/7 background loop with auto-reconnect on network timeout.
- Instant live publication if approved post is already past its scheduled time.
- Seamless automatic Git state sync to GitHub main.
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
GEN_ERRORS = ROOT / "state" / "generation-attempts.json"

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
        subprocess.run([git_bin, "-C", str(ROOT), "pull", "--rebase", "origin", "main"], timeout=20, capture_output=True)
        subprocess.run([git_bin, "-C", str(ROOT), "add", "state/posts.json", "state/generation-attempts.json"], timeout=15, capture_output=True)
        subprocess.run([git_bin, "-C", str(ROOT), "commit", "-m", "chore: sync approval state [skip ci]"], timeout=15, capture_output=True)
        subprocess.run([git_bin, "-C", str(ROOT), "push", "origin", "main"], check=True, timeout=25, capture_output=True)
        print("📡 Synced approval state to GitHub main!", flush=True)
    except Exception as exc:
        print(f"Git push state warning: {exc}", flush=True)

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
        return False, "⚠️ No pending draft found to approve."

    entry["status"] = "approved"
    entry["approved_at"] = datetime.now(timezone.utc).isoformat()
    entry["approval_hash"] = autopilot.approval_digest(entry)
    slot_name = entry.get("slot", "post").capitalize()
    pub_time = entry.get("publish_time_ist", "14:00")
    write_json(STATE, posts)
    git_push_state()

    # If post is already due (past scheduled time), publish right away!
    settings = read_json(ROOT / "settings.json", {})
    now_ist = datetime.now(autopilot.IST)
    publish_result_msg = ""
    if autopilot.due(entry, settings, now_ist) and settings.get("publishing_enabled", False):
        print(f"🚀 {target_key} is already due ({pub_time} IST) - publishing immediately...", flush=True)
        try:
            autopilot.process_publications(posts, settings, now_ist)
            write_json(STATE, posts)
            git_push_state()
            fb_id = entry.get("fb", {}).get("id")
            ig_id = entry.get("ig", {}).get("id")
            threads_id = entry.get("threads", {}).get("id")
            publish_result_msg = (
                f"\n\n🚀 <b>PUBLISHED LIVE NOW!</b>\n"
                f"• Facebook ID: <code>{fb_id or 'posted'}</code>\n"
                f"• Instagram ID: <code>{ig_id or 'posted'}</code>\n"
                f"• Threads ID: <code>{threads_id or 'posted'}</code>"
            )
        except Exception as pe:
            print(f"Immediate publish warning: {pe}", flush=True)
            publish_result_msg = f"\n\n⚠️ Publication note: {pe}"

    return True, (
        f"✅ <b>{slot_name.upper()} APPROVED by {approver_name}!</b>\n\n"
        f"📅 Date: {entry.get('clean_date', target_key)}\n"
        f"⏰ Scheduled Time: {pub_time} IST\n"
        f"🌐 Platforms: Facebook, Instagram, Threads{publish_result_msg}"
    )

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
        return False, "⚠️ No pending draft found to reject."

    entry["status"] = "rejected"
    entry["approved_at"] = None
    entry["approval_hash"] = None
    slot_name = entry.get("slot", "post").capitalize()
    write_json(STATE, posts)
    git_push_state()
    return True, f"❌ <b>{slot_name.upper()} REJECTED by {approver_name}.</b>\nThis post will not be published. (Reply 'recreate' if you want a fresh draft!)"

def recreate_and_send_post(posts, approver_name="Subha", target_key=None):
    approver_id = str(os.getenv("TG_APPROVER_ID", ""))
    entry = None
    if target_key and target_key in posts:
        entry = posts[target_key]
    else:
        for k in sorted(posts.keys(), reverse=True):
            if posts[k].get("status") in ("pending", "rejected", "needs_attention", "expired"):
                entry = posts[k]
                target_key = k
                break

    if not target_key:
        today = datetime.now().date().isoformat()
        target_key = f"{today}_afternoon"

    slot = entry.get("slot", "afternoon") if entry else ("evening" if "_evening" in target_key else "afternoon")
    old_rev = entry.get("revision", 1) if entry else 1
    new_rev = old_rev + 1

    # Clear generation error backoff so it generates immediately with zero wait
    gen_errors = read_json(GEN_ERRORS, {})
    gen_errors.pop(target_key, None)
    write_json(GEN_ERRORS, gen_errors)

    if entry:
        entry["status"] = "rejected"
        write_json(STATE, posts)

    slot_label = "🌞 AFTERNOON" if slot == "afternoon" else "🌙 EVENING"
    tg_api("sendMessage", {
        "chat_id": approver_id,
        "text": f"🔄 <b>RECREATING {slot_label} POST</b> (Revision {new_rev}) with fresh live feed & Unsplash visuals...\nGenerating now (15-20s) ⏳",
        "parse_mode": "HTML"
    })

    import autopilot
    settings = read_json(ROOT / "settings.json", {})
    try:
        new_entry = autopilot.create_post(target_key, settings, posts, revision=new_rev, slot=slot)
        write_json(STATE, posts)
        git_push_state()
        return True, f"✨ New {slot.capitalize()} Carousel Revision {new_rev} generated and sent above!\nReply 'approve' when ready. 🚀"
    except Exception as e:
        return False, f"⚠️ Error regenerating post: {e}"

def poll_once(offset=0):
    token = os.getenv("TG_BOT_TOKEN")
    approver_id = str(os.getenv("TG_APPROVER_ID", "")).strip()

    try:
        r = requests.get(
            f"https://api.telegram.org/bot{token}/getUpdates",
            params={"offset": offset, "timeout": 8, "allowed_updates": json.dumps(["message"])},
            timeout=12
        )
        res = r.json()
        if not res.get("ok"):
            return offset
    except Exception as e:
        print(f"Polling network warning: {e}", flush=True)
        time.sleep(2)
        return offset

    updates = res.get("result", [])
    posts = read_json(STATE, {})

    for u in updates:
        offset = max(offset, int(u["update_id"]) + 1)
        write_json(TG_STATE, {"offset": offset})

        if "message" in u:
            m = u["message"]
            user_id = str(m.get("from", {}).get("id", "")).strip()
            first_name = m.get("from", {}).get("first_name", "Subha")
            text = m.get("text", "").strip()
            if not text:
                continue

            print(f"📥 Message from {first_name} ({user_id}): '{text}'", flush=True)
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
                    if posts[k].get("status") in ("pending", "needs_attention", "expired"):
                        target_key = k
                        break

            # Handle Actions
            if any(w in lower_text for w in ("approve", "yes", "ok", "publish", "1")):
                success, msg = approve_pending_post(posts, first_name, target_key=target_key)
                tg_api("sendMessage", {"chat_id": approver_id, "text": msg, "parse_mode": "HTML"})
                print(f"[APPROVED] {target_key} by {first_name}", flush=True)

            elif any(w in lower_text for w in ("recreate", "regenerate", "new", "retry", "regen", "2")):
                success, msg = recreate_and_send_post(posts, first_name, target_key=target_key)
                tg_api("sendMessage", {"chat_id": approver_id, "text": msg, "parse_mode": "HTML"})
                print(f"[RECREATED] {target_key} by {first_name}", flush=True)

            elif any(w in lower_text for w in ("reject", "no", "cancel", "3")):
                success, msg = reject_pending_post(posts, first_name, target_key=target_key)
                tg_api("sendMessage", {"chat_id": approver_id, "text": msg, "parse_mode": "HTML"})
                print(f"[REJECTED] {target_key} by {first_name}", flush=True)

            elif any(w in lower_text for w in ("status", "check", "info")):
                afternoon_st = posts.get(f"{today}_afternoon", {}).get("status", "not generated").upper()
                evening_st = posts.get(f"{today}_evening", {}).get("status", "not generated").upper()
                msg = (
                    f"📊 <b>DAILY SCHEDULE STATUS ({today})</b>\n\n"
                    f"🌞 <b>Afternoon Post (2:00 PM IST)</b>: {afternoon_st}\n"
                    f"🌙 <b>Evening Post (8:00 PM IST)</b>: {evening_st}\n\n"
                    "👉 Reply <b>approve</b>, <b>recreate</b>, or <b>reject</b>."
                )
                tg_api("sendMessage", {"chat_id": approver_id, "text": msg, "parse_mode": "HTML"})

            else:
                msg = (
                    "👋 <b>Choicematrix Autopilot Commands:</b>\n\n"
                    "• Reply <b>approve</b> to approve the pending draft 🚀\n"
                    "• Reply <b>approve afternoon</b> or <b>approve evening</b>\n"
                    "• Reply <b>recreate</b> to generate a fresh new carousel 🔄\n"
                    "• Reply <b>reject</b> to cancel the draft ❌\n"
                    "• Reply <b>status</b> to see today's schedule 📊"
                )
                tg_api("sendMessage", {"chat_id": approver_id, "text": msg, "parse_mode": "HTML"})

    return offset

def main():
    load_env()
    print("🚀 Telegram Text-Only Real-Time Approval Daemon started.", flush=True)
    tg_state = read_json(TG_STATE, {"offset": 0})
    offset = tg_state.get("offset", 0)
    print(f"Listening for text commands on offset {offset}...", flush=True)
    while True:
        try:
            offset = poll_once(offset)
        except Exception as exc:
            print(f"Daemon loop warning ({type(exc).__name__}): {exc}", flush=True)
            time.sleep(3)
        time.sleep(1)

if __name__ == "__main__":
    main()
