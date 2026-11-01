"""Real-time Telegram Approval, Interaction & Diagnostics Daemon.
- 100% Interactive with 1-Click Inline Buttons & Pure Text Commands:
    - [✅ Approve] — 1-click approval + instant live publish if scheduled time passed.
    - [🔄 Recreate] — 1-click fresh regeneration with live news & Unsplash visuals.
    - [🎨 Switch Theme] — 1-click cycling through 4 design archetypes & 20 themes.
    - [📊 Status] — 1-click check of daily afternoon & evening schedule.
    - [🩺 Health] — 1-click system diagnostics (CPU, RAM, Disk, Git, Meta API).
    - [❌ Reject] — 1-click rejection.
- Commands supported:
    - '/topic <idea>' — On-demand custom 6-slide carousel generation on any topic.
    - '/theme [cyber|royal|neo|solaris|0-3]' — Switch visual design archetype on the fly.
    - '/diagnose' or 'health' — Complete server, git, and API diagnostics.
    - 'approve afternoon', 'approve evening', 'approve', 'yes', 'ok'
    - 'reject afternoon', 'reject evening', 'reject all', 'cancel'
    - 'recreate afternoon', 'recreate evening', 'recreate', 'new'
    - 'status'
    - 'token' — Meta Graph API token verification.
- Crash-proof 24/7 background loop with auto-reconnect on network timeout.
- Seamless automatic Git state sync to GitHub main.
"""
import os
import re
import json
import time
import requests
import subprocess
import sys
import shutil
import platform
from datetime import datetime, timezone
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent
STATE = ROOT / "state" / "posts.json"
TG_STATE = ROOT / "state" / "telegram.json"
GEN_ERRORS = ROOT / "state" / "generation-attempts.json"

ARCHETYPE_LABELS = {
    0: "⚡ Cyber Grid (Sharp Brackets & Diamonds)",
    1: "👑 Royal Luxury (Double Border & Star Florets)",
    2: "🌿 Minimalist Neo (Smooth Rounded & Notches)",
    3: "☀️ Radiant Solaris (Pill Dots & Triangles)"
}

def load_env():
    env_file = ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ[k.strip().lstrip("\ufeff")] = v.strip()

load_env()

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

MAIN_KEYBOARD = {
    "keyboard": [
        [{"text": "✅ Approve Afternoon"}, {"text": "✅ Approve Evening"}],
        [{"text": "🔄 Recreate Afternoon"}, {"text": "🔄 Recreate Evening"}],
        [{"text": "🎨 Switch Theme"}, {"text": "🩺 Diagnostics"}],
        [{"text": "📊 Check Status"}, {"text": "❌ Reject All"}]
    ],
    "resize_keyboard": True,
    "is_persistent": True
}

def tg_api(method, payload=None):
    token = os.getenv("TG_BOT_TOKEN")
    url = f"https://api.telegram.org/bot{token}/{method}"
    p = payload.copy() if payload else {}
    if method == "sendMessage" and "reply_markup" not in p:
        p["reply_markup"] = MAIN_KEYBOARD
    try:
        r = requests.post(url, json=p, timeout=30)
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
        subprocess.run([git_bin, "-C", str(ROOT), "add", "docs/posts/", "state/posts.json", "state/generation-attempts.json"], timeout=20, capture_output=True)
        subprocess.run([git_bin, "-C", str(ROOT), "commit", "-m", "chore: sync approval state [skip ci]"], timeout=15, capture_output=True)
        subprocess.run([git_bin, "-C", str(ROOT), "push", "origin", "main"], check=True, timeout=25, capture_output=True)
        print("📡 Synced approval state to GitHub main!", flush=True)
    except Exception as exc:
        print(f"Git push state warning: {exc}", flush=True)

LAST_TOKEN_ALERT = 0

