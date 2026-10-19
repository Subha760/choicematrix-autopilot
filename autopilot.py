"""No-cost, approval-gated Facebook Page + Instagram photo publisher.

GitHub Actions runs this periodically. All credentials come from Actions secrets.
Only original text and locally drawn, people-free JPEG artwork are generated.
"""
from __future__ import annotations

import hashlib
import json
import os
import random
import re
import subprocess
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent
STATE = ROOT / "state" / "posts.json"
TG_STATE = ROOT / "state" / "telegram.json"
GEN_ERRORS = ROOT / "state" / "generation-attempts.json"
CALENDAR = ROOT / "content_calendar.json"
IMAGE_DIR = ROOT / "docs" / "posts"
try:
    IST = ZoneInfo("Asia/Kolkata")
except Exception:
    IST = timezone(timedelta(hours=5, minutes=30), name="IST")
TIMEOUT = 20


def read_json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)


def getenv(name, required=False):
    value = os.getenv(name, "").strip()
    if required and not value:
        raise ValueError(f"Missing {name}. Set it in GitHub Actions secrets/variables.")
    return value


def telegram(method, data=None, files=None):
    token = getenv("TG_BOT_TOKEN", True)
    url = f"https://api.telegram.org/bot{token}/{method}"
    try:
        response = requests.post(url, data=data or {}, files=files, timeout=TIMEOUT)
        response.raise_for_status()
        body = response.json()
        if not body.get("ok"):
            raise RuntimeError(f"Telegram {method}: {body.get('description', 'unknown error')}")
        return body["result"]
    except requests.RequestException as exc:
        raise RuntimeError(f"Telegram {method} failed: {type(exc).__name__}") from exc


def notify(message):
    if getenv("TG_BOT_TOKEN") and getenv("TG_APPROVER_ID"):
        telegram("sendMessage", {"chat_id": getenv("TG_APPROVER_ID"), "text": message[:4000]})
    else:
        print("NOTICE", message[:500])


def safe_notify(message):
    try:
        notify(message)
    except Exception as exc:
        print(f"Could not send Telegram notification: {type(exc).__name__}")


