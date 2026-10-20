"""No-cost, approval-gated Facebook Page + Instagram photo publisher.

GitHub Actions runs this periodically. All credentials come from Actions secrets.
Only original text and locally drawn, people-free JPEG artwork are generated.
"""
from __future__ import annotations

import hashlib
import io
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

def _load_env_fallback():
    env_file = ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())

_load_env_fallback()
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
UNSPLASH_CACHE = ROOT / "state" / "unsplash_cache"


def resize_and_crop(photo: Image.Image, W: int, H: int) -> Image.Image:
    pw, ph = photo.size
    target_aspect = W / H
    current_aspect = pw / ph
    if current_aspect > target_aspect:
        new_w = int(ph * target_aspect)
        left = (pw - new_w) // 2
        photo = photo.crop((left, 0, left + new_w, ph))
    else:
        new_h = int(pw / target_aspect)
        top = (ph - new_h) // 2
        photo = photo.crop((0, top, pw, top + new_h))
    return photo.resize((W, H), Image.Resampling.LANCZOS)


def fetch_unsplash_image(query: str, W: int = 1080, H: int = 1350) -> Image.Image | None:
    """Fetch high-res photo from Unsplash matching query, with curated fallback."""
    UNSPLASH_CACHE.mkdir(parents=True, exist_ok=True)
    access_key = getenv("UNSPLASH_ACCESS_KEY")
    cleaned_query = re.sub(r"[^\w\s]", "", query or "technology").strip() or "technology"
    cache_key = hashlib.sha256(cleaned_query.lower().encode()).hexdigest()[:12]
    cached_file = UNSPLASH_CACHE / f"{cache_key}.jpg"
    if cached_file.exists():
        try:
            return Image.open(cached_file).convert("RGB")
        except Exception:
            pass

    # 1. Try official Unsplash API if access key is present
    if access_key:
        try:
            url = "https://api.unsplash.com/search/photos"
            params = {"query": cleaned_query, "per_page": 1, "orientation": "portrait"}
            headers = {"Authorization": f"Client-ID {access_key}"}
            r = requests.get(url, params=params, headers=headers, timeout=6)
            if r.status_code == 200:
                results = r.json().get("results", [])
                if results:
                    img_url = results[0]["urls"].get("regular") or results[0]["urls"].get("small")
                    img_data = requests.get(img_url, timeout=10).content
                    photo = Image.open(io.BytesIO(img_data)).convert("RGB")
                    processed = resize_and_crop(photo, W, H)
                    try:
                        processed.save(cached_file, "JPEG", quality=92)
                    except Exception:
                        pass
                    return processed
        except Exception as e:
            print(f"Unsplash API search warning: {e}")

    # 2. Curated high-res Unsplash photos mapped across topic categories
    CURATED_UNSPLASH = [
        "https://images.unsplash.com/photo-1518770660439-4636190af475?w=1080&q=80",  # Tech / Circuit
        "https://images.unsplash.com/photo-1618005182384-a83a8bd57fbe?w=1080&q=80",  # AI / Neon Flow
        "https://images.unsplash.com/photo-1498050108023-c5249f4df085?w=1080&q=80",  # Coding / Laptop
        "https://images.unsplash.com/photo-1451187580459-43490279c0fa?w=1080&q=80",  # Cosmos / Space
        "https://images.unsplash.com/photo-1556761175-5973dc0f32e7?w=1080&q=80",  # Team / Startup
        "https://images.unsplash.com/photo-1524492412937-b28074a5d7da?w=1080&q=80",  # India Monument / Heritage
        "https://images.unsplash.com/photo-1505740420928-5e560c06d30e?w=1080&q=80",  # Gadget / Audio
        "https://images.unsplash.com/photo-1497435334941-8c899ee9e8e9?w=1080&q=80",  # Green Energy
        "https://images.unsplash.com/photo-1507679799987-c73779587ccf?w=1080&q=80",  # Strategy / Mindset
    ]
    idx = int(hashlib.sha256(cleaned_query.encode()).hexdigest()[:8], 16) % len(CURATED_UNSPLASH)
    try:
        r = requests.get(CURATED_UNSPLASH[idx], timeout=8)
        if r.status_code == 200:
            photo = Image.open(io.BytesIO(r.content)).convert("RGB")
            processed = resize_and_crop(photo, W, H)
            try:
                processed.save(cached_file, "JPEG", quality=92)
            except Exception:
                pass
            return processed
    except Exception as e:
        print(f"Curated photo fallback warning: {e}")
    return None


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