def check_meta_token_health(force=False, reply_chat_id=None):
    global LAST_TOKEN_ALERT
    now = time.time()
    if not force and (now - LAST_TOKEN_ALERT < 21600):  # 6 hours debounce
        return True, "OK"
    token = os.getenv("META_PAGE_TOKEN")
    page_id = os.getenv("FB_PAGE_ID")
    if not token or not page_id:
        return False, "META_PAGE_TOKEN or FB_PAGE_ID missing in .env"
    try:
        r = requests.get(
            f"https://graph.facebook.com/v26.0/{page_id}",
            params={"fields": "id,name", "access_token": token},
            timeout=10
        )
        res = r.json()
        if not r.ok or "error" in res:
            err = res.get("error", {})
            msg = err.get("message", "Unknown error")
            LAST_TOKEN_ALERT = now
            alert = (
                "🚨 <b>ACTION REQUIRED: Meta Access Token Expired!</b>\n\n"
                f"<i>Error:</i> {msg[:200]}\n\n"
                "Facebook & Instagram posts cannot publish until refreshed.\n\n"
                "👉 <b>How to refresh in 1 minute:</b>\n"
                "1. Open <a href=\"https://developers.facebook.com/tools/explorer/\">Meta Graph API Explorer</a>\n"
                "2. Under <b>Meta App</b>, select <code>autopost</code>\n"
                "3. Under <b>User or Page</b>, select <code>Choicematrix</code>\n"
                "4. Click <b>Generate Access Token</b>\n"
                "5. Open <a href=\"https://developers.facebook.com/tools/debug/accesstoken/\">Access Token Debugger</a>, paste token, click <b>Extend Access Token</b>\n"
                "6. Update <code>META_PAGE_TOKEN</code> in your VM <code>.env</code>."
            )
            approver_id = reply_chat_id or os.getenv("TG_APPROVER_ID")
            if approver_id:
                tg_api("sendMessage", {"chat_id": approver_id, "text": alert, "parse_mode": "HTML", "disable_web_page_preview": True})
            return False, msg
        return True, f"Token active for Page '{res.get('name')}'"
    except Exception as exc:
        return False, str(exc)

def get_system_diagnostics():
    import autopilot
    posts = read_json(STATE, {})
    today = datetime.now(autopilot.IST).date().isoformat()
    sys_plat = platform.platform()
    
    # Disk
    du = shutil.disk_usage(ROOT)
    total_gb = du.total / (1024**3)
    free_gb = du.free / (1024**3)
    disk_pct = (du.used / du.total) * 100
    
    # Memory
    mem_info = "N/A"
    try:
        if Path("/proc/meminfo").exists():
            mem_data = {}
            for line in Path("/proc/meminfo").read_text().splitlines():
                parts = line.split(":")
                if len(parts) == 2:
                    mem_data[parts[0].strip()] = parts[1].strip()
            total_kb = int(mem_data.get("MemTotal", "0 kB").split()[0])
            avail_kb = int(mem_data.get("MemAvailable", "0 kB").split()[0])
            used_kb = total_kb - avail_kb
            mem_info = f"{used_kb // 1024} MB / {total_kb // 1024} MB ({(used_kb/total_kb)*100:.1f}%)"
    except Exception:
        pass

    # Load avg
    load_avg = "N/A"
    try:
        if hasattr(os, "getloadavg"):
            l1, l5, l15 = os.getloadavg()
            load_avg = f"{l1:.2f}, {l5:.2f}, {l15:.2f}"
    except Exception:
        pass

    # Git status
    git_hash = "unknown"
    git_msg = "unknown"
    try:
        mingit = r"C:\Users\bongo\.mingit\cmd\git.exe"
        git_bin = mingit if Path(mingit).exists() else "git"
        git_hash = subprocess.check_output([git_bin, "rev-parse", "--short", "HEAD"], cwd=str(ROOT), text=True).strip()
        git_msg = subprocess.check_output([git_bin, "log", "-1", "--pretty=%s"], cwd=str(ROOT), text=True).strip()
    except Exception:
        pass

    # Meta Graph Token Health
    meta_ok, meta_msg = check_meta_token_health(force=True)
    meta_status = f"✅ Active ({meta_msg})" if meta_ok else f"⚠️ Alert: {meta_msg[:60]}"

    # Image Cache
    img_dir = ROOT / "docs" / "posts"
    img_count = len(list(img_dir.glob("*.jpg"))) if img_dir.exists() else 0

    # Today's status
    aft_entry = posts.get(f"{today}_afternoon", {})
    aft_status = aft_entry.get("status", "not generated").upper()
    aft_style = ARCHETYPE_LABELS.get(aft_entry.get("design_mode", 0), "Rotational")
    
    eve_entry = posts.get(f"{today}_evening", {})
    eve_status = eve_entry.get("status", "not generated").upper()
    eve_style = ARCHETYPE_LABELS.get(eve_entry.get("design_mode", 0), "Rotational")

    report = (
        "🩺 <b>AUTOPILOT SYSTEM DIAGNOSTICS & HEALTH</b>\n\n"
        f"🖥️ <b>Host Platform:</b> <code>{sys_plat}</code>\n"
        f"⚙️ <b>CPU Load:</b> <code>{load_avg}</code>\n"
        f"💾 <b>RAM Usage:</b> <code>{mem_info}</code>\n"
        f"💽 <b>Disk Space:</b> <code>{free_gb:.1f} GB free</code> / {total_gb:.1f} GB ({disk_pct:.1f}% used)\n"
        f"📦 <b>Git Version:</b> <code>{git_hash}</code> — <i>{git_msg[:35]}</i>\n"
        f"🔑 <b>Meta API:</b> {meta_status}\n"
        f"🖼️ <b>Media Cache:</b> <code>{img_count} high-res slides</code>\n\n"
        f"📅 <b>Schedule Status ({today}):</b>\n"
        f"• 🌞 Afternoon (14:00 IST): <b>{aft_status}</b> [{aft_style[:18]}]\n"
        f"• 🌙 Evening (20:00 IST): <b>{eve_status}</b> [{eve_style[:18]}]\n\n"
        f"⚡ <b>Daemon Engine:</b> 🟢 Active & Listening (PID: {os.getpid()})"
    )
    return report