def choose_topic(date_key, revision=1):
    """Select a consistent weekday pillar and rotate licensed-free, curated facts."""
    day = date.fromisoformat(date_key)
    plan = read_json(CALENDAR, {})
    days = plan.get("weekdays", [])
    if len(days) != 7:
        raise RuntimeError("Mixed content calendar requires exactly seven weekday entries")
    slot = dict(days[day.weekday()])
    if slot["type"] not in ("practical", "sourced"):
        raise RuntimeError("Unknown calendar content type")
    slot["angle"] = [
        "a concrete beginner example", "one easy-to-miss detail", "a useful quick checklist",
        "a small hands-on exercise", "a thoughtful question and answer", "one simple explanation",
        "a practical mini-challenge", "an everyday analogy"
    ][(day.toordinal() // 7 + revision - 1) % 8]
    slot["source"] = None
    if slot["type"] == "sourced":
        seeds = plan.get("source_bank", {}).get(slot["category"], [])
        if not seeds:
            raise RuntimeError(f"No curated source facts for {slot['category']}")
        seed = seeds[(day.toordinal() // 7 + revision - 1) % len(seeds)]
        if not seed.get("fact") or not seed.get("url", "").startswith("https://"):
            raise RuntimeError("Invalid curated fact: missing content or HTTPS source")
        slot["seed"] = seed["fact"]
        slot["source"] = {"label": seed["label"], "url": seed["url"]}
    return slot


def validate_ai_copy(item, posts, slot):
    """Deterministic quality guard; not a substitute for fact/legal review."""
    if not isinstance(item, dict):
        raise RuntimeError("AI returned something other than a JSON object")
    for field, limit in (("title", 58), ("subtitle", 115), ("caption", 650)):
        value = item.get(field)
        if not isinstance(value, str) or not value.strip() or len(value) > limit:
            raise RuntimeError(f"AI content failed length/empty check: {field}")
        item[field] = value.strip()
    if not (3 <= len(item["title"].split()) <= 9):
        raise RuntimeError("Visual headline must have 3–9 words")
    content = f"{item['title']} {item['subtitle']} {item['caption']}".lower()
    # Prevent unattributed quotations, made-up research/news, spam and unapproved URLs.
    if re.search(r'https?://|www\.|@[a-z0-9_]+|[“”\"«»]', content, re.I):
        raise RuntimeError("No quoted/attributed content, external links or @handles in generated copy")
    if re.search(r'\b(latest news|breaking news|new study|according to (a )?study|scientists say|guaranteed|100%|proven cure|financial advice|as an ai|buy now|limited offer)\b', content, re.I):
        raise RuntimeError("Unsupported claims, news, or marketing language blocked")
    if re.search(r'\b\d+(?:[.,]\d+)?\s*(?:%|percent|million|billion|times)(?!\w)', content, re.I):
        raise RuntimeError("Unreviewed statistics or quantitative claims blocked")
    hashtags = re.findall(r'(?<!\w)#[a-z][a-z0-9_]*', item["caption"], re.I)
    if not 3 <= len(hashtags) <= 5:
        raise RuntimeError("Captions require 3–5 relevant hashtags")
    recent = list(posts.values())[-90:]
    norm_title = normalize(item["title"])
    norm_caption = normalize(item["caption"])
    if any(similar(norm_title, normalize(p.get("title", ""))) or
           similar(norm_caption, normalize(p.get("caption", ""))) for p in recent):
        raise RuntimeError("Too close to a previous title/caption; no new preview sent")
    if slot["type"] == "sourced" and not slot.get("source"):
        raise RuntimeError("Factual posts must use a source selected by code, never AI")
    return item


def generated_content(settings, posts, date_key, revision=1):
    key = getenv("GEMINI_API_KEY", True)
    slot = choose_topic(date_key, revision)
    recent = [p.get("title", "") for _, p in sorted(posts.items())[-60:]]
    source_rule = (
        "FACT ANCHOR from a curated institutional source: " + slot["seed"] + "\n"
        "Paraphrase ONLY this known fact. Do not extend it with additional claims. "
        "The citation URL is appended by the system; DO NOT generate sources or source names."
        if slot["type"] == "sourced" else
        "Offer your own original suggestion; make it clearly advice, not an externally verified fact."
    )
    prompt = f"""Write ONE wholly original social-media post for a friendly, educational mixed-topic page.
Brand: {settings['brand']}.
Today: {date_key}. WEEKDAY THEME: {slot['category']}.
Specific direction: {slot['direction']}
Presentation angle: {slot['angle']}.
Audience: {settings['audience']}; language: {settings['language']}.
Style: {settings['content_style']}.
{source_rule}
Previous headlines that MUST NOT be repeated: {json.dumps(recent)}.
Return ONLY one JSON object containing exactly these strings:
  title: strong 3–9-word headline, maximum 58 characters,
  subtitle: one concrete explanation or action, maximum 115 characters,
  caption: 2–4 friendly, accurate sentences (max 650 characters TOTAL), followed by 3–5 genuine relevant hashtags.
No external URLs, @handles, quotations, borrowed catchphrases, lyrics, slogans or copied passages.
No named private individuals, celebrity likenesses, brands, unverifiable statistics, or current events.
No politics, religion advocacy, diagnoses, treatment, investment tips, or harmful DIY steps.
No promises, research citations, medical/financial assertions, fake testimonials or promotional bait.
No people photos; this post will be typeset on a code-generated abstract geometric artwork.
Avoid AI-sounding filler. Make the idea interesting and genuinely useful, clear, and modest."""
    model = getenv("GEMINI_MODEL") or "gemini-2.5-flash"
    if not re.fullmatch(r"[a-zA-Z0-9_.-]+", model):
        raise ValueError("Invalid model identifier")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"responseMimeType": "application/json", "temperature": 0.75},
    }
    response = requests.post(url, headers={"x-goog-api-key": key}, json=body, timeout=45)
    if response.status_code != 200:
        raise RuntimeError(f"Gemini draft HTTP {response.status_code}; fail closed")
    parts = response.json().get("candidates", [{}])[0].get("content", {}).get("parts", [])
    if not parts:
        raise RuntimeError("AI returned no text; no post generated")
    item = json.loads("".join(part.get("text", "") for part in parts))
    item = validate_ai_copy(item, posts, slot)
    item["category"] = slot["category"]
    item["content_type"] = slot["type"]
    item["source"] = slot.get("source")
    # Label the provenance of source-backed facts in the public caption.
    if slot["source"]:
        item["caption"] += f"\n\nReference: {slot['source']['label']} — {slot['source']['url']}"
    return item


def approval_digest(entry):
    # A change to the approved text, image reference or provenance invalidates approval.
    keys = ("date", "revision", "title", "subtitle", "caption", "category", "content_type", "source", "image", "image_sha256")
    data = {k: entry.get(k) for k in keys}
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def eligible_for_publication(entry, settings, now):
    if settings.get("approval_required") is not True:
        return False
    if entry.get("status") not in ("approved", "publishing") or not entry.get("approved_at"):
        return False
    if not entry.get("approval_hash") or entry["approval_hash"] != approval_digest(entry):
        return False
    if not entry.get("image_sha256"):
        return False
    try:
        image_path = (ROOT / "docs" / entry["image"]).resolve(strict=True)
        if not image_path.is_relative_to((ROOT / "docs" / "posts").resolve()):
            return False
        if hashlib.sha256(image_path.read_bytes()).hexdigest() != entry["image_sha256"]:
            return False
    except (OSError, KeyError, ValueError):
        return False
    if entry.get("content_type") not in ("sourced", "practical"):
        return False
    if settings.get("require_source_for_facts", True) and entry["content_type"] == "sourced":
        if not entry.get("source") or not entry["source"].get("url", "").startswith("https://"):
            return False
    if post_is_stale(entry, settings, now):
        return False
    try:
        approved_at = datetime.fromisoformat(entry["approved_at"]).astimezone(IST)
    except (TypeError, ValueError):
        return False
    return approved_at <= now and due(entry, settings, now)


def post_is_stale(entry, settings, now):
    try:
        date_started = datetime.fromisoformat(entry["date"] + "T00:00:00").replace(tzinfo=IST)
        return now - date_started >= timedelta(hours=int(settings.get("max_post_age_hours", 48)))
    except (TypeError, ValueError):
        return True


def expire_old_posts(posts, settings, now):
    for entry in posts.values():
        if entry.get("status") in ("pending", "approved") and post_is_stale(entry, settings, now):
            entry["status"] = "expired"
            entry["approved_at"] = None
            entry["approval_hash"] = None


def normalize(value):
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def similar(a, b):
    # Exact and high-overlap title matches are blocked, preserving topic diversity.
    import difflib
    return bool(a and b and (a == b or difflib.SequenceMatcher(None, a, b).ratio() > 0.82))


def font(size, bold=True):
    families = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
        "C:\\Windows\\Fonts\\arialbd.ttf" if bold else "C:\\Windows\\Fonts\\arial.ttf",
        "C:\\Windows\\Fonts\\segoeuib.ttf" if bold else "C:\\Windows\\Fonts\\segoeui.ttf",
    ]
    for item in families:
        if Path(item).exists():
            return ImageFont.truetype(item, size)
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def wrap_by_pixels(draw, words, f, max_width):
    lines, line = [], ""
    for word in words.split():
        candidate = (line + " " + word).strip()
        if draw.textbbox((0, 0), candidate, font=f)[2] <= max_width:
            line = candidate
        else:
            if line:
                lines.append(line)
            line = word
    if line:
        lines.append(line)
    return lines


def image_art(date_key, revision, settings, item):
    """Draw original abstract illustrations and typography; JPEG is IG compliant."""
    W, H = 1080, 1350
    image = Image.new("RGB", (W, H))
    draw = ImageDraw.Draw(image)
    rnd = random.Random(int(hashlib.sha256(f"{date_key}-{revision}-{item['title']}".encode()).hexdigest()[:12], 16))
    schemes = {
        "Productivity": ((24, 44, 76), (40, 65, 99), (255, 196, 117)),
        "Digital Safety": ((20, 51, 65), (31, 88, 94), (155, 232, 207)),
        "Science & Nature": ((26, 45, 58), (39, 85, 85), (172, 220, 156)),
        "Everyday Life": ((65, 37, 60), (105, 62, 90), (255, 208, 185)),
        "Sustainable Living": ((33, 61, 48), (53, 94, 75), (207, 232, 146)),
        "Creativity & DIY": ((69, 39, 74), (100, 67, 105), (246, 188, 224)),
        "Culture & Learning": ((61, 44, 48), (102, 73, 73), (255, 209, 163)),
    }
    top, bottom, accent = schemes.get(item.get("category"), schemes["Productivity"])
    for y in range(H):
        ratio = y / H
        c = tuple(int(top[i]*(1-ratio) + bottom[i]*ratio) for i in range(3))
        draw.line((0, y, W, y), fill=c)
    # Abstract artwork only: no people, photos, trademarked imagery, or third-party graphics.
    for _ in range(15):
        x, y = rnd.randrange(-160, W+160), rnd.randrange(-160, H+160)
        r = rnd.randrange(20, 145)
        tone = tuple(min(255, int(v * rnd.uniform(0.42, 0.77))) for v in accent)
        draw.ellipse((x-r, y-r, x+r, y+r), outline=tone, width=rnd.randrange(2, 6))
    draw.rounded_rectangle((68, 73, W-68, H-79), radius=50, fill=(*top,))
    draw.rounded_rectangle((110, 116, 110+365, 178), radius=28, fill=accent)
    draw.text((138, 132), settings["brand"].upper()[:24], font=font(24), fill=top)
    category = item.get("category", "IDEAS").upper()[:28]
    catfont = font(21)
    # Small themed label; avoids overlap with the brand pill on longer category names.
    draw.text((113, 204), category, font=catfont, fill=accent)
    draw.line((112, 264, W-112, 264), fill=accent, width=4)
    title = item["title"].strip()
    for size in range(76, 38, -2):
        f = font(size)
        lines = wrap_by_pixels(draw, title, f, 840)
        if len(lines) <= 4:
            break
    if len(lines) > 4 or any(draw.textbbox((0, 0), line, font=f)[2] > 840 for line in lines):
        raise RuntimeError("Headline cannot fit safely on artwork")
    y = 340
    for line in lines:
        draw.text((116, y), line, font=f, fill=(255, 254, 250))
        y += size + 32
    y += 35
    draw.rounded_rectangle((112, y, W-112, y+7), radius=4, fill=accent)
    y += 75
    # Responsive font choice prevents clipping of long (but validated) explanations.
    max_subtitle_h = H - 240 - y
    if max_subtitle_h <= 0:
        raise RuntimeError("Title exceeds safe artwork area")
    for subtitle_size in (43, 39, 35, 31, 27):
        f2 = font(subtitle_size, bold=False)
        sublines = wrap_by_pixels(draw, item["subtitle"], f2, 825)
        if (subtitle_size + 18) * len(sublines) <= max_subtitle_h and len(sublines) <= 5:
            break
    else:
        raise RuntimeError("Subtitle is too long for safe card typography")
    for line in sublines:
        draw.text((115, y), line, font=f2, fill=(234, 235, 234))
        y += subtitle_size + 18
    draw.text((114, H-180), "A NEW IDEA, EACH DAY", font=font(24), fill=accent)
    IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    destination = IMAGE_DIR / f"{date_key}-v{revision}.jpg"
    image.save(destination, "JPEG", quality=89, optimize=True)
    return destination.relative_to(ROOT / "docs").as_posix()


def public_url(relative_image_path):
    base = getenv("PUBLIC_BASE_URL")
    if not base:
        full = getenv("GITHUB_REPOSITORY", True)
        owner, repo = full.split("/", 1)
        base = f"https://{owner}.github.io" + ("" if repo.lower() == f"{owner.lower()}.github.io" else f"/{repo}")
    return base.rstrip("/") + "/" + relative_image_path


def create_post(date_key, settings, posts, revision=1):
    # Fail closed: do not replace approved content, except on an explicitly chosen regeneration.
    content = generated_content(settings, posts, date_key, revision)
    image = image_art(date_key, revision, settings, content)
    entry = {
        "date": date_key, "revision": revision, "title": content["title"],
        "subtitle": content["subtitle"], "caption": content["caption"],
        "category": content["category"], "content_type": content["content_type"],
        "source": content["source"], "image": image,
        "image_sha256": hashlib.sha256((ROOT / "docs" / image).read_bytes()).hexdigest(),
        "status": "pending",
        "approved_at": None, "approval_hash": None,
        "fb": {"status": "not_started", "id": None},
        "ig": {"status": "not_started", "id": None, "container": None},
        "threads": {"status": "not_started", "id": None, "container": None},
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    posts[date_key] = entry
    try:
        send_preview(entry, settings)
    except Exception:
        # If Telegram did not accept the preview, allow a clean retry next run.
        del posts[date_key]
        raise
    return entry


def send_preview(post, settings):
    date_key, revision = post["date"], post["revision"]
    markup = {"inline_keyboard": [
        [{"text": "✅ Approve", "callback_data": f"approve:{date_key}:{revision}"},
         {"text": "🔄 Regenerate", "callback_data": f"regen:{date_key}:{revision}"}],
        [{"text": "❌ Reject", "callback_data": f"reject:{date_key}:{revision}"}]
    ]}
    chat = getenv("TG_APPROVER_ID", True)
    preview_label = f"DAILY DRAFT • {date_key} • {post.get('category', 'Ideas')}"
    file = ROOT / "docs" / post["image"]
    with file.open("rb") as image_file:
        telegram("sendPhoto", {"chat_id": chat, "caption": preview_label}, {"photo": image_file})
    # Approval buttons are on the COMPLETE caption, not a truncated photo caption.
    full_text = (f"TEXT TO PUBLISH ON BOTH PLATFORMS\n\n{post['caption']}\n\n"
                 f"Scheduled at/after {settings['publish_time_ist']} IST.\n"
                 "Only your approval allows posting. If you ignore it, nothing publishes.")
    telegram("sendMessage", {"chat_id": chat, "text": full_text[:4000],
                             "reply_markup": json.dumps(markup)})


def take_approvals(posts, settings, now=None):
    if now is None:
        now = datetime.now(IST)
    tg = read_json(TG_STATE, {"offset": 0})
    updates = telegram("getUpdates", {"offset": tg["offset"], "timeout": 0,
                                       "allowed_updates": json.dumps(["callback_query"])})
    for update in updates:
        tg["offset"] = max(tg["offset"], int(update["update_id"]) + 1)
        callback = update.get("callback_query")
        if not callback:
            continue
        callback_id = callback.get("id", "")
        user_id = str(callback.get("from", {}).get("id", ""))
        if user_id != getenv("TG_APPROVER_ID", True):
            telegram("answerCallbackQuery", {"callback_query_id": callback_id,
                                            "text": "Only the account owner can approve."})
            continue
        action, sep, rest = callback.get("data", "").partition(":")
        fields = rest.split(":") if sep else []
        if len(fields) != 2:
            telegram("answerCallbackQuery", {"callback_query_id": callback_id, "text": "Invalid action"})
            continue
        date_key, revision = fields
        entry = posts.get(date_key)
        if not entry or str(entry["revision"]) != revision or entry["status"] != "pending":
            telegram("answerCallbackQuery", {"callback_query_id": callback_id,
                                            "text": "This preview has expired."})
            continue
        if action == "approve":
            if post_is_stale(entry, settings, now):
                entry["status"] = "expired"
                answer = "This preview is too old; nothing will publish."
            else:
                entry["status"] = "approved"
                entry["approved_at"] = datetime.now(timezone.utc).isoformat()
                entry["approval_hash"] = approval_digest(entry)
                answer = "Approved! Posting at/after the scheduled time."
        elif action == "reject":
            entry["status"] = "rejected"
            entry["approved_at"] = None
            entry["approval_hash"] = None
            answer = "Rejected. This post will not be published."
        elif action == "regen":
            # Regeneration is initiated only by the authorized owner's button.
            try:
                create_post(date_key, settings, posts, int(revision) + 1)
                answer = "New preview sent for your approval."
            except Exception as exc:
                answer = f"Regeneration failed: {type(exc).__name__}"
                safe_notify(answer)
        else:
            answer = "Invalid action"
        telegram("answerCallbackQuery", {"callback_query_id": callback_id, "text": answer[:200]})
    write_json(TG_STATE, tg)


def meta_request(method, endpoint, token, data=None):
    version = getenv("META_API_VERSION") or "v26.0"
    url = f"https://graph.facebook.com/{version}/{endpoint}"
    try:
        response = requests.request(method, url, headers={"Authorization": f"Bearer {token}"},
                                    data=data if method == "POST" else None,
                                    params=data if method == "GET" else None, timeout=45)
    except requests.RequestException as exc:
        # No automatic replay of a possibly successful publish call.
        raise RuntimeError(f"AMBIGUOUS network error during {endpoint}: {type(exc).__name__}") from exc
    payload = response.json() if "json" in response.headers.get("Content-Type", "") else {}
    if not response.ok or "error" in payload:
        message = payload.get("error", {}).get("message", f"HTTP {response.status_code}")
        raise ValueError(f"Meta {endpoint} rejected request: {message[:240]}")
    return payload


def check_image_ready(url):
    try:
        response = requests.head(url, timeout=12, allow_redirects=True)
        return response.status_code == 200 and "image/jpeg" in response.headers.get("Content-Type", "")
    except requests.RequestException:
        return False


def fb_publish(entry):
    token = getenv("META_PAGE_TOKEN", True)
    page = getenv("FB_PAGE_ID", True)
    data = {"url": public_url(entry["image"]), "message": entry["caption"], "published": "true"}
    result = meta_request("POST", f"{page}/photos", token, data)
    if not result.get("id"):
        raise RuntimeError("AMBIGUOUS Facebook success without a photo ID")
    entry["fb"] = {"status": "published", "id": result["id"]}


def ig_publish(entry):
    token = getenv("META_PAGE_TOKEN", True)
    ig = getenv("IG_USER_ID", True)
    state = entry["ig"]
    if not state["container"]:
        data = {"image_url": public_url(entry["image"]), "caption": entry["caption"]}
        result = meta_request("POST", f"{ig}/media", token, data)
        if not result.get("id"):
            raise RuntimeError("AMBIGUOUS Instagram container creation")
        state["container"] = result["id"]
        state["status"] = "container_created"
    container_id = state["container"]
    # Container processing is asynchronous. Retry next scheduled run if not ready.
    for _ in range(8):
        status = meta_request("GET", container_id, token, {"fields": "status_code,status"})
        if status.get("status_code") == "FINISHED":
            break
        if status.get("status_code") in ("ERROR", "EXPIRED"):
            raise ValueError(f"Instagram container error: {status.get('status', 'unknown')}")
        time.sleep(5)
    else:
        state["status"] = "awaiting_media"
        return
    result = meta_request("POST", f"{ig}/media_publish", token, {"creation_id": container_id})
    if not result.get("id"):
        raise RuntimeError("AMBIGUOUS Instagram publish response")
    state.update({"status": "published", "id": result["id"]})


def threads_publish(entry):
    token = getenv("THREADS_ACCESS_TOKEN") or getenv("META_PAGE_TOKEN", True)
    threads_user = getenv("THREADS_USER_ID", True)
    entry.setdefault("threads", {"status": "not_started", "id": None, "container": None})
    state = entry["threads"]
    if not state.get("container"):
        # Threads caption limit is 500 characters
        caption = entry["caption"]
        if len(caption) > 500:
            caption = caption[:496].rstrip() + "..."
        data = {
            "media_type": "IMAGE",
            "image_url": public_url(entry["image"]),
            "text": caption,
        }
        url = f"https://graph.threads.net/v1.0/{threads_user}/threads"
        response = requests.post(url, headers={"Authorization": f"Bearer {token}"}, data=data, timeout=45)
        payload = response.json() if "json" in response.headers.get("Content-Type", "") else {}
        if not response.ok or "error" in payload or not payload.get("id"):
            err = payload.get("error", {}).get("message", f"HTTP {response.status_code}")
            raise ValueError(f"Threads container error: {err}")
        state["container"] = payload["id"]
        state["status"] = "container_created"
    container_id = state["container"]
    for _ in range(8):
        url = f"https://graph.threads.net/v1.0/{container_id}"
        resp = requests.get(url, headers={"Authorization": f"Bearer {token}"},
                            params={"fields": "status,error_message"}, timeout=30)
        status_data = resp.json() if "json" in resp.headers.get("Content-Type", "") else {}
        st = status_data.get("status")
        if st == "FINISHED":
            break
        if st in ("ERROR", "EXPIRED"):
            err_msg = status_data.get("error_message", "unknown error")
            raise ValueError(f"Threads container status ({st}): {err_msg}")
        time.sleep(5)
    else:
        state["status"] = "awaiting_media"
        return
    pub_url = f"https://graph.threads.net/v1.0/{threads_user}/threads_publish"
    resp = requests.post(pub_url, headers={"Authorization": f"Bearer {token}"},
                         data={"creation_id": container_id}, timeout=45)
    result = resp.json() if "json" in resp.headers.get("Content-Type", "") else {}
    if not resp.ok or "error" in result or not result.get("id"):
        err = result.get("error", {}).get("message", f"HTTP {resp.status_code}")
        raise RuntimeError(f"AMBIGUOUS Threads publish response: {err}")
    state.update({"status": "published", "id": result["id"]})


def due(entry, settings, now):
    date_key = entry["date"]
    scheduled = datetime.fromisoformat(f"{date_key}T{settings['publish_time_ist']}:00").replace(tzinfo=IST)
    approval = datetime.fromisoformat(entry["approved_at"]).astimezone(IST)
    return now >= scheduled and now >= approval


def durable_reserve(posts, entry, platform):
    """Persist a may-have-been-posted marker before calling an external publish API.

    A crash after this marker may require manual reconciliation, but cannot
    silently retry with a possible duplicate. GitHub push failure stops publish.
    """
    entry[platform]["status"] = "attempting"
    entry[platform]["attempted_at"] = datetime.now(timezone.utc).isoformat()
    write_json(STATE, posts)
    if os.getenv("GITHUB_ACTIONS", "").lower() != "true":
        raise RuntimeError("Publishing blocked outside GitHub Actions durable commit")
    relative_state = STATE.relative_to(ROOT).as_posix()
    subprocess.run(["git", "add", relative_state], cwd=ROOT, check=True, timeout=20)
    subprocess.run(["git", "-c", "user.name=social-autopilot[bot]",
                    "-c", "user.email=41898282+github-actions[bot]@users.noreply.github.com",
                    "commit", "-m", f"chore: reserve {platform} {entry['date']} [skip ci]"],
                   cwd=ROOT, check=True, timeout=20, capture_output=True)
    branch = getenv("GITHUB_REF_NAME", True)
    if not re.fullmatch(r"[a-zA-Z0-9_./-]+", branch) or branch.startswith("-"):
        raise RuntimeError("Invalid checked-out branch for checkpoint")
    subprocess.run(["git", "push", "origin", f"HEAD:{branch}"],
                   cwd=ROOT, check=True, timeout=40, capture_output=True)


def process_publications(posts, settings, now):
    if settings.get("approval_required") is not True:
        raise RuntimeError("Approval gate disabled: refusing all publishing")
    if not settings.get("publishing_enabled", False):
        print("Safe setup mode: publishing disabled in settings.json")
        return
    for date_key, entry in sorted(posts.items()):
        if entry.get("status") not in ("approved", "publishing"):
            continue
        if not entry.get("approved_at"):
            continue
        try:
            if not due(entry, settings, now):
                continue
        except (TypeError, ValueError, KeyError):
            entry["status"] = "needs_attention"
            continue
        if not eligible_for_publication(entry, settings, now):
            # Refuse rather than silently publish changed/unsourced/stale content.
            entry["status"] = "needs_attention" if not post_is_stale(entry, settings, now) else "expired"
            print(f"Stopped {date_key}: approval integrity/age/eligibility check failed")
            continue
        if not check_image_ready(public_url(entry["image"])):
            print(f"Image for {date_key} is not yet on GitHub Pages; safe retry later")
            continue
        entry["status"] = "publishing"
        active_platforms = [("fb", fb_publish), ("ig", ig_publish)]
        if getenv("THREADS_USER_ID"):
            active_platforms.append(("threads", threads_publish))

        for name, publisher in active_platforms:
            entry.setdefault(name, {"status": "not_started", "id": None, "container": None})
            state = entry[name]
            if state["status"] in ("published", "ambiguous", "error", "attempting"):
                continue
            try:
                durable_reserve(posts, entry, name)
                publisher(entry)
            except Exception as exc:
                msg = str(exc)
                state["status"] = "ambiguous" if ("AMBIGUOUS" in msg or
                    "durable commit" in msg or "CalledProcessError" in type(exc).__name__) else "error"
                state["error"] = msg[:300]
                safe_notify(f"⚠️ {date_key}: {name.upper()} publishing stopped. {msg[:230]}\n"
                            "No automatic retry; reconcile against the account before manual action.")
        all_published = all(entry.get(p, {}).get("status") == "published" for p, _ in active_platforms)
        any_failed = any(entry.get(p, {}).get("status") in ("ambiguous", "error", "attempting") for p, _ in active_platforms)
        if all_published:
            entry["status"] = "published"
            platform_str = "Facebook, Instagram" + (" and Threads" if getenv("THREADS_USER_ID") else "")
            safe_notify(f"✅ Published on {platform_str} ({date_key}).")
        elif any_failed:
            entry["status"] = "needs_attention"
        # If IG or Threads processing is ongoing, keep 'publishing' for the next cycle.


def main():
    settings = read_json(ROOT / "settings.json", {})
    if settings.get("approval_required") is not True:
        raise RuntimeError("Safety gate: approval_required must be true")
    for item in ("brand", "niche", "audience", "language", "publish_time_ist", "generation_time_ist"):
        if not settings.get(item):
            raise ValueError(f"Missing setting: {item}")
    now = datetime.now(IST)
    today = now.date().isoformat()
    posts = read_json(STATE, {})
    expire_old_posts(posts, settings, now)
    try:
        take_approvals(posts, settings, now)
    except Exception as exc:
        print(f"Approval read failed: {type(exc).__name__}: {exc}")
        # Fail closed. No generation or publishing if Telegram state cannot be read.
        write_json(STATE, posts)
        raise
    generation_at = datetime.fromisoformat(f"{today}T{settings['generation_time_ist']}:00").replace(tzinfo=IST)
    attempt_log = read_json(GEN_ERRORS, {})
    attempt = attempt_log.get(today, {"count": 0, "next_after": "1970-01-01T00:00:00+00:00"})
    allowed_time = datetime.fromisoformat(attempt["next_after"]).astimezone(IST)
    if (today not in posts and now >= generation_at and now >= allowed_time
            and attempt["count"] < 6 and not settings.get("manual_publish_only")):
        try:
            create_post(today, settings, posts)
            attempt_log.pop(today, None)
        except Exception as exc:
            count = attempt["count"] + 1
            attempt_log[today] = {"count": count,
                                  "next_after": (now + timedelta(minutes=min(30 * 2**(count-1), 240))).isoformat()}
            print(f"Daily generation failed: {type(exc).__name__}: {exc}")
            if count == 1 or count == 6:
                safe_notify(f"⚠️ AI draft for {today} failed (attempt {count}/6): {type(exc).__name__}")
    # Small bounded history prevents unneeded public repo growth.
    attempt_log = {k: v for k, v in attempt_log.items() if k >= (now.date() - timedelta(days=14)).isoformat()}
    write_json(GEN_ERRORS, attempt_log)
    process_publications(posts, settings, now)
    write_json(STATE, posts)
    print(f"Finished social-autopilot cycle at {now.isoformat()} / {len(posts)} posts")


if __name__ == "__main__":
    main()