def fetch_web_feed_topic(date_key=None, revision=1, recent_titles=None):
    """Fetch real-time trending topics from diverse daily Global & India Hot Topic RSS feeds."""
    import urllib.request
    import xml.etree.ElementTree as ET
    feed_channels = [
        ("AI & Deep Tech [Global]", "https://news.google.com/rss/search?q=AI+breakthrough+OR+generative+AI+OR+OpenAI+OR+Anthropic+OR+NVIDIA&hl=en-IN&gl=IN&ceid=IN:en"),
        ("India Tech & Smart Gadgets [India Hot Topic]", "https://news.google.com/rss/search?q=India+technology+OR+smartphone+launch+India+OR+gadgets+360+India&hl=en-IN&gl=IN&ceid=IN:en"),
        ("High-Performance Productivity [Global]", "https://news.google.com/rss/search?q=productivity+tools+OR+digital+habits+OR+deep+work+software&hl=en-US&gl=US&ceid=US:en"),
        ("India Startups & Innovation [India Hot Topic]", "https://news.google.com/rss/search?q=Indian+startups+OR+unicorn+India+OR+founder+growth+India&hl=en-IN&gl=IN&ceid=IN:en"),
        ("Science & Space Frontiers [Global]", "https://news.google.com/rss/headlines/section/topic/SCIENCE?hl=en-IN&gl=IN&ceid=IN:en"),
        ("Strategic Mindset & Growth [Mindset]", "https://news.google.com/rss/search?q=mental+models+thinking+OR+career+growth+strategy+OR+psychology+hacks&hl=en-IN&gl=IN&ceid=IN:en"),
        ("Future Clean Tech & Living [Global & India]", "https://news.google.com/rss/search?q=green+energy+tech+OR+electric+mobility+OR+sustainable+future&hl=en-IN&gl=IN&ceid=IN:en"),
    ]
    day_idx = 0
    if date_key:
        try:
            day_idx = date.fromisoformat(date_key).weekday()
        except Exception:
            day_idx = 0
    category, feed_url = feed_channels[(day_idx + revision - 1) % len(feed_channels)]
    try:
        req = urllib.request.Request(feed_url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
        xml_data = urllib.request.urlopen(req, timeout=6).read()
        root = ET.fromstring(xml_data)
        items = root.findall(".//item")
        if items:
            offset = (revision - 1) % len(items)
            for item in items[offset:offset+10]:
                title = item.find("title").text if item.find("title") is not None else ""
                clean_title = title.rsplit(" - ", 1)[0] if " - " in title else title
                clean_title = clean_title.strip()
                link = item.find("link").text if item.find("link") is not None else ""
                if clean_title and len(clean_title) > 10:
                    if recent_titles and any(similar(normalize(clean_title), normalize(t)) for t in recent_titles):
                        continue
                    return {"category": category, "headline": clean_title, "url": link}
    except Exception as exc:
        print(f"Web feed fetch warning: {exc}")
    try:
        fallback_url = "https://news.google.com/rss/headlines/section/topic/TECHNOLOGY?hl=en-IN&gl=IN&ceid=IN:en"
        req = urllib.request.Request(fallback_url, headers={"User-Agent": "Mozilla/5.0"})
        xml_data = urllib.request.urlopen(req, timeout=6).read()
        root = ET.fromstring(xml_data)
        for item in root.findall(".//item")[:5]:
            title = item.find("title").text if item.find("title") is not None else ""
            clean_title = title.rsplit(" - ", 1)[0] if " - " in title else title
            if clean_title and len(clean_title) > 10:
                return {"category": "Tech & Innovation", "headline": clean_title.strip(), "url": item.find("link").text if item.find("link") is not None else ""}
    except Exception:
        pass
    return None


def choose_topic(date_key, revision=1, use_web=False, recent_titles=None):
    """Select a consistent weekday pillar, with optional live web feed override."""
    day = date.fromisoformat(date_key)
    if use_web:
        web = fetch_web_feed_topic(date_key, revision, recent_titles=recent_titles)
        if web:
            return {
                "type": "practical",
                "category": web["category"],
                "direction": web["headline"],
                "angle": "a timely breakdown of today's hot topic",
                "source": None,
                "web_url": web.get("url")
            }
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
    if "subtitle" in item and isinstance(item["subtitle"], str) and len(item["subtitle"]) > 160:
        item["subtitle"] = item["subtitle"][:156].rstrip() + "..."
    if "caption" in item and isinstance(item["caption"], str) and len(item["caption"]) > 1500:
        item["caption"] = item["caption"][:1490].rstrip() + "..."
    for field, limit in (("title", 65), ("subtitle", 165), ("caption", 1600)):
        value = item.get(field)
        if not isinstance(value, str) or not value.strip() or len(value) > limit:
            raise RuntimeError(f"AI content failed length/empty check: {field}")
        item[field] = value.strip()
    if not (2 <= len(item["title"].split()) <= 10):
        raise RuntimeError("Visual headline must have 2–10 words")
    content = f"{item['title']} {item['subtitle']} {item['caption']}".lower()
    # Prevent unattributed quotations, made-up research/news, spam and unapproved URLs.
    if re.search(r'https?://|www\.|@[a-z0-9_]+|[“”\"«»]', content, re.I):
        raise RuntimeError("No quoted/attributed content, external links or @handles in generated copy")
    if re.search(r'\b(latest news|breaking news|new study|according to (a )?study|scientists say|guaranteed|100%|proven cure|financial advice|as an ai|buy now|limited offer)\b', content, re.I):
        raise RuntimeError("Unsupported claims, news, or marketing language blocked")
    if re.search(r'\b\d+(?:[.,]\d+)?\s*(?:%|percent|million|billion|times)(?!\w)', content, re.I):
        raise RuntimeError("Unreviewed statistics or quantitative claims blocked")
    hashtags = re.findall(r'(?<!\w)#[a-z][a-z0-9_]*', item["caption"], re.I)
    if not 3 <= len(hashtags) <= 6:
        raise RuntimeError("Captions require 3–6 relevant hashtags")
    recent = list(posts.values())[-90:]
    norm_title = normalize(item["title"])
    norm_caption = normalize(item["caption"])
    if any(similar(norm_title, normalize(p.get("title", ""))) or
           similar(norm_caption, normalize(p.get("caption", ""))) for p in recent):
        raise RuntimeError("Too close to a previous title/caption; no new preview sent")
    if slot["type"] == "sourced" and not slot.get("source"):
        raise RuntimeError("Factual posts must use a source selected by code, never AI")
    if "recommended_audio" not in item or not isinstance(item.get("recommended_audio"), dict):
        item["recommended_audio"] = {
            "title": "Neon Horizons",
            "artist": "Synthwave Collective",
            "vibe": "Focus / Tech Flow",
            "search_query": "Neon Horizons Synthwave"
        }
    else:
        for k in ("title", "artist", "vibe"):
            val = item["recommended_audio"].get(k)
            if not isinstance(val, str) or not val.strip():
                item["recommended_audio"][k] = "Trending Tech Focus"
    return item


def generated_content(settings, posts, date_key, revision=1):
    key = getenv("GEMINI_API_KEY", True)
    use_web = settings.get("use_live_web_feed", False)
    recent = [p.get("title", "") for _, p in sorted(posts.items())[-60:]]
    slot = choose_topic(date_key, revision, use_web=use_web, recent_titles=recent)
    brand = settings.get("brand", "Choicematrix")
    
    prompt = f"""You are a master social media content strategist for '{brand}'.
Create an ultra-high-value, highly engaging 6-SLIDE CAROUSEL post based on today's topic.
Brand: {brand}.
Topic Category: {slot['category']}
Headline or Direction: {slot.get('direction', slot.get('seed', 'Modern Tech Innovation'))}
Angle: {slot['angle']}
Audience: {settings['audience']}; Language: {settings['language']}.
Style: {settings['content_style']}.

DEPTH & QUALITY REQUIREMENTS:
- Provide rich depth, specific takeaways, and substantial explanations on each slide. Avoid shallow, generic bullet points.
- Slide 1: High-impact hook and clear problem/opportunity overview with a compelling teaser.
- Slides 2-5: Detailed 4-6 sentence comprehensive breakdown (65 to 95 words per slide). Explain the core logic, actionable implementation steps, real-world examples, and nuanced pro tips that make the reader stop and read every sentence.
- Recommend a real trending background audio / music track (title, artist, vibe) that pairs perfectly with this post on Instagram Reels / Facebook / Threads.
- Provide a targeted 'unsplash_query' (2-3 words, e.g., 'artificial intelligence laboratory', 'workspace laptop coding', 'modern gadgets desk') for high-resolution photography.
- Slide 6: Follow & Save CTA.

Return ONLY ONE valid JSON object with this exact structure:
{{
  "title": "Strong 3-6 word title (max 50 chars)",
  "subtitle": "Clear explanation hook of what readers will learn (max 130 chars)",
  "caption": "Full high-value social caption (max 950 chars) breaking down the essence, ending with 3-5 hashtags including #{brand}.",
  "unsplash_query": "2-3 word photo query (e.g., 'workspace laptop desk' or 'futuristic artificial intelligence')",
  "recommended_audio": {{
    "title": "Track Title (e.g., 'Resonance', 'Midnight City', or 'Lofi Focus')",
    "artist": "Artist Name (e.g., 'HOME', 'M83', or 'Chillhop')",
    "vibe": "Genre / Vibe (e.g., 'Futuristic Synthwave / High Focus')",
    "search_query": "Exact search query for Instagram/TikTok audio picker"
  }},
  "slides": [
    {{
      "slide_number": 1,
      "tag": "HOT TREND",
      "headline": "Bold 3-6 Word Title",
      "body": "Compelling 3-4 sentence overview breaking down the problem or opportunity and compelling the user to read all slides.",
      "swipe_prompt": "Swipe to explore ➡️"
    }},
    {{
      "slide_number": 2,
      "tag": "STEP 01",
      "headline": "The Core Foundation",
      "body": "Detailed 4-6 sentence deep dive explaining the foundational mechanism, why traditional approaches fail, and what the new breakthrough does differently."
    }},
    {{
      "slide_number": 3,
      "tag": "STEP 02",
      "headline": "Actionable Implementation",
      "body": "Comprehensive 4-6 sentence breakdown of the exact step-by-step process, tools, metrics, and workflows to apply immediately."
    }},
    {{
      "slide_number": 4,
      "tag": "STEP 03",
      "headline": "Nuance & Pro Insights",
      "body": "In-depth 4-6 sentence analysis of common pitfalls to avoid, insider optimizations, and high-leverage habits that multiply your results."
    }},
    {{
      "slide_number": 5,
      "tag": "SUMMARY",
      "headline": "The Master Takeaway",
      "body": "Full synthesized conclusion uniting the entire framework into a memorable, actionable takeaway ready to put into practice today."
    }},
    {{
      "slide_number": 6,
      "tag": "CONNECT",
      "headline": "FOUND THIS HELPFUL? 💡",
      "body": "Follow @{brand.lower()} for more daily hot trends, tech breakdowns & smart tips! 🚀🔥✨\\n\\n📌 Save this post for later\\n↗️ Share with a friend"
    }}
  ]
}}
"""
    model = getenv("GEMINI_MODEL") or "gemini-2.5-flash"
    if not re.fullmatch(r"[a-zA-Z0-9_.-]+", model):
        raise ValueError("Invalid model identifier")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"responseMimeType": "application/json", "temperature": 0.75},
    }
    response = None
    for attempt in range(3):
        try:
            response = requests.post(url, headers={"x-goog-api-key": key}, json=body, timeout=45)
            if response.status_code == 200:
                break
            print(f"Gemini API returned {response.status_code}, retrying in {(attempt + 1) * 2}s...")
        except Exception as exc:
            print(f"Gemini request error: {exc}")
        time.sleep((attempt + 1) * 2)

    item = None
    if response and response.status_code == 200:
        parts = response.json().get("candidates", [{}])[0].get("content", {}).get("parts", [])
        if parts:
            try:
                item = json.loads("".join(part.get("text", "") for part in parts))
            except Exception:
                item = None

    # Ultra-fast Groq LLM Fallback (Zero downtime)
    if not item:
        groq_key = getenv("GROQ_API_KEY")
        if groq_key:
            print("Falling back to ultra-fast Groq LLM (qwen/qwen3.8-27b)...")
            try:
                groq_res = requests.post(
                    "https://api.groq.com/openai/v1/chat/completions",
                    headers={"Authorization": f"Bearer {groq_key}", "Content-Type": "application/json"},
                    json={
                        "model": "qwen/qwen3.8-27b",
                        "messages": [
                            {"role": "system", "content": "You are a master social media content strategist. Return ONLY one valid JSON object."},
                            {"role": "user", "content": prompt}
                        ],
                        "response_format": {"type": "json_object"},
                        "temperature": 0.7
                    },
                    timeout=25
                )
                if groq_res.status_code == 200:
                    item = json.loads(groq_res.json()["choices"][0]["message"]["content"])
            except Exception as e:
                print(f"Groq fallback warning: {e}")

    if not item:
        raise RuntimeError("AI returned no text; fail closed")
    item = validate_ai_copy(item, posts, slot)
    item["category"] = slot["category"]
    item["content_type"] = slot["type"]
    item["source"] = slot.get("source")
    if slot.get("source"):
        item["caption"] += f"\n\nReference: {slot['source']['label']} — {slot['source']['url']}"
    audio = item.get("recommended_audio")
    if audio and isinstance(audio, dict) and audio.get("title"):
        audio_line = f"\n\n🎵 Suggested Audio: {audio.get('title')} - {audio.get('artist')} ({audio.get('vibe')})"
        if audio_line not in item["caption"] and len(item["caption"]) + len(audio_line) < 1800:
            item["caption"] += audio_line
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