def approve_pending_post(posts, approver_name="Subha", target_key=None, target_slot=None):
    import autopilot
    today = datetime.now(autopilot.IST).date().isoformat()
    entry = None
    if target_key and target_key in posts:
        entry = posts[target_key]
    else:
        if target_slot and f"{today}_{target_slot}" in posts:
            target_key = f"{today}_{target_slot}"
            entry = posts[target_key]
        else:
            for k in sorted(posts.keys(), reverse=True):
                if k.startswith(today) and posts[k].get("status") in ("pending", "needs_attention", "expired", "rejected"):
                    if target_slot is None or posts[k].get("slot") == target_slot:
                        entry = posts[k]
                        target_key = k
                        break

    if not entry:
        slot_label = f" {target_slot.capitalize()}" if target_slot else ""
        return False, f"⚠️ No draft found to approve for today's{slot_label} post.\n(Reply 'recreate{slot_label.lower()}' to generate one!)"

    entry["status"] = "approved"
    entry["approved_at"] = datetime.now(timezone.utc).isoformat()
    entry["approval_hash"] = autopilot.approval_digest(entry)
    for p in ("fb", "ig", "threads"):
        if entry.get(p, {}).get("status") != "published":
            entry[p] = {"status": "not_started", "id": None, "container": None}
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

def reject_pending_post(posts, approver_name="Subha", target_key=None, target_slot=None):
    import autopilot
    today = datetime.now(autopilot.IST).date().isoformat()
    entry = None
    if target_key and target_key in posts:
        entry = posts[target_key]
    else:
        if target_slot and f"{today}_{target_slot}" in posts:
            target_key = f"{today}_{target_slot}"
            entry = posts[target_key]
        else:
            for k in sorted(posts.keys(), reverse=True):
                if k.startswith(today) and posts[k].get("status") in ("pending", "approved", "needs_attention", "expired"):
                    if target_slot is None or posts[k].get("slot") == target_slot:
                        entry = posts[k]
                        target_key = k
                        break

    if not entry:
        slot_label = f" {target_slot.capitalize()}" if target_slot else ""
        return False, f"⚠️ No draft found to reject for today's{slot_label} post."

    slot_name = entry.get("slot", "post").capitalize()
    if entry.get("status") == "published":
        return False, f"⚠️ {slot_name} has already been published live and cannot be cancelled."

    entry["status"] = "rejected"
    entry["approved_at"] = None
    entry["approval_hash"] = None
    write_json(STATE, posts)
    git_push_state()
    return True, f"❌ <b>{slot_name.upper()} POST REJECTED by {approver_name}.</b>\nThis post will not be published.\n(Reply 'recreate {slot_name.lower()}' if you want a fresh new draft!)"

def recreate_and_send_post(posts=None, approver_name="Subha", target_key=None, target_slot=None):
    import autopilot
    approver_id = str(os.getenv("TG_APPROVER_ID", ""))
    
    if isinstance(posts, str):
        target_key = posts
        posts = None
    if posts is None or not isinstance(posts, dict):
        posts = read_json(STATE, {})

    today = datetime.now(autopilot.IST).date().isoformat()
    if target_key and "_" in target_key:
        today_cand, slot_cand = target_key.split("_", 1)
        today = today_cand
        slot = slot_cand
    elif target_slot:
        slot = target_slot
    else:
        if posts.get(f"{today}_evening", {}).get("status") in ("pending", "needs_attention", "rejected"):
            slot = "evening"
        elif posts.get(f"{today}_afternoon", {}).get("status") in ("pending", "needs_attention", "rejected"):
            slot = "afternoon"
        else:
            now_hour = datetime.now(autopilot.IST).hour
            slot = "evening" if now_hour >= 14 else "afternoon"

    target_key = f"{today}_{slot}"
    entry = posts.get(target_key)
    old_rev = entry.get("revision", 1) if entry else 1
    new_rev = old_rev + 1

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

    settings = read_json(ROOT / "settings.json", {})
    try:
        new_entry = autopilot.create_post(target_key, settings, posts, revision=new_rev, slot=slot)
        write_json(STATE, posts)
        git_push_state()
        return True, f"✨ New {slot.capitalize()} Carousel Revision {new_rev} generated and sent above!\nReply 'approve {slot}' when ready. 🚀"
    except Exception as e:
        return False, f"⚠️ Error regenerating post: {e}"

def switch_theme_and_send_post(posts=None, approver_name="Subha", target_key=None, target_slot=None, mode=None):
    import autopilot
    approver_id = str(os.getenv("TG_APPROVER_ID", ""))
    if posts is None or not isinstance(posts, dict):
        posts = read_json(STATE, {})
    
    today = datetime.now(autopilot.IST).date().isoformat()
    if target_key and "_" in target_key:
        today_cand, slot_cand = target_key.split("_", 1)
        today = today_cand
        slot = slot_cand
    elif target_slot:
        slot = target_slot
    else:
        if f"{today}_evening" in posts and posts[f"{today}_evening"].get("status") in ("pending", "needs_attention", "approved"):
            slot = "evening"
        else:
            slot = "afternoon"

    target_key = f"{today}_{slot}"
    entry = posts.get(target_key)
    if not entry or not entry.get("slides"):
        return False, f"⚠️ No active draft found for {slot.capitalize()} to switch theme.\nReply 'recreate {slot}' or '/topic' to create one!"

    current_mode = entry.get("design_mode", 0)
    if mode is not None:
        try:
            new_mode = int(mode) % 4
        except Exception:
            new_mode = (current_mode + 1) % 4
    else:
        new_mode = (current_mode + 1) % 4

    style_label = ARCHETYPE_LABELS.get(new_mode, f"Archetype {new_mode}")

    tg_api("sendMessage", {
        "chat_id": approver_id,
        "text": f"🎨 <b>SWITCHING THEME TO: {style_label}</b>\nRe-rendering slides with atmospheric background and vector accents... ⏳",
        "parse_mode": "HTML"
    })

    settings = read_json(ROOT / "settings.json", {})
    try:
        images = autopilot.render_carousel_slides(
            date_key=entry.get("clean_date", today),
            revision=entry.get("revision", 1),
            settings=settings,
            item=entry,
            slot=slot,
            forced_design_mode=new_mode
        )
        if images:
            entry["images"] = images
            entry["primary_image"] = images[0]
            entry["design_mode"] = new_mode
            write_json(STATE, posts)
            git_push_state()
            autopilot.send_preview(entry, settings)
            return True, f"✨ Switched {slot.capitalize()} to {style_label}! Check preview above."
        else:
            return False, "⚠️ Render returned no images."
    except Exception as exc:
        return False, f"⚠️ Error switching theme: {exc}"