THEMES = [
    {
        "name": "Cyber Obsidian",
        "bg_top": (10, 15, 26), "bg_bot": (18, 28, 48), "card_bg": (22, 34, 58),
        "accent": (0, 242, 254), "accent_secondary": (255, 184, 0),
        "text_primary": (255, 255, 255), "text_secondary": (170, 185, 205), "border": (40, 60, 95)
    },
    {
        "name": "Clean Editorial",
        "bg_top": (248, 247, 244), "bg_bot": (242, 240, 235), "card_bg": (255, 255, 255),
        "accent": (225, 29, 72), "accent_secondary": (15, 23, 42),
        "text_primary": (15, 23, 42), "text_secondary": (71, 85, 105), "border": (226, 232, 240)
    },
    {
        "name": "Royal Sunset",
        "bg_top": (30, 27, 75), "bg_bot": (59, 7, 100), "card_bg": (48, 20, 95),
        "accent": (245, 158, 11), "accent_secondary": (251, 113, 133),
        "text_primary": (255, 255, 255), "text_secondary": (226, 232, 240), "border": (88, 28, 135)
    },
    {
        "name": "Emerald Horizon",
        "bg_top": (6, 35, 29), "bg_bot": (11, 59, 50), "card_bg": (14, 75, 64),
        "accent": (16, 185, 129), "accent_secondary": (167, 243, 208),
        "text_primary": (255, 255, 255), "text_secondary": (209, 250, 229), "border": (20, 90, 75)
    },
    {
        "name": "Swiss Monochrome",
        "bg_top": (10, 10, 12), "bg_bot": (18, 18, 22), "card_bg": (28, 28, 34),
        "accent": (250, 204, 21), "accent_secondary": (255, 255, 255),
        "text_primary": (255, 255, 255), "text_secondary": (161, 161, 170), "border": (45, 45, 55)
    },
    {
        "name": "Deep Cobalt",
        "bg_top": (11, 25, 44), "bg_bot": (30, 62, 98), "card_bg": (25, 50, 80),
        "accent": (56, 189, 248), "accent_secondary": (0, 173, 181),
        "text_primary": (255, 255, 255), "text_secondary": (224, 231, 255), "border": (45, 85, 135)
    }
]