def create_custom_topic_post(topic_text, slot=None, approver_name="Subha"):
    import autopilot
    approver_id = str(os.getenv("TG_APPROVER_ID", ""))
    posts = read_json(STATE, {})
    today = datetime.now(autopilot.IST).date().isoformat()

    if not slot:
        has_indic = bool(re.search(r'[\u0980-\u09FF\u0900-\u097F]', topic_text))
        slot = "evening" if has_indic else "afternoon"

    target_key = f"{today}_{slot}"
    old_rev = posts.get(target_key, {}).get("revision", 0)
    new_rev = old_rev + 1

    slot_label = "🌞 AFTERNOON (English)" if slot == "afternoon" else "🌙 EVENING (Regional)"
    tg_api("sendMessage", {
        "chat_id": approver_id,
        "text": (
            f"🧠 <b>GENERATING CUSTOM TOPIC POST</b>\n\n"
            f"📌 <b>Topic:</b> <i>{topic_text}</i>\n"
            f"🌐 <b>Slot:</b> {slot_label}\n"
            f"🔄 <b>Revision:</b> {new_rev}\n\n"
            "Generating 6-slide carousel with AI content & Unsplash visual cards... ⏳ (15-20s)"
        ),
        "parse_mode": "HTML"
    })

    gen_errors = read_json(GEN_ERRORS, {})
    gen_errors.pop(target_key, None)
    write_json(GEN_ERRORS, gen_errors)

    settings = read_json(ROOT / "settings.json", {})
    try:
        new_entry = autopilot.create_post(target_key, settings, posts, revision=new_rev, slot=slot, custom_topic=topic_text)
        write_json(STATE, posts)
        git_push_state()
        return True, f"✨ Custom Topic Carousel generated and sent above!\nClick [✅ Approve {slot.capitalize()}] when ready. 🚀"
    except Exception as exc:
        return False, f"⚠️ Error creating custom topic post: {exc}"

def poll_once(offset=0):
    token = os.getenv("TG_BOT_TOKEN")
    approver_id = str(os.getenv("TG_APPROVER_ID", "")).strip()

    try:
        r = requests.get(
            f"https://api.telegram.org/bot{token}/getUpdates",
            params={"offset": offset, "timeout": 10, "allowed_updates": json.dumps(["message", "callback_query"])},
            timeout=25
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

        # 1. Handle Inline Callback Queries (1-Click Action Buttons!)
        if "callback_query" in u:
            cq = u["callback_query"]
            cq_id = cq["id"]
            cq_data = cq.get("data", "")
            cq_from = cq.get("from", {})
            user_id = str(cq_from.get("id", ""))
            first_name = cq_from.get("first_name", "Subha")
            
            # Dismiss Telegram loading spinner immediately
            tg_api("answerCallbackQuery", {"callback_query_id": cq_id, "text": f"Processing {cq_data}..."})
            print(f"🔘 Callback Query from {first_name} ({user_id}): '{cq_data}'", flush=True)

            import autopilot
            today = datetime.now(autopilot.IST).date().isoformat()

            if cq_data.startswith("approve:"):
                slot = cq_data.split(":", 1)[1]
                target_key = f"{today}_{slot}"
                success, msg = approve_pending_post(posts, first_name, target_key=target_key, target_slot=slot)
                tg_api("sendMessage", {"chat_id": approver_id, "text": msg, "parse_mode": "HTML"})
            
            elif cq_data.startswith("recreate:"):
                slot = cq_data.split(":", 1)[1]
                target_key = f"{today}_{slot}"
                success, msg = recreate_and_send_post(posts, first_name, target_key=target_key, target_slot=slot)
                tg_api("sendMessage", {"chat_id": approver_id, "text": msg, "parse_mode": "HTML"})

            elif cq_data.startswith("theme:"):
                slot = cq_data.split(":", 1)[1]
                target_key = f"{today}_{slot}"
                success, msg = switch_theme_and_send_post(posts, first_name, target_key=target_key, target_slot=slot)
                tg_api("sendMessage", {"chat_id": approver_id, "text": msg, "parse_mode": "HTML"})

            elif cq_data.startswith("reject:"):
                slot = cq_data.split(":", 1)[1]
                target_key = f"{today}_{slot}"
                success, msg = reject_pending_post(posts, first_name, target_key=target_key, target_slot=slot)
                tg_api("sendMessage", {"chat_id": approver_id, "text": msg, "parse_mode": "HTML"})

            elif cq_data == "status":
                afternoon_st = posts.get(f"{today}_afternoon", {}).get("status", "not generated").upper()
                evening_st = posts.get(f"{today}_evening", {}).get("status", "not generated").upper()
                msg = (
                    f"📊 <b>DAILY SCHEDULE STATUS ({today})</b>\n\n"
                    f"🌞 <b>Afternoon Post (2:00 PM IST)</b>: {afternoon_st}\n"
                    f"🌙 <b>Evening Post (8:00 PM IST)</b>: {evening_st}\n\n"
                    "👉 Tap inline buttons or reply 'approve afternoon' / 'approve evening'"
                )
                tg_api("sendMessage", {"chat_id": approver_id, "text": msg, "parse_mode": "HTML"})

            elif cq_data in ("diagnose", "health"):
                diag_msg = get_system_diagnostics()
                tg_api("sendMessage", {"chat_id": approver_id, "text": diag_msg, "parse_mode": "HTML"})
            continue

        # 2. Handle Text Messages & Slash Commands
        if "message" in u:
            m = u["message"]
            user_id = str(m.get("from", {}).get("id", "")).strip()
            first_name = m.get("from", {}).get("first_name", "Subha")
            text = m.get("text", "").strip()
            if not text:
                continue

            print(f"📥 Message from {first_name} ({user_id}): '{text}'", flush=True)
            lower_text = text.lower()
            import autopilot
            today = datetime.now(autopilot.IST).date().isoformat()

            # Identify target slot
            target_slot = None
            if any(w in lower_text for w in ("afternoon", "after", "2pm", "day", "aft")):
                target_slot = "afternoon"
            elif any(w in lower_text for w in ("evening", "evenig", "eve", "8pm", "evng", "night")):
                target_slot = "evening"

            target_key = f"{today}_{target_slot}" if target_slot else None

            # Custom Topic Command: /topic <topic> or topic: <topic>
            if lower_text.startswith(("/topic", "topic:", "custom:")) or (lower_text.startswith("topic ") and not any(w in lower_text for w in ("approve", "reject", "recreate"))):
                raw = text
                for pfx in ("/topic", "topic:", "custom:", "topic"):
                    if raw.lower().startswith(pfx):
                        raw = raw[len(pfx):].strip()
                        break
                slot = None
                if raw.lower().startswith(("afternoon ", "after ")):
                    slot = "afternoon"
                    raw = raw.split(" ", 1)[1].strip()
                elif raw.lower().startswith(("evening ", "eve ")):
                    slot = "evening"
                    raw = raw.split(" ", 1)[1].strip()
                
                if not raw:
                    tg_api("sendMessage", {"chat_id": approver_id, "text": "⚠️ Please provide a topic! Example:\n<code>/topic AI Agents Revolution 2026</code>\n<code>/topic সুন্দরবনের রয়েল বেঙ্গল টাইগার</code>", "parse_mode": "HTML"})
                else:
                    success, msg = create_custom_topic_post(raw, slot=slot, approver_name=first_name)
                    tg_api("sendMessage", {"chat_id": approver_id, "text": msg, "parse_mode": "HTML"})

            # Theme Switcher Command: /theme or theme [cyber|royal|neo|solaris|0-3]
            elif lower_text.startswith(("/theme", "theme", "switch theme")):
                target_mode = None
                if "cyber" in lower_text or " 0" in lower_text:
                    target_mode = 0
                elif "royal" in lower_text or " 1" in lower_text:
                    target_mode = 1
                elif "neo" in lower_text or "minimal" in lower_text or " 2" in lower_text:
                    target_mode = 2
                elif "solaris" in lower_text or "radiant" in lower_text or " 3" in lower_text:
                    target_mode = 3
                
                success, msg = switch_theme_and_send_post(posts, first_name, target_key=target_key, target_slot=target_slot, mode=target_mode)
                tg_api("sendMessage", {"chat_id": approver_id, "text": msg, "parse_mode": "HTML"})

            # System Diagnostics / Health Command
            elif any(w in lower_text for w in ("/diagnose", "diagnose", "diagnostics", "health", "system", "/health")):
                diag_msg = get_system_diagnostics()
                tg_api("sendMessage", {"chat_id": approver_id, "text": diag_msg, "parse_mode": "HTML"})

            # Reject All / Cancel All
            elif any(w in lower_text for w in ("reject all", "reject both", "cancel all", "cancel both")):
                rej_count = 0
                for s in ("afternoon", "evening"):
                    sk = f"{today}_{s}"
                    if sk in posts and posts[sk].get("status") in ("pending", "approved", "needs_attention", "expired", "rejected"):
                        posts[sk]["status"] = "rejected"
                        posts[sk]["approved_at"] = None
                        posts[sk]["approval_hash"] = None
                        rej_count += 1
                if rej_count > 0:
                    write_json(STATE, posts)
                    git_push_state()
                    msg = f"❌ <b>BOTH AFTERNOON & EVENING POSTS REJECTED by {first_name}.</b>\nNeither post will be published today."
                else:
                    msg = "⚠️ No active drafts found to reject for today."
                tg_api("sendMessage", {"chat_id": approver_id, "text": msg, "parse_mode": "HTML"})

            # Approve Single Slot
            elif any(w in lower_text for w in ("approve", "publish", "accept", "✅")) or re.search(r'\b(yes|ok|1)\b', lower_text):
                success, msg = approve_pending_post(posts, first_name, target_key=target_key, target_slot=target_slot)
                tg_api("sendMessage", {"chat_id": approver_id, "text": msg, "parse_mode": "HTML"})

            # Reject Single Slot
            elif any(w in lower_text for w in ("reject", "cancel", "decline", "❌")) or re.search(r'\b(no|3)\b', lower_text):
                success, msg = reject_pending_post(posts, first_name, target_key=target_key, target_slot=target_slot)
                tg_api("sendMessage", {"chat_id": approver_id, "text": msg, "parse_mode": "HTML"})

            # Recreate Single Slot
            elif any(w in lower_text for w in ("recreate", "regenerate", "retry", "regen", "🔄")) or re.search(r'\b(new|2)\b', lower_text):
                success, msg = recreate_and_send_post(posts, first_name, target_key=target_key, target_slot=target_slot)
                tg_api("sendMessage", {"chat_id": approver_id, "text": msg, "parse_mode": "HTML"})

            # Check Status
            elif any(w in lower_text for w in ("status", "check", "info")):
                afternoon_st = posts.get(f"{today}_afternoon", {}).get("status", "not generated").upper()
                evening_st = posts.get(f"{today}_evening", {}).get("status", "not generated").upper()
                msg = (
                    f"📊 <b>DAILY SCHEDULE STATUS ({today})</b>\n\n"
                    f"🌞 <b>Afternoon Post (2:00 PM IST)</b>: {afternoon_st}\n"
                    f"🌙 <b>Evening Post (8:00 PM IST)</b>: {evening_st}\n\n"
                    "👉 Tap inline buttons or reply 'approve afternoon' / 'approve evening'"
                )
                tg_api("sendMessage", {"chat_id": approver_id, "text": msg, "parse_mode": "HTML"})

            # Check Meta Token Status
            elif any(w in lower_text for w in ("token", "meta token", "key", "keys")):
                ok, detail = check_meta_token_health(force=True, reply_chat_id=approver_id)
                if ok:
                    tg_api("sendMessage", {"chat_id": approver_id, "text": f"✅ <b>Meta Page Token is ACTIVE & VALID!</b>\n\n{detail}", "parse_mode": "HTML"})

            # Help / Commands Guide
            else:
                msg = (
                    "👋 <b>Choicematrix Autopilot Tools & Commands:</b>\n\n"
                    "🔘 <b>1-Click Action Buttons:</b>\n"
                    "• Tap inline buttons below any preview to Approve, Recreate, Switch Theme, or check Status!\n\n"
                    "🧠 <b>On-Demand Custom Carousel:</b>\n"
                    "• <code>/topic &lt;topic&gt;</code> — Generate instant 6-slide carousel!\n"
                    "  <i>Example:</i> <code>/topic AI Breakthroughs 2026</code>\n\n"
                    "🎨 <b>Visual Archetype Switcher:</b>\n"
                    "• <code>/theme [cyber|royal|neo|solaris]</code> — Cycle through 4 design modes & 20 themes!\n\n"
                    "🩺 <b>Diagnostics & Monitoring:</b>\n"
                    "• <code>/diagnose</code> — Live CPU, RAM, disk, Git & Meta API status\n"
                    "• <code>token</code> — Check Meta Facebook/Instagram token health\n"
                    "• <code>status</code> — View daily schedule & approval status\n\n"
                    "💬 <b>Quick Text Commands:</b>\n"
                    "• <code>approve afternoon</code> / <code>approve evening</code>\n"
                    "• <code>recreate afternoon</code> / <code>recreate evening</code>\n"
                    "• <code>reject all</code>"
                )
                tg_api("sendMessage", {"chat_id": approver_id, "text": msg, "parse_mode": "HTML"})

    return offset

def check_and_publish_due_posts():
    try:
        import autopilot
        settings = read_json(ROOT / "settings.json", {})
        if not settings.get("publishing_enabled", False):
            return
        mingit = r"C:\Users\bongo\.mingit\cmd\git.exe"
        git_bin = mingit if Path(mingit).exists() else "git"
        subprocess.run([git_bin, "-C", str(ROOT), "pull", "--rebase", "origin", "main"],
                       check=False, timeout=20, capture_output=True)
        posts = read_json(STATE, {})
        now_ist = datetime.now(autopilot.IST)
        has_due = False
        for date_key, entry in sorted(posts.items()):
            if entry.get("status") in ("approved", "publishing") and entry.get("approved_at"):
                try:
                    if autopilot.due(entry, settings, now_ist) and autopilot.eligible_for_publication(entry, settings, now_ist):
                        has_due = True
                        break
                except Exception:
                    continue
        if has_due:
            print("🚀 Daemon detected approved post due for publication! Executing publish...", flush=True)
            autopilot.process_publications(posts, settings, now_ist)
            write_json(STATE, posts)
            git_push_state()
    except Exception as exc:
        print(f"Daemon publish check warning: {exc}", flush=True)

def main():
    load_env()
    print("🚀 Telegram Interactive & Real-Time Approval Daemon started.", flush=True)
    tg_state = read_json(TG_STATE, {"offset": 0})
    offset = tg_state.get("offset", 0)
    print(f"Listening for inline buttons and text commands on offset {offset}...", flush=True)
    last_health_check = 0
    last_due_check = 0
    while True:
        try:
            now_ts = time.time()
            if now_ts - last_health_check > 14400:  # Check every 4 hours
                last_health_check = now_ts
                check_meta_token_health(force=False)
            if now_ts - last_due_check > 30:  # Check for due approved posts every 30s
                last_due_check = now_ts
                check_and_publish_due_posts()
            offset = poll_once(offset)
        except Exception as exc:
            print(f"Daemon loop warning ({type(exc).__name__}): {exc}", flush=True)
            time.sleep(3)
        time.sleep(1)

if __name__ == "__main__":
    main()