def render_carousel_slides(date_key, revision, settings, item):
    """Render 5-6 high-res 1080x1350 carousel slides with daily rotating theme."""
    day = date.fromisoformat(date_key)
    slides_data = item.get("slides") or []
    if not slides_data:
        # Fallback to single card representation in carousel format
        slides_data = [
            {"slide_number": 1, "tag": item.get("category", "IDEAS"), "headline": item["title"],
             "body": item["subtitle"], "swipe_prompt": "Swipe to explore ➡️"},
            {"slide_number": 2, "tag": "CONNECT", "headline": "FOUND THIS HELPFUL? 💡",
             "body": f"Follow @{settings['brand'].lower()} for more daily hot trends, tech & life hacks! 🚀🔥✨\n\n📌 Save this post for later\n↗️ Share with a friend"}
        ]
    total_slides = len(slides_data)
    rendered_paths = []
    brand = settings.get("brand", "Choicematrix")
    W, H = 1080, 1350
    IMAGE_DIR.mkdir(parents=True, exist_ok=True)

    for idx, slide in enumerate(slides_data):
        slide_num = slide.get("slide_number", idx + 1)
        if slide_num == total_slides:
            # Special vibrant Outro / CTA Theme with gold & rose glow
            theme = {
                "name": "Radiant Outro",
                "bg_top": (22, 12, 38), "bg_bot": (44, 15, 62), "card_bg": (36, 16, 52),
                "accent": (255, 184, 0), "accent_secondary": (244, 63, 94),
                "text_primary": (255, 255, 255), "text_secondary": (235, 220, 245), "border": (85, 25, 105)
            }
        else:
            # Rotate distinct themes across slides so every slide has its own theme
            theme_idx = (day.toordinal() + revision - 1 + idx) % len(THEMES)
            theme = THEMES[theme_idx]

        slide_tag = slide.get("tag", "")
        photo_query = f"{item.get('unsplash_query', '')} {slide_tag}".strip()
        bg_photo = fetch_unsplash_image(photo_query or item.get("title", "technology"), W, H)

        if bg_photo:
            overlay = Image.new("RGBA", (W, H))
            draw_ol = ImageDraw.Draw(overlay)
            for y in range(H):
                ratio = y / H
                alpha = int(185 + 50 * ratio)  # Darkened tint to preserve 100% text contrast
                r = int(theme["bg_top"][0]*(1-ratio) + theme["bg_bot"][0]*ratio)
                g = int(theme["bg_top"][1]*(1-ratio) + theme["bg_bot"][1]*ratio)
                b = int(theme["bg_top"][2]*(1-ratio) + theme["bg_bot"][2]*ratio)
                draw_ol.line((0, y, W, y), fill=(r, g, b, alpha))
            image = Image.alpha_composite(bg_photo.convert("RGBA"), overlay).convert("RGB")
            draw = ImageDraw.Draw(image)
        else:
            image = Image.new("RGB", (W, H))
            draw = ImageDraw.Draw(image)
            # Background gradient
            for y in range(H):
                ratio = y / H
                c = tuple(int(theme["bg_top"][i]*(1-ratio) + theme["bg_bot"][i]*ratio) for i in range(3))
                draw.line((0, y, W, y), fill=c)

        # Subtle geometric decorative accents matching slide theme
        rnd = random.Random(int(hashlib.sha256(f"{date_key}-{revision}-{slide_num}-{theme['name']}".encode()).hexdigest()[:12], 16))
        for _ in range(8):
            rx, ry = rnd.randrange(0, W), rnd.randrange(0, H)
            rad = rnd.randrange(40, 180)
            alpha_color = tuple(min(255, int(v * 0.45)) for v in theme["border"])
            draw.ellipse((rx-rad, ry-rad, rx+rad, ry+rad), outline=alpha_color, width=2)

        slide_num = slide.get("slide_number", 1)
        tag = slide.get("tag", "HOT TOPIC").upper()
        headline = slide.get("headline", "")
        body = slide.get("body", "")
        audio = item.get("recommended_audio") or {}

        # Top Header Bar
        pill_w = 260
        draw.rounded_rectangle((70, 75, 70 + pill_w, 135), radius=16, fill=theme["accent"])
        draw.text((90, 92), tag[:22], font=font(22, bold=True), fill=theme["bg_top"])
        counter_str = f"{slide_num:02d} / {total_slides:02d}"
        draw.text((W - 190, 92), counter_str, font=font(24, bold=True), fill=theme["accent_secondary"])
        draw.line((70, 165, W - 70, 165), fill=theme["border"], width=2)

        if slide_num == 1:
            draw.text((70, 220), "TODAY'S HOT BREAKDOWN", font=font(22, bold=True), fill=theme["accent"])
            f_title = font(54, bold=True)
            title_lines = wrap_by_pixels(draw, headline, f_title, 940)
            y = 265
            for line in title_lines[:4]:
                draw.text((70, y), line, font=f_title, fill=theme["text_primary"])
                y += 68
            y += 15
            draw.rounded_rectangle((70, y, 70 + 200, y + 8), radius=4, fill=theme["accent"])
            y += 35
            card_h = 380
            draw.rounded_rectangle((70, y, W - 70, y + card_h), radius=28, fill=theme["card_bg"], outline=theme["border"], width=3)
            f_body = font(33, bold=False)
            body_lines = wrap_by_pixels(draw, body, f_body, 860)
            by = y + 40
            for line in body_lines[:7]:
                draw.text((110, by), line, font=f_body, fill=theme["text_secondary"])
                by += 46

            if audio.get("title"):
                audio_title_clean = str(audio.get("title", ""))[:28]
                audio_artist_clean = str(audio.get("artist", ""))[:22]
                draw.rounded_rectangle((70, H - 325, W - 70, H - 265), radius=18, fill=theme["card_bg"], outline=theme["accent_secondary"], width=2)
                draw.text((105, H - 308), f"🎵 AUDIO: {audio_title_clean} • {audio_artist_clean}", font=font(21, bold=True), fill=theme["accent_secondary"])
                draw.text((W - 270, H - 308), str(audio.get("vibe", "Trending"))[:16].upper(), font=font(19, bold=False), fill=theme["text_secondary"])

            swipe_y = H - 235
            draw.rounded_rectangle((70, swipe_y, W - 70, swipe_y + 85), radius=24, fill=theme["accent"])
            draw.text((110, swipe_y + 26), slide.get("swipe_prompt", "Swipe to explore ➡️").upper(), font=font(28, bold=True), fill=theme["bg_top"])
        elif slide_num == total_slides:
            draw.text((70, 220), "KEY TAKEAWAY COMPLETED", font=font(22, bold=True), fill=theme["accent"])
            f_title = font(54, bold=True)
            draw.text((70, 270), headline, font=f_title, fill=theme["text_primary"])
            box_y = 360
            box_h = 490
            draw.rounded_rectangle((70, box_y, W - 70, box_y + box_h), radius=32, fill=theme["card_bg"], outline=theme["accent"], width=4)
            draw.text((110, box_y + 40), f"👉 FOLLOW @{brand.upper()}", font=font(38, bold=True), fill=theme["accent"])
            draw.text((110, box_y + 98), "For daily hot trends, tech breakdowns & smart tips! 🚀🔥✨", font=font(25, bold=False), fill=theme["text_secondary"])
            
            if audio.get("title"):
                draw.text((110, box_y + 148), f"🎵 Recommended Reel Audio: {audio.get('title')} — {audio.get('artist')}", font=font(23, bold=True), fill=theme["accent_secondary"])
                draw.line((110, box_y + 195, W - 110, box_y + 195), fill=theme["border"], width=2)
                line_offset = 230
            else:
                draw.line((110, box_y + 165, W - 110, box_y + 165), fill=theme["border"], width=2)
                line_offset = 200

            draw.text((110, box_y + line_offset), "📌  Save this post to revisit anytime", font=font(28, bold=False), fill=theme["text_primary"])
            draw.text((110, box_y + line_offset + 60), "💬  Share your thoughts in the comments", font=font(28, bold=False), fill=theme["text_primary"])
            draw.text((110, box_y + line_offset + 120), "↗️  Send to someone who needs this!", font=font(28, bold=False), fill=theme["text_primary"])
        else:
            step_str = f"STEP {slide_num - 1:02d}" if slide_num < total_slides - 1 else "SUMMARY"
            draw.text((70, 210), step_str, font=font(26, bold=True), fill=theme["accent"])
            f_title = font(48, bold=True)
            title_lines = wrap_by_pixels(draw, headline, f_title, 940)
            y = 260
            for line in title_lines[:3]:
                draw.text((70, y), line, font=f_title, fill=theme["text_primary"])
                y += 64
            y += 25
            card_h = 670
            draw.rounded_rectangle((70, y, W - 70, y + card_h), radius=32, fill=theme["card_bg"], outline=theme["border"], width=3)
            f_body = font(29, bold=False)
            body_lines = wrap_by_pixels(draw, body, f_body, 860)
            by = y + 40
            for line in body_lines[:14]:
                draw.text((110, by), line, font=f_body, fill=theme["text_secondary"])
                by += 43

        # Footer Brand Bar
        draw.line((70, H - 120, W - 70, H - 120), fill=theme["border"], width=2)
        draw.text((70, H - 90), f"@{brand.upper()}", font=font(24, bold=True), fill=theme["accent"])
        draw.text((W - 320, H - 90), "DAILY HOT TRENDS", font=font(22, bold=False), fill=theme["text_secondary"])

        dest = IMAGE_DIR / f"{date_key}-v{revision}-slide{slide_num}.jpg"
        image.save(dest, "JPEG", quality=90, optimize=True)
        rendered_paths.append(dest.relative_to(ROOT / "docs").as_posix())

    return rendered_paths


def image_art(date_key, revision, settings, item):
    """Draw original abstract illustration for single-card backwards compatibility."""
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
    draw.rounded_rectangle((68, 73, W-68, H-79), radius=50, fill=(*top,))
    draw.rounded_rectangle((110, 116, 110+365, 178), radius=28, fill=accent)
    draw.text((138, 132), settings["brand"].upper()[:24], font=font(24), fill=top)
    draw.text((113, 204), item.get("category", "IDEAS").upper()[:28], font=font(21), fill=accent)
    draw.line((112, 264, W-112, 264), fill=accent, width=4)
    title = item["title"].strip()
    for size in range(76, 38, -2):
        f = font(size)
        lines = wrap_by_pixels(draw, title, f, 840)
        if len(lines) <= 4:
            break
    y = 340
    for line in lines:
        draw.text((116, y), line, font=f, fill=(255, 254, 250))
        y += size + 32
    y += 35
    draw.rounded_rectangle((112, y, W-112, y+7), radius=4, fill=accent)
    y += 75
    f2 = font(35, bold=False)
    sublines = wrap_by_pixels(draw, item["subtitle"], f2, 825)
    for line in sublines:
        draw.text((115, y), line, font=f2, fill=(234, 235, 234))
        y += 35 + 18
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
    content = generated_content(settings, posts, date_key, revision)
    slides = content.get("slides")
    if slides and isinstance(slides, list) and len(slides) >= 2:
        images = render_carousel_slides(date_key, revision, settings, content)
        primary_image = images[0]
    else:
        primary_image = image_art(date_key, revision, settings, content)
        images = [primary_image]

    entry = {
        "date": date_key, "revision": revision, "title": content["title"],
        "subtitle": content["subtitle"], "caption": content["caption"],
        "category": content["category"], "content_type": content["content_type"],
        "source": content["source"], "image": primary_image,
        "images": images,
        "recommended_audio": content.get("recommended_audio"),
        "image_sha256": hashlib.sha256((ROOT / "docs" / primary_image).read_bytes()).hexdigest(),
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
        del posts[date_key]
        raise
    return entry


def send_preview(post, settings):
    date_key, revision = post["date"], post["revision"]
    chat = getenv("TG_APPROVER_ID", True)
    images = post.get("images") or [post["image"]]

    if len(images) > 1:
        # Deliver full swipeable media album to Telegram!
        media_group = [{"type": "photo", "media": f"attach://photo{i}"} for i in range(len(images))]
        files = {f"photo{i}": (Path(img).name, open(ROOT / "docs" / img, "rb"), "image/jpeg") for i, img in enumerate(images)}
        try:
            token = getenv("TG_BOT_TOKEN", True)
            requests.post(f"https://api.telegram.org/bot{token}/sendMediaGroup",
                          data={"chat_id": chat, "media": json.dumps(media_group)},
                          files=files, timeout=40)
        finally:
            for _, f_tuple in files.items():
                f_tuple[1].close()
    else:
        file = ROOT / "docs" / post["image"]
        with file.open("rb") as image_file:
            telegram("sendPhoto", {"chat_id": chat, "caption": f"DAILY DRAFT • {date_key}"}, {"photo": image_file})

    markup = {"inline_keyboard": [
        [{"text": "✅ Approve Carousel", "callback_data": f"approve:{date_key}:{revision}"},
         {"text": "🔄 Regenerate", "callback_data": f"regen:{date_key}:{revision}"}],
        [{"text": "❌ Reject", "callback_data": f"reject:{date_key}:{revision}"}]
    ]}
    audio = post.get("recommended_audio")
    audio_info = ""
    if audio and isinstance(audio, dict) and audio.get("title"):
        audio_info = f"🎵 RECOMMENDED AUDIO: {audio.get('title')} — {audio.get('artist')} ({audio.get('vibe')})\n\n"

    full_text = (
        f"🔥 {len(images)}-SLIDE CAROUSEL DRAFT • {date_key}\n\n"
        f"TITLE: {post['title']}\n\n"
        f"{audio_info}"
        f"{post['caption']}\n\n"
        f"Scheduled at/after {settings['publish_time_ist']} IST.\n"
        "👉 Click Approve below or reply 'approve' in this chat to confirm!"
    )
    telegram("sendMessage", {"chat_id": chat, "text": full_text[:4000],
                             "reply_markup": json.dumps(markup)})


def take_approvals(posts, settings, now=None):
    if now is None:
        now = datetime.now(IST)
    tg = read_json(TG_STATE, {"offset": 0})
    updates = telegram("getUpdates", {"offset": tg["offset"], "timeout": 0,
                                       "allowed_updates": json.dumps(["callback_query", "message"])})
    for update in updates:
        tg["offset"] = max(tg["offset"], int(update["update_id"]) + 1)
        callback = update.get("callback_query")
        if callback:
            callback_id = callback.get("id", "")
            user_id = str(callback.get("from", {}).get("id", ""))
            # Instantly answer query so Telegram Web does NOT hang
            try:
                telegram("answerCallbackQuery", {"callback_query_id": callback_id, "text": "Action received! ✅"})
            except Exception:
                pass
            if user_id != getenv("TG_APPROVER_ID", True):
                continue
            action, sep, rest = callback.get("data", "").partition(":")
            fields = rest.split(":") if sep else []
            if len(fields) != 2:
                continue
            date_key, revision = fields
            entry = posts.get(date_key)
            if not entry or str(entry["revision"]) != revision or entry["status"] != "pending":
                continue
            if action == "approve":
                if post_is_stale(entry, settings, now):
                    entry["status"] = "expired"
                else:
                    entry["status"] = "approved"
                    entry["approved_at"] = datetime.now(timezone.utc).isoformat()
                    entry["approval_hash"] = approval_digest(entry)
                    safe_notify("✅ APPROVED! Carousel is scheduled to publish across Facebook, Instagram, and Threads at 19:00 IST. 🚀")
            elif action == "reject":
                entry["status"] = "rejected"
                entry["approved_at"] = None
                entry["approval_hash"] = None
                safe_notify("❌ REJECTED. This post will not be published.")
            elif action == "regen":
                try:
                    create_post(date_key, settings, posts, int(revision) + 1)
                    safe_notify("🔄 New carousel generated from today's live web feed and sent for approval!")
                except Exception as exc:
                    safe_notify(f"Regeneration failed: {type(exc).__name__}")

        # Support direct chat text messages (approve, yes, ok, regen, reject)
        msg = update.get("message")
        if msg:
            user_id = str(msg.get("from", {}).get("id", ""))
            text = msg.get("text", "").strip().lower()
            if user_id == getenv("TG_APPROVER_ID", True) and text:
                pending_key = None
                for k in sorted(posts.keys(), reverse=True):
                    if posts[k].get("status") == "pending":
                        pending_key = k
                        break
                if pending_key:
                    entry = posts[pending_key]
                    if text in ("approve", "/approve", "yes", "y", "ok", "publish", "1"):
                        entry["status"] = "approved"
                        entry["approved_at"] = datetime.now(timezone.utc).isoformat()
                        entry["approval_hash"] = approval_digest(entry)
                        safe_notify("✅ APPROVED! Scheduled to publish across Facebook, Instagram, and Threads at 19:00 IST. 🚀")
                    elif text in ("regen", "/regen", "regenerate", "new", "2"):
                        try:
                            create_post(pending_key, settings, posts, entry["revision"] + 1)
                            safe_notify("🔄 Generating a new carousel for you...")
                        except Exception as exc:
                            safe_notify(f"Regeneration failed: {type(exc).__name__}")
                    elif text in ("reject", "/reject", "no", "cancel", "3"):
                        entry["status"] = "rejected"
                        safe_notify("❌ Post rejected. Nothing will be published today.")

    write_json(TG_STATE, tg)


def meta_request(method, endpoint, token, data=None):
    version = getenv("META_API_VERSION") or "v26.0"
    url = f"https://graph.facebook.com/{version}/{endpoint}"
    try:
        response = requests.request(method, url, headers={"Authorization": f"Bearer {token}"},
                                    data=data if method == "POST" else None,
                                    params=data if method == "GET" else None, timeout=45)
    except requests.RequestException as exc:
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
    images = entry.get("images") or [entry["image"]]
    if len(images) > 1:
        # Multi-photo carousel / album on Facebook Page
        photo_ids = []
        for img in images:
            res = meta_request("POST", f"{page}/photos", token, {"url": public_url(img), "published": "false"})
            if res.get("id"):
                photo_ids.append({"media_fbid": res["id"]})
        data = {"message": entry["caption"], "attached_media": json.dumps(photo_ids)}
        result = meta_request("POST", f"{page}/feed", token, data)
    else:
        data = {"url": public_url(entry["image"]), "message": entry["caption"], "published": "true"}
        result = meta_request("POST", f"{page}/photos", token, data)
    if not result.get("id"):
        raise RuntimeError("AMBIGUOUS Facebook success without an ID")
    entry["fb"] = {"status": "published", "id": result["id"]}


def ig_publish(entry):
    token = getenv("META_PAGE_TOKEN", True)
    ig = getenv("IG_USER_ID", True)
    state = entry["ig"]
    images = entry.get("images") or [entry["image"]]
    if len(images) > 1:
        if not state.get("container"):
            child_ids = []
            for img in images:
                sub = meta_request("POST", f"{ig}/media", token, {"image_url": public_url(img), "is_carousel_item": "true"})
                child_ids.append(sub["id"])
            parent = meta_request("POST", f"{ig}/media", token, {"media_type": "CAROUSEL", "children": ",".join(child_ids), "caption": entry["caption"]})
            state["container"] = parent["id"]
            state["status"] = "container_created"
    else:
        if not state.get("container"):
            data = {"image_url": public_url(entry["image"]), "caption": entry["caption"]}
            result = meta_request("POST", f"{ig}/media", token, data)
            state["container"] = result["id"]
            state["status"] = "container_created"

    container_id = state["container"]
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
    images = entry.get("images") or [entry["image"]]
    caption = entry["caption"]
    if len(caption) > 500:
        caption = caption[:496].rstrip() + "..."

    if len(images) > 1:
        if not state.get("container"):
            child_ids = []
            for img in images:
                url = f"https://graph.threads.net/v1.0/{threads_user}/threads"
                r = requests.post(url, headers={"Authorization": f"Bearer {token}"},
                                  data={"media_type": "IMAGE", "image_url": public_url(img), "is_carousel_item": "true"}, timeout=45)
                child_ids.append(r.json()["id"])
            parent_url = f"https://graph.threads.net/v1.0/{threads_user}/threads"
            r = requests.post(parent_url, headers={"Authorization": f"Bearer {token}"},
                              data={"media_type": "CAROUSEL", "children": ",".join(child_ids), "text": caption}, timeout=45)
            state["container"] = r.json()["id"]
            state["status"] = "container_created"
    else:
        if not state.get("container"):
            url = f"https://graph.threads.net/v1.0/{threads_user}/threads"
            r = requests.post(url, headers={"Authorization": f"Bearer {token}"},
                              data={"media_type": "IMAGE", "image_url": public_url(entry["image"]), "text": caption}, timeout=45)
            state["container"] = r.json()["id"]
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
            raise ValueError(f"Threads container status ({st}): {status_data.get('error_message')}")
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
