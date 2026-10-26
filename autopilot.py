"""No-cost, approval-gated Facebook Page + Instagram photo publisher.

GitHub Actions runs this periodically. All credentials come from Actions secrets.
Only original text and locally drawn, people-free JPEG artwork are generated.
"""
from __future__ import annotations

import base64
import hashlib
import io
from io import BytesIO
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
        for line in env_file.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ[k.strip().lstrip("\ufeff")] = v.strip()

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


def fetch_unsplash_image(query: str, W: int = 1080, H: int = 1350, used_ids: set | None = None) -> Image.Image | None:
    """Fetch high-res photo from Unsplash matching query, with curated fallback."""
    UNSPLASH_CACHE.mkdir(parents=True, exist_ok=True)
    access_key = getenv("UNSPLASH_ACCESS_KEY")

    # 1. Clean query of noise tags (e.g. 'STEP 01', 'HOT TREND', etc.)
    raw_query = query or "technology"
    cleaned = re.sub(r"\b(step\s*\d+|hot\s*trend|summary|connect|takeaway|breakdown)\b", "", raw_query, flags=re.IGNORECASE)
    cleaned = re.sub(r"[^\w\s]", "", cleaned).strip()
    if not cleaned or len(cleaned) < 3:
        cleaned = "technology future"

    cache_key = hashlib.sha256(cleaned.lower().encode()).hexdigest()[:12]
    cached_file = UNSPLASH_CACHE / f"v2_{cache_key}.jpg"
    if cached_file.exists() and (used_ids is None or len(used_ids) == 0):
        try:
            return Image.open(cached_file).convert("RGB")
        except Exception:
            pass

    # 2. Candidate queries for graceful keyword relaxation
    words = [w for w in cleaned.split() if len(w) > 2]
    candidate_queries = [cleaned]
    if len(words) > 3:
        candidate_queries.append(" ".join(words[:3]))
        candidate_queries.append(" ".join(words[-3:]))
    if len(words) > 1:
        candidate_queries.append(" ".join(words[:2]))
    candidate_queries.append("modern technology future")

    # 3. Try official Unsplash API if access key is present
    if access_key:
        for cq in candidate_queries:
            try:
                url = "https://api.unsplash.com/search/photos"
                params = {"query": cq, "per_page": 10, "order_by": "relevant"}
                headers = {"Authorization": f"Client-ID {access_key}"}
                r = requests.get(url, params=params, headers=headers, timeout=6)
                if r.status_code == 200:
                    results = r.json().get("results", [])
                    available = [p for p in results if used_ids is None or p.get("id") not in used_ids]
                    if not available:
                        available = results
                    if available:
                        best = max(available, key=lambda p: p.get("likes", 0))
                        pid = best.get("id")
                        if used_ids is not None and pid:
                            used_ids.add(pid)
                        img_url = best["urls"].get("regular") or best["urls"].get("small")
                        img_data = requests.get(img_url, timeout=10).content
                        photo = Image.open(io.BytesIO(img_data)).convert("RGB")
                        processed = resize_and_crop(photo, W, H)
                        try:
                            processed.save(cached_file, "JPEG", quality=92)
                        except Exception:
                            pass
                        return processed
            except Exception as e:
                print(f"Unsplash API search warning on '{cq}': {e}")

    # 4. Curated high-res Unsplash photos mapped across topic categories
    CURATED_UNSPLASH = [
        # Cyberpunk / AI / Tech / Microchips
        "https://images.unsplash.com/photo-1618005182384-a83a8bd57fbe?w=1080&q=85",
        "https://images.unsplash.com/photo-1620712943543-bcc4688e7485?w=1080&q=85",
        "https://images.unsplash.com/photo-1635070041078-e363dbe005cb?w=1080&q=85",
        "https://images.unsplash.com/photo-1526374965328-7f61d4dc18c5?w=1080&q=85",
        "https://images.unsplash.com/photo-1550751827-4bd374c3f58b?w=1080&q=85",
        "https://images.unsplash.com/photo-1504384308090-c894fdcc538d?w=1080&q=85",
        "https://images.unsplash.com/photo-1531297484001-80022131f5a1?w=1080&q=85",
        "https://images.unsplash.com/photo-1518770660439-4636190af475?w=1080&q=85",
        # India / Culture / Kolkata / Heritage
        "https://images.unsplash.com/photo-1634015158905-de840be0c62c?w=1080&q=85",
        "https://images.unsplash.com/photo-1593847794002-a67998d742fc?w=1080&q=85",
        "https://images.unsplash.com/photo-1524492412937-b28074a5d7da?w=1080&q=85",
        "https://images.unsplash.com/photo-1582510003544-4d00b7f74220?w=1080&q=85",
        "https://images.unsplash.com/photo-1605649487212-47bdab064df7?w=1080&q=85",
        # Agriculture / Nature / Green Crops
        "https://images.unsplash.com/photo-1560493676-04071c5f467b?w=1080&q=85",
        "https://images.unsplash.com/photo-1500937386664-56d1dfef3854?w=1080&q=85",
        "https://images.unsplash.com/photo-1537401845705-1a04003a2973?w=1080&q=85",
        "https://images.unsplash.com/photo-1608424371207-ab70d9d68e80?w=1080&q=85",
        # Health / Science / Biotech / Wellness
        "https://images.unsplash.com/photo-1532187863486-abf9dbad1b69?w=1080&q=85",
        "https://images.unsplash.com/photo-1505751172876-fa1923c5c528?w=1080&q=85",
        "https://images.unsplash.com/photo-1576086213369-97a306d36557?w=1080&q=85",
        "https://images.unsplash.com/photo-1512069772995-ec65ed45afd6?w=1080&q=85",
        # Global / Futuristic Concepts
        "https://images.unsplash.com/photo-1451187580459-43490279c0fa?w=1080&q=85",
        "https://images.unsplash.com/photo-1486406146926-c627a92ad1ab?w=1080&q=85"
    ]
    idx = int(hashlib.sha256(cleaned.encode()).hexdigest()[:8], 16) % len(CURATED_UNSPLASH)
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


def get_evening_language(date_key):
    """Alternate daily between Hindi and Bengali.
    2026-10-20: Hindi (All India Trend)
    2026-10-21: Bengali (West Bengal Trend)
    2026-10-22: Hindi (All India Trend)
    2026-10-23: Bengali (West Bengal Trend)
    """
    clean_date = date_key.split("_")[0]
    base = date.fromisoformat("2026-10-20")
    target = date.fromisoformat(clean_date)
    diff = (target - base).days
    return "Hindi" if diff % 2 == 0 else "Bengali"


WEEKDAY_TOPIC_MATRIX = {
    0: {  # Monday
        "theme": "Future Tech, AI & Tools",
        "global": [
            ("Tech & AI Horizons", "(AI breakthrough OR generative AI OR new tech tools OR robotics) when:2d", "en-US", "US:en"),
            ("Consumer Tech & Smart Devices", "(Apple OR Google OR Nvidia OR smartphone innovation) when:2d", "en-US", "US:en")
        ],
        "india": [
            ("डिजिटल भारत & AI तकनीक", "(भारत AI OR डिजिटल भारत OR Indian tech startups OR AI tools) when:2d", "hi", "IN:hi"),
            ("स्मार्ट तकनीक & भारतीय नवाचार", "(स्मार्टफोन भारत OR भारतीय तकनीक नवाचार) when:2d", "hi", "IN:hi")
        ],
        "bengal": [
            ("আধুনিক প্রযুক্তি ও AI উদ্ভাবন", "(কলকাতা প্রযুক্তি OR পশ্চিমবঙ্গ বিজ্ঞান OR AI প্রযুক্তি) when:3d", "bn", "IN:bn"),
            ("ডিজিটাল বাংলা ও স্মার্ট গ্যাজেট", "(স্মার্ট প্রযুক্তি OR নতুন গ্যাজেট OR ডিজিটাল উদ্যোগ) when:3d", "bn", "IN:bn")
        ],
        "angle": "a practical, insightful breakdown of the latest technology or AI trend shaping our future"
    },
    1: {  # Tuesday
        "theme": "Smart Productivity, Focus & Daily Systems",
        "global": [
            ("Productivity & Brain Mastery", "(productivity hack OR deep work OR mental models OR habit building OR focus tips) when:3d", "en-US", "US:en"),
            ("Modern Workflow & Time Systems", "(time management OR digital minimalism OR organization routines) when:3d", "en-US", "US:en")
        ],
        "india": [
            ("सफलता के नियम & कार्यक्षमता", "(उत्पादकता टिप्स OR समय प्रबंधन OR सफलता की आदतें OR करियर में तरक्की) when:3d", "hi", "IN:hi"),
            ("स्मार्ट जीवनशैली & अनुशासन", "(आत्म विकास OR कार्यकुशलता OR फोकस टिप्स) when:3d", "hi", "IN:hi")
        ],
        "bengal": [
            ("কর্মদক্ষতা ও সাফল্যের অভ্যাস", "(কর্মদক্ষতা OR সময় সচেতনতা OR একাগ্রতা ও সাফল্য OR কাজের সহজ নিয়ম) when:3d", "bn", "IN:bn"),
            ("স্মার্ট জীবন ও আত্মবিকাশ", "(আত্মউন্নয়ন OR মানসিক একাগ্রতা OR সফল হওয়ার উপায়) when:3d", "bn", "IN:bn")
        ],
        "angle": "actionable daily systems, cognitive frameworks, and habit design to multiply focus and eliminate friction"
    },
    2: {  # Wednesday
        "theme": "Science, Cosmos & Mysteries of Nature",
        "global": [
            ("Cosmos & Deep Science Mysteries", "(space discovery OR astronomy OR James Webb OR neuroscience OR ocean discovery) when:3d", "en-US", "US:en"),
            ("Frontier Physics & Nature Wonders", "(quantum physics OR nature phenomena OR earth science breakthrough) when:3d", "en-US", "US:en")
        ],
        "india": [
            ("इसरो & भारत का अंतरिक्ष विज्ञान", "(ISRO space mission OR भारत अंतरिक्ष OR भारतीय वैज्ञानिक चमत्कार OR गगनयान) when:3d", "hi", "IN:hi"),
            ("प्रकृति & विज्ञान के रहस्य", "(ब्रह्मांड के रहस्य OR विज्ञान खोज भारत) when:3d", "hi", "IN:hi")
        ],
        "bengal": [
            ("মহাকাশ বিজ্ঞান ও গবেষণার রহস্য", "(মহাকাশ গবেষণা OR ভারতীয় বিজ্ঞান OR প্রকৃতির বিস্ময় OR ইসরো) when:3d", "bn", "IN:bn"),
            ("বিজ্ঞানের চমক ও আবিষ্কার", "(বিজ্ঞান আবিষ্কার OR জ্যোতির্বিজ্ঞান OR বিস্ময়কর তথ্য) when:3d", "bn", "IN:bn")
        ],
        "angle": "a mind-expanding dive into a fascinating scientific discovery, cosmic event, or nature phenomenon"
    },
    3: {  # Thursday
        "theme": "Health, Longevity, Nutrition & Mind",
        "global": [
            ("Longevity, Wellness & Biohacking", "(longevity science OR sleep optimization OR nutrition health OR metabolic health) when:3d", "en-US", "US:en"),
            ("Mental Fitness & Modern Well-being", "(mental health wellness OR brain health science OR stress resilience) when:3d", "en-US", "US:en")
        ],
        "india": [
            ("स्वास्थ्य, आयुर्वेद & आधुनिक वेलनेस", "(स्वस्थ जीवनशैली OR सही पोषण OR योग आयुर्वेद वेलनेस OR फिटनेस मंत्र) when:3d", "hi", "IN:hi"),
            ("मानसिक शांति & संपूर्ण तंदुरुस्ती", "(मानसिक स्वास्थ्य OR तनाव मुक्ति OR सेहतमंद दिनचर्या) when:3d", "hi", "IN:hi")
        ],
        "bengal": [
            ("সুস্থ জীবনযাপন, পুষ্টি ও স্বাস্থ্য", "(সুস্থ জীবনযাপন OR পুষ্টি সচেতনতা OR ঘরোয়া স্বাস্থ্য টিপস OR ঋতুভিত্তিক যত্ন) when:3d", "bn", "IN:bn"),
            ("মানসিক শান্তি ও দীর্ঘায়ু", "(মন ভালো রাখার উপায় OR মানসিক স্বাস্থ্য OR দৈনন্দিন সুস্থতা) when:3d", "bn", "IN:bn")
        ],
        "angle": "science-backed lifestyle secrets, nutrition habits, and wellness principles for vitality and energy"
    },
    4: {  # Friday
        "theme": "Future Wealth, Startups & Entrepreneurship",
        "global": [
            ("Future Wealth & Global Startups", "(game changing startups OR business strategy OR creator economy OR future of work) when:3d", "en-US", "US:en"),
            ("Fintech & Modern Investing Mindset", "(fintech innovation OR investing psychology OR smart money trends) when:3d", "en-US", "US:en")
        ],
        "india": [
            ("भारतीय स्टार्टअप्स & बिजनेस सूत्र", "(Indian startups OR भारत यूनिकॉर्न OR नया बिजनेस आइडिया OR वित्तीय समझदारी) when:3d", "hi", "IN:hi"),
            ("आत्मनिर्भर भारत & उद्यमिता", "(उद्यमिता सफलता OR मेक इन इंडिया स्टार्टअप) when:3d", "hi", "IN:hi")
        ],
        "bengal": [
            ("বাংলার উদ্যোগ ও স্বাবলম্বিতা", "(বাংলার উদ্যোক্তা OR নতুন ব্যবসা উদ্যোগ OR সৃজনশীল কাজ ও আয়) when:3d", "bn", "IN:bn"),
            ("স্মার্ট ক্যারিয়ার ও ভবিষ্যৎ অর্থ", "(ক্যারিয়ার টিপস OR আর্থিক স্বাধীনতা OR বাংলার নতুন উদ্যোগ) when:3d", "bn", "IN:bn")
        ],
        "angle": "sharp business tactics, founder lessons, and modern wealth-building mental models"
    },
    5: {  # Saturday
        "theme": "Culture, Heritage, Festivals & Stories",
        "global": [
            ("Living Culture, Architecture & Design", "(cultural heritage OR architectural marvels OR festival traditions OR living crafts) when:4d", "en-US", "US:en"),
            ("Creative Arts & Cultural Horizons", "(world festivals OR cultural history OR artistic innovation) when:4d", "en-US", "US:en")
        ],
        "india": [
            ("भारतीय संस्कृति, त्यौहार & गौरव", "(भारतीय संस्कृति OR त्यौहार उत्सव OR ऐतिहासिक धरोहर OR दिवाली होली पूजा) when:4d", "hi", "IN:hi"),
            ("भारत की महान गाथा & विरासत", "(भारत का इतिहास OR प्राचीन मंदिर वास्तुकला OR भारतीय कला) when:4d", "hi", "IN:hi")
        ],
        "bengal": [
            ("বাংলার উৎসব, ঐতিহ্য ও সংস্কৃতি", "(দুর্গাপূজা OR কালীপূজা OR বাংলা সংস্কৃতি ঐতিহ্য OR টেরাকোটা মন্দির) when:4d", "bn", "IN:bn"),
            ("সাহিত্য, শিল্পকলা ও বাংলার গর্ব", "(বাংলা সাহিত্য OR রবীন্দ্র নজরুল OR বাংলার কারুশিল্প ও মিষ্টি) when:4d", "bn", "IN:bn")
        ],
        "angle": "celebrating rich cultural roots, festive grandeur, architectural wonders, and living heritage"
    },
    6: {  # Sunday
        "theme": "Human Wisdom, Life Paradoxes & Stories",
        "global": [
            ("Human Wisdom & Paradoxes of Life", "(human psychology insights OR philosophical lessons OR life paradoxes) when:4d", "en-US", "US:en"),
            ("Fascinating Historical Insights", "(fascinating facts history OR timeless human wisdom) when:4d", "en-US", "US:en")
        ],
        "india": [
            ("प्रेरणादायक कहानियाँ & जीवन दर्शन", "(प्रेरणादायक कहानी OR जीवन दर्शन सीख OR भारतीय महापुरुष विचार) when:4d", "hi", "IN:hi"),
            ("अनोखे तथ्य & भारत की सीख", "(रोचक तथ्य भारत OR जीवन में सकारात्मकता) when:4d", "hi", "IN:hi")
        ],
        "bengal": [
            ("রোমাঞ্চকর অজানা তথ্য ও জীবন দর্শন", "(অজানা ঐতিহাসিক তথ্য OR অনুপ্রেরণামূলক কাহিনী OR জীবনের চিরন্তন শিক্ষা) when:4d", "bn", "IN:bn"),
            ("বাংলার স্মরণীয় মনীষী ও গল্প", "(বাংলার মনীষী OR ভাবনার নতুন আলো OR কালজয়ী গল্প) when:4d", "bn", "IN:bn")
        ],
        "angle": "soulful reflections, thought-provoking paradoxes, and inspiring real-life human stories"
    }
}


def fetch_web_feed_topic(date_key=None, revision=1, recent_titles=None, feed_type="global", max_age_hours=48, weekday_idx=None):
    """Fetch real-time trending topics across 7 rotating weekday pillars (Tech, Productivity, Science, Health, Wealth, Culture, Wisdom)."""
    import urllib.request
    import urllib.parse
    import xml.etree.ElementTree as ET
    import email.utils
    from datetime import datetime, timezone, date

    now = datetime.now(timezone.utc)
    if weekday_idx is None:
        if date_key:
            try:
                weekday_idx = date.fromisoformat(date_key.split("_")[0]).weekday()
            except Exception:
                weekday_idx = 0
        else:
            weekday_idx = datetime.now(IST).weekday()

    # Look up today's thematic channels from WEEKDAY_TOPIC_MATRIX
    theme_info = WEEKDAY_TOPIC_MATRIX.get(weekday_idx, WEEKDAY_TOPIC_MATRIX[0])
    channels = theme_info.get(feed_type, theme_info["global"])
    ordered_channels = channels[(revision - 1) % len(channels):] + channels[:(revision - 1) % len(channels)]

    for category, query_str, hl, ceid in ordered_channels:
        try:
            feed_url = f"https://news.google.com/rss/search?q={urllib.parse.quote(query_str)}&hl={hl}&gl=IN&ceid={ceid}"
            req = urllib.request.Request(feed_url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
            xml_data = urllib.request.urlopen(req, timeout=8).read()
            root = ET.fromstring(xml_data)
            items = root.findall(".//item")
            if not items:
                continue

            valid_items = []
            for item in items:
                title = item.find("title").text if item.find("title") is not None else ""
                clean_title = title.rsplit(" - ", 1)[0] if " - " in title else title
                source_name = title.rsplit(" - ", 1)[1] if " - " in title else "Live News"
                clean_title = clean_title.strip()
                link = item.find("link").text if item.find("link") is not None else ""

                if not clean_title or len(clean_title) < 10:
                    continue

                pub_date_elem = item.find("pubDate")
                pub_hours = 0
                pub_str = "Today"
                if pub_date_elem is not None and pub_date_elem.text:
                    try:
                        pub_dt = email.utils.parsedate_to_datetime(pub_date_elem.text)
                        if pub_dt.tzinfo is None:
                            pub_dt = pub_dt.replace(tzinfo=timezone.utc)
                        age_hours = (now - pub_dt).total_seconds() / 3600
                        if age_hours > max_age_hours or age_hours < -2:
                            continue
                        pub_hours = round(age_hours, 1)
                        pub_str = f"{pub_hours}h ago ({pub_dt.strftime('%b %d, %H:%M UTC')})"
                    except Exception:
                        pass

                if recent_titles and any(similar(normalize(clean_title), normalize(t)) for t in recent_titles):
                    continue

                valid_items.append({
                    "theme": theme_info["theme"],
                    "category": category,
                    "headline": clean_title,
                    "source": source_name,
                    "url": link,
                    "pub_str": pub_str,
                    "age_hours": pub_hours,
                    "angle": theme_info["angle"]
                })

            if valid_items:
                selected = valid_items[(revision - 1) % len(valid_items)]
                return selected
        except Exception as exc:
            print(f"Web feed fetch warning ({category}): {exc}")
            continue

    # Secondary thematic fallback ensuring fresh trend coverage
    try:
        fb_cat, fb_query, fb_hl, fb_ceid = channels[0]
        fallback_url = f"https://news.google.com/rss/search?q={urllib.parse.quote(fb_query)}&hl={fb_hl}&gl=IN&ceid={fb_ceid}"
        req = urllib.request.Request(fallback_url, headers={"User-Agent": "Mozilla/5.0"})
        xml_data = urllib.request.urlopen(req, timeout=6).read()
        root = ET.fromstring(xml_data)
        for item in root.findall(".//item")[:10]:
            title = item.find("title").text if item.find("title") is not None else ""
            clean_title = title.rsplit(" - ", 1)[0] if " - " in title else title
            source_name = title.rsplit(" - ", 1)[1] if " - " in title else "Live Trend"
            if clean_title and len(clean_title) > 10:
                if recent_titles and any(similar(normalize(clean_title), normalize(t)) for t in recent_titles):
                    continue
                return {
                    "theme": theme_info["theme"],
                    "category": fb_cat,
                    "headline": clean_title.strip(),
                    "source": source_name,
                    "url": item.find("link").text if item.find("link") is not None else "",
                    "pub_str": "Today",
                    "age_hours": 2,
                    "angle": theme_info["angle"]
                }
    except Exception:
        pass
    return None


def choose_topic(date_key, revision=1, use_web=False, recent_titles=None, slot_type="afternoon"):
    """Select a consistent weekday pillar or live web feed based on slot & language."""
    clean_date = date_key.split("_")[0]
    day = date.fromisoformat(clean_date)

    if slot_type == "afternoon":
        feed_key = "global"
        language = "English"
    else:
        language = get_evening_language(clean_date)
        feed_key = "india" if language == "Hindi" else "bengal"

    if use_web:
        web = fetch_web_feed_topic(clean_date, revision, recent_titles=recent_titles, feed_type=feed_key, weekday_idx=day.weekday())
        if web:
            return {
                "type": "practical",
                "theme": web.get("theme", "Daily Trend"),
                "category": web["category"],
                "direction": web["headline"],
                "angle": web.get("angle", f"a timely, in-depth breakdown of today's trend ({web.get('pub_str', 'Today')})"),
                "source": {"label": web["source"], "url": web.get("url")} if web.get("url") else None,
                "news_source": web.get("source", "Live Trending News"),
                "pub_str": web.get("pub_str", "Today"),
                "web_url": web.get("url"),
                "language": language,
                "slot": slot_type
            }
    plan = read_json(CALENDAR, {})
    days = plan.get("weekdays", [])
    if len(days) != 7:
        raise RuntimeError("Mixed content calendar requires exactly seven weekday entries")
    slot = dict(days[day.weekday()])
    slot["language"] = language
    slot["slot"] = slot_type
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
    if not (2 <= len(item["title"].split()) <= 14):
        raise RuntimeError("Visual headline must have 2–14 words")
    content = f"{item['title']} {item['subtitle']} {item['caption']}".lower()
    # Prevent unattributed quotations, made-up research/news, spam and unapproved URLs.
    if re.search(r'https?://|www\.|@[a-z0-9_]+|[“”\"«»]', content, re.I):
        raise RuntimeError("No quoted/attributed content, external links or @handles in generated copy")
    if re.search(r'\b(latest news|breaking news|new study|according to (a )?study|scientists say|guaranteed|100%|proven cure|financial advice|as an ai|buy now|limited offer)\b', content, re.I):
        raise RuntimeError("Unsupported claims, news, or marketing language blocked")
    if not slot.get("news_source") and re.search(r'\b\d+(?:[.,]\d+)?\s*(?:%|percent|million|billion|times)(?!\w)', content, re.I):
        raise RuntimeError("Unreviewed statistics or quantitative claims blocked")
    hashtags = re.findall(r'(?<!\w)#[\w]+', item["caption"], re.UNICODE)
    if not 3 <= len(hashtags) <= 8:
        raise RuntimeError("Captions require 3–8 relevant hashtags")
    recent = list(posts.values())[-90:]
    norm_title = normalize(item["title"])
    norm_caption = normalize(item["caption"])
    if len(norm_title) > 3 and any(similar(norm_title, normalize(p.get("title", ""))) for p in recent):
        raise RuntimeError("Too close to a previous title/caption; no new preview sent")
    if slot["type"] == "sourced" and not slot.get("source"):
        raise RuntimeError("Factual posts must use a source selected by code, never AI")
    item.pop("recommended_audio", None)
    return item


def tri_ai_generate_and_upgrade(prompt, slot_info, gemini_key):
    """Simultaneously query 3 AI models in parallel:
    1. Google Gemini 2.5 Flash
    2. Groq Qwen 3.8 27B (Specialized in Indic/Bengali linguistics)
    3. Groq OpenAI GPT-OSS 120B (Editorial structuring)
    """
    import concurrent.futures
    groq_key = getenv("GROQ_API_KEY")
    candidates = {}

    def fetch_gemini():
        if not gemini_key:
            return None
        model = getenv("GEMINI_MODEL") or "gemini-2.5-flash"
        if not re.fullmatch(r"[a-zA-Z0-9_.-]+", model):
            raise ValueError("Invalid model identifier")
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        body = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"responseMimeType": "application/json", "temperature": 0.75},
        }
        for attempt in range(2):
            try:
                res = requests.post(url, headers={"x-goog-api-key": gemini_key}, json=body, timeout=35)
                if res.status_code == 200:
                    parts = res.json().get("candidates", [{}])[0].get("content", {}).get("parts", [])
                    if parts:
                        return json.loads("".join(p.get("text", "") for p in parts))
            except Exception as e:
                print(f"Gemini API warning: {e}")
            time.sleep(1)
        return None

    def fetch_groq_qwen():
        if not groq_key:
            return None
        try:
            res = requests.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {groq_key}", "Content-Type": "application/json"},
                json={
                    "model": "qwen/qwen3.8-27b",
                    "messages": [
                        {"role": "system", "content": "You are a master social media content strategist and Indic linguistic specialist. Return ONLY valid JSON."},
                        {"role": "user", "content": prompt}
                    ],
                    "response_format": {"type": "json_object"},
                    "temperature": 0.7
                },
                timeout=25
            )
            if res.status_code == 200:
                payload = res.json()
                content = payload.get("choices", [{}])[0].get("message", {}).get("content", "")
                return json.loads(content)
        except Exception as e:
            print(f"Groq-Qwen warning: {e}")
        return None

    def fetch_groq_gpt_oss():
        if not groq_key:
            return None
        try:
            res = requests.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {groq_key}", "Content-Type": "application/json"},
                json={
                    "model": "openai/gpt-oss-120b",
                    "messages": [
                        {"role": "system", "content": "You are a senior social media director and content editor. Return ONLY valid JSON."},
                        {"role": "user", "content": prompt}
                    ],
                    "response_format": {"type": "json_object"},
                    "temperature": 0.7
                },
                timeout=25
            )
            if res.status_code == 200:
                payload = res.json()
                content = payload.get("choices", [{}])[0].get("message", {}).get("content", "")
                return json.loads(content)
        except Exception as e:
            print(f"Groq-GPT-OSS warning: {e}")
        return None

    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
        f_gemini = executor.submit(fetch_gemini)
        f_qwen = executor.submit(fetch_groq_qwen)
        f_gpt = executor.submit(fetch_groq_gpt_oss)

        try:
            candidates["gemini"] = f_gemini.result()
        except Exception:
            candidates["gemini"] = None
        try:
            candidates["qwen"] = f_qwen.result()
        except Exception:
            candidates["qwen"] = None
        try:
            candidates["gpt_oss"] = f_gpt.result()
        except Exception:
            candidates["gpt_oss"] = None

    valid = [c for c in [candidates.get("gemini"), candidates.get("qwen"), candidates.get("gpt_oss")] if c and isinstance(c, dict) and c.get("title")]

    if not valid:
        raise RuntimeError("AI returned no text; fail closed")

    best = max(valid, key=lambda c: (len(c.get("slides", [])), len(c.get("caption", ""))))
    return best


def generated_content(settings, posts, date_key, revision=1, slot="afternoon"):
    key = getenv("GEMINI_API_KEY", True)
    use_web = settings.get("use_live_web_feed", False)
    clean_date = date_key.split("_")[0]
    recent = [p.get("title", "") for _, p in sorted(posts.items())[-60:]]
    slot_info = choose_topic(clean_date, revision, use_web=use_web, recent_titles=recent, slot_type=slot)
    brand = settings.get("brand", "Choicematrix")
    language = slot_info.get("language", "English")

    if language == "Bengali":
        lang_spec = f"""LANGUAGE & SCRIPT REQUIREMENT: BENGALI (বাংলা)
- Write the entire content in natural, engaging, grammatically correct BENGALI (বাংলা).
- Visual Title, subtitle, caption, all 6 slide headlines, and all slide body text MUST be written in Bengali script.
- Provide an appropriate transliterated hashtag list (e.g. #KolkataTech #WestBengalInnovation #TechBangla #Choicematrix).
- Provide a targeted 'unsplash_query' in ENGLISH (3-5 evocative, cinematic words, e.g., 'kolkata heritage howrah bridge golden hour', 'kumartuli durga idol clay sculpting', 'rural bengal green jute farm').
- Slide 6 CTA:
  headline: "তথ্যটি ভালো লেগেছে কি? 💡"
  body: "Follow @{brand.lower()} প্রতিদিনের সেরা টেক ট্রেন্ড, শিক্ষা ও উদ্ভাবনের আপডেটের জন্য! 🚀🔥✨\\n\\n📌 পোস্টটি সেভ করে রাখুন\\n↗️ বন্ধুদের সাথে শেয়ার করুন"
"""
    elif language == "Hindi":
        lang_spec = f"""LANGUAGE & SCRIPT REQUIREMENT: HINDI (हिंदी)
- Write the entire content in natural, conversational, fluent HINDI (हिंदी).
- Visual Title, subtitle, caption, all 6 slide headlines, and all slide body text MUST be written in Devanagari Hindi script.
- Provide an appropriate transliterated hashtag list (e.g. #TechInHindi #DigitalIndia #IndiaTech #Choicematrix).
- Provide a targeted 'unsplash_query' in ENGLISH (3-5 evocative, cinematic words, e.g., 'india modern technology robotic lab', 'bengaluru tech campus futuristic', 'indian semiconductor innovation').
- Slide 6 CTA:
  headline: "क्या यह जानकारी उपयोगी लगी? 💡"
  body: "@choicematrix को फॉलो करें रोज़ाना बेहतरीन टेक इनसाइट्स, स्मार्ट टिप्स और ट्रेंडिंग अपडेट्स के लिए! 🚀🔥✨\\n\\n📌 इस पोस्ट को सेव करें\\n↗️ दोस्तों के साथ शेयर करें"
"""
    else:
        lang_spec = f"""LANGUAGE: ENGLISH
- Write in crisp, high-impact, globally relevant English.
- Provide a targeted 'unsplash_query' in ENGLISH (3-5 evocative, cinematic words, e.g., 'futuristic quantum computing processor glowing', 'cyberpunk high tech server datacenter', 'holographic user interface display').
- Slide 6 CTA:
  headline: "FOUND THIS HELPFUL? 💡"
  body: "Follow @{brand.lower()} for more daily global tech breakthroughs, smart hacks & productivity frameworks! 🚀🔥✨\\n\\n📌 Save this post for later\\n↗️ Share with a friend"
"""

    prompt = f"""You are a master social media content strategist for '{brand}'.
Create an ultra-high-value, highly engaging 6-SLIDE CAROUSEL post based on today's topic.
Brand: {brand}.
Topic Category: {slot_info['category']}
Headline or Direction: {slot_info.get('direction', slot_info.get('seed', 'Modern Tech Innovation'))}
News Source: {slot_info.get('news_source', 'Live Breaking Feed')}
Published Time: {slot_info.get('pub_str', 'Today')}
Angle: {slot_info['angle']}
Audience: {settings['audience']}; Target Language: {language}.
Style: {settings['content_style']}.

CRITICAL THEME & TOPIC DIVERSITY RULE:
- Today's thematic pillar is: {slot_info.get('theme', 'Daily Trend')} ({slot_info.get('category', 'General')}).
- Topic Headline / Trend: "{slot_info.get('direction', '')}".
- Angle & Focus: {slot_info.get('angle', 'an insightful breakdown')}.
- THEMATIC VISUAL & CONTENT RULES:
  * If Culture / Festivals (e.g. Durga Puja, Kali Puja, Diwali, Indian heritage, architecture, traditional arts):
    Celebrate the cultural grandeur, festive joy, history, and community spirit.
    Slide 1 front image query MUST be an authentic cultural photograph (e.g. 'kumartuli maa durga idol sculpting kolkata' or 'diwali traditional diya oil lamp glowing golden hour').
  * If Technology / AI Tools:
    Explain practical applications, architecture, and real-world tools with high-tech / clean hardware visuals.
  * If Health / Longevity / Wellness:
    Provide science-backed habits, nutrition, and daily vitality routines with fresh organic lifestyle visuals.
  * If Productivity / Mindset / Career:
    Provide actionable frameworks, daily micro-systems, and time management strategies.
  * If Startups / Business / Wealth:
    Dissect business models, founder mindset, and modern wealth-building principles.
  * If Science / Cosmos / Mysteries:
    Bring out the awe-inspiring cosmic discoveries, astronomy, and universe wonders.
- Do NOT use dry, boring news reports or generic filler. Deliver rich, substantial, highly engaging storytelling that stops users mid-scroll!

{lang_spec}

DEPTH, EMOJI & VISUAL REQUIREMENTS:
- Provide rich depth, specific takeaways, and substantial explanations on each slide. Avoid shallow, generic bullet points.
- Format the CAPTION beautifully with stylish section headers, relevant emojis (🚀, 🔍, 💡, ⚡, 📊, 🎯, 💬), and clean line breaks:
  Format structure:
  🚀 [HIGH-IMPACT EMOJI HOOK TITLE]
  🔍 [WHAT HAPPENED TODAY]: 2-3 engaging sentences explaining the breaking news.
  💡 [CORE TAKEAWAYS]:
  • ⚡ 1. Point one...
  • 📊 2. Point two...
  • 🎯 3. Point three...
  🔥 [WHY THIS MATTERS NOW]: Thoughtful perspective.
  💬 [CALL TO ACTION QUESTION]: Prompting users to comment their perspective.
  #Hashtags (including #{brand})
- For EVERY slide, provide a dedicated 'image_query' (2-3 targeted English words for Unsplash photos, e.g. 'technology data chart', 'artificial intelligence neural network', 'indian space rocket', 'kolkata heritage street', 'teacher classroom').
- For slides 2-5, provide a 'visual_badge' object: {{"label": "E.g. 📊 BENCHMARK METRIC or 📍 LOCATION", "value": "E.g. +340% ACCELERATION or ১ টাকার মডেল", "type": "chart" or "badge"}}.
- Slide 1: High-impact hook and clear problem/opportunity overview with a compelling teaser.
- Slides 2-5: Detailed 4-6 sentence comprehensive breakdown (65 to 95 words per slide). Explain the core logic, actionable implementation steps, real-world examples, and nuanced pro tips that make the reader stop and read every sentence.
- Slide 6: Follow & Save CTA matching the language instructions above.

Return ONLY ONE valid JSON object with this exact structure:
{{
  "title": "Strong title (3-7 words)",
  "subtitle": "Clear explanation hook of what readers will learn (max 140 chars)",
  "caption": "Full high-value social caption (max 1100 chars) breaking down the essence, ending with 3-5 hashtags including #{brand}.",
  "unsplash_query": "2-3 word photo query",
  "language": "{language}",
  "recommended_audio": {{
    "title": "Track Title",
    "artist": "Artist Name",
    "vibe": "Genre / Vibe",
    "search_query": "Exact search query for Instagram/TikTok audio picker"
  }},
  "slides": [
    {{
      "slide_number": 1,
      "tag": "HOT TREND",
      "headline": "Bold 3-6 Word Headline",
      "body": "Compelling 3-4 sentence overview breaking down the problem or opportunity.",
      "swipe_prompt": "Swipe to explore ➡️",
      "image_query": "Targeted 3-4 word English visual query for cover photo (e.g. 'futuristic glowing smartphone display' or 'durga idol kumartuli celebration')"
    }},
    {{
      "slide_number": 2,
      "tag": "STEP 01",
      "headline": "The Core Foundation",
      "body": "Detailed 4-6 sentence deep dive explaining the foundational mechanism, why traditional approaches fail, and what the new breakthrough does differently.",
      "image_query": "Targeted 3-4 word English visual query for foundation concept (e.g. 'circuit board microchip glowing' or 'artisan sculpting clay sculpture')"
    }},
    {{
      "slide_number": 3,
      "tag": "STEP 02",
      "headline": "Actionable Implementation",
      "body": "Comprehensive 4-6 sentence breakdown of the exact step-by-step process, tools, metrics, and workflows to apply immediately.",
      "image_query": "Targeted 3-4 word English visual query for process/workflow (e.g. 'ai neural network glowing' or 'rural agriculture field green')"
    }},
    {{
      "slide_number": 4,
      "tag": "STEP 03",
      "headline": "Nuance & Pro Insights",
      "body": "In-depth 4-6 sentence analysis of common pitfalls to avoid, insider optimizations, and high-leverage habits that multiply your results.",
      "image_query": "Targeted 3-4 word English visual query for deep engineering or nuance (e.g. 'microchip processor circuit macro' or 'traditional weaving golden jute')"
    }},
    {{
      "slide_number": 5,
      "tag": "SUMMARY",
      "headline": "The Master Takeaway",
      "body": "Full synthesized conclusion uniting the entire framework into a memorable, actionable takeaway ready to put into practice today.",
      "image_query": "Targeted 3-4 word English visual query for future vision/impact (e.g. 'futuristic city skyline night' or 'happy community gathering celebration')"
    }},
    {{
      "slide_number": 6,
      "tag": "CONNECT",
      "headline": "Follow CTA Headline",
      "body": "Follow CTA body"
    }}
  ]
}}
"""
    # Call 3 AI models simultaneously (Gemini + Groq Qwen + Groq GPT-OSS)
    item = tri_ai_generate_and_upgrade(prompt, slot_info, key)
    if item and isinstance(item, dict):
        for field in ("title", "subtitle", "caption"):
            if field in item and isinstance(item[field], str):
                item[field] = re.sub(r'[“”"«»]', '', item[field]).strip()
        if "caption" in item and isinstance(item["caption"], str):
            item["caption"] = re.sub(r'@[a-zA-Z0-9_]+', brand, item["caption"])
            item["caption"] = re.sub(r'https?://\S+|www\.\S+', '', item["caption"]).strip()
    item = validate_ai_copy(item, posts, slot_info)
    item["category"] = slot_info["category"]
    item["content_type"] = slot_info["type"]
    item["language"] = slot_info.get("language", language)
    item["slot"] = slot
    item["source"] = slot_info.get("source")
    if slot_info.get("source"):
        item["caption"] += f"\n\nReference: {slot_info['source']['label']} — {slot_info['source']['url']}"
    item.pop("recommended_audio", None)
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
        clean_date = entry.get("clean_date") or str(entry.get("date", "")).split("_")[0]
        date_started = datetime.fromisoformat(clean_date + "T00:00:00").replace(tzinfo=IST)
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
    return re.sub(r"[\s\W_]+", "", value.lower(), flags=re.UNICODE)


def similar(a, b):
    # Exact and high-overlap title matches are blocked, preserving topic diversity.
    import difflib
    if not a or not b:
        return False
    return bool(a == b or difflib.SequenceMatcher(None, a, b).ratio() > 0.82)


def font(size, bold=True, language="English"):
    families = []
    if language == "Bengali":
        families = [
            str(ROOT / "fonts" / ("NotoSansBengali-Bold.ttf" if bold else "NotoSansBengali-Regular.ttf")),
            "C:\\Windows\\Fonts\\Nirmala.ttc",
            "/usr/share/fonts/truetype/noto/NotoSansBengali-Bold.ttf"
        ]
    elif language == "Hindi":
        families = [
            str(ROOT / "fonts" / ("NotoSansDevanagari-Bold.ttf" if bold else "NotoSansDevanagari-Regular.ttf")),
            "C:\\Windows\\Fonts\\Nirmala.ttc",
            "/usr/share/fonts/truetype/noto/NotoSansDevanagari-Bold.ttf"
        ]
    else:
        families = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
            "C:\\Windows\\Fonts\\arialbd.ttf" if bold else "C:\\Windows\\Fonts\\arial.ttf",
            "C:\\Windows\\Fonts\\segoeuib.ttf" if bold else "C:\\Windows\\Fonts\\segoeui.ttf",
        ]
    for item in families:
        if Path(item).exists():
            try:
                return ImageFont.truetype(item, size)
            except Exception:
                pass
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


def to_indic_digits(num_str, language):
    if language == "Bengali":
        return str(num_str).translate(str.maketrans("0123456789", "০১২৩৪৫৬৭৮৯"))
    elif language == "Hindi":
        return str(num_str).translate(str.maketrans("0123456789", "०१२३४५६७८९"))
    return str(num_str)


THEMES_CYBER = [
    {
        "name": "Cyber Cyan",
        "bg_top": (8, 14, 28), "bg_bot": (15, 25, 48), "card_bg": (18, 30, 56),
        "accent": (0, 242, 254), "accent_secondary": (255, 184, 0),
        "text_primary": (255, 255, 255), "text_secondary": (170, 195, 225), "border": (35, 65, 105)
    },
    {
        "name": "Electric Violet",
        "bg_top": (14, 12, 34), "bg_bot": (28, 18, 62), "card_bg": (36, 22, 74),
        "accent": (168, 85, 247), "accent_secondary": (56, 189, 248),
        "text_primary": (255, 255, 255), "text_secondary": (220, 210, 245), "border": (68, 45, 115)
    },
    {
        "name": "Matrix Emerald",
        "bg_top": (6, 26, 22), "bg_bot": (10, 48, 40), "card_bg": (14, 64, 54),
        "accent": (52, 211, 153), "accent_secondary": (167, 243, 208),
        "text_primary": (255, 255, 255), "text_secondary": (209, 250, 229), "border": (25, 85, 72)
    },
    {
        "name": "Cyber Amber",
        "bg_top": (16, 18, 26), "bg_bot": (28, 32, 44), "card_bg": (35, 40, 56),
        "accent": (251, 191, 36), "accent_secondary": (245, 158, 11),
        "text_primary": (255, 255, 255), "text_secondary": (226, 232, 240), "border": (60, 75, 100)
    },
    {
        "name": "Deep Cobalt",
        "bg_top": (10, 22, 40), "bg_bot": (22, 48, 82), "card_bg": (28, 58, 96),
        "accent": (56, 189, 248), "accent_secondary": (0, 255, 210),
        "text_primary": (255, 255, 255), "text_secondary": (215, 230, 255), "border": (45, 85, 140)
    },
    {
        "name": "Cosmic Nebula",
        "bg_top": (8, 8, 24), "bg_bot": (18, 14, 48), "card_bg": (26, 20, 64),
        "accent": (192, 132, 252), "accent_secondary": (250, 204, 21),
        "text_primary": (255, 255, 255), "text_secondary": (224, 215, 250), "border": (70, 50, 120)
    },
    {
        "name": "Neon Crimson",
        "bg_top": (22, 10, 18), "bg_bot": (42, 16, 30), "card_bg": (48, 20, 36),
        "accent": (244, 63, 94), "accent_secondary": (251, 146, 60),
        "text_primary": (255, 255, 255), "text_secondary": (254, 205, 215), "border": (105, 35, 65)
    },
    {
        "name": "Stealth Obsidian",
        "bg_top": (12, 14, 18), "bg_bot": (20, 24, 30), "card_bg": (28, 34, 42),
        "accent": (163, 230, 53), "accent_secondary": (226, 232, 240),
        "text_primary": (255, 255, 255), "text_secondary": (203, 213, 225), "border": (55, 65, 80)
    },
    {
        "name": "Quantum Teal",
        "bg_top": (6, 20, 26), "bg_bot": (12, 38, 48), "card_bg": (16, 48, 62),
        "accent": (45, 212, 191), "accent_secondary": (251, 191, 36),
        "text_primary": (255, 255, 255), "text_secondary": (204, 251, 241), "border": (28, 75, 92)
    },
    {
        "name": "Solaris Plasma",
        "bg_top": (24, 12, 10), "bg_bot": (46, 20, 14), "card_bg": (54, 24, 18),
        "accent": (249, 115, 22), "accent_secondary": (253, 224, 71),
        "text_primary": (255, 255, 255), "text_secondary": (254, 215, 170), "border": (110, 48, 28)
    }
]

THEMES_WARM = [
    {
        "name": "Royal Terracotta",
        "bg_top": (42, 16, 18), "bg_bot": (70, 26, 24), "card_bg": (56, 20, 22),
        "accent": (245, 158, 11), "accent_secondary": (254, 215, 170),
        "text_primary": (255, 255, 255), "text_secondary": (250, 225, 215), "border": (125, 45, 38)
    },
    {
        "name": "Bengal Indigo",
        "bg_top": (12, 22, 44), "bg_bot": (24, 40, 72), "card_bg": (20, 32, 60),
        "accent": (251, 191, 36), "accent_secondary": (147, 197, 253),
        "text_primary": (255, 255, 255), "text_secondary": (224, 235, 255), "border": (48, 78, 125)
    },
    {
        "name": "Royal Plum",
        "bg_top": (36, 14, 38), "bg_bot": (60, 20, 58), "card_bg": (48, 16, 46),
        "accent": (245, 158, 11), "accent_secondary": (244, 114, 182),
        "text_primary": (255, 255, 255), "text_secondary": (245, 220, 240), "border": (98, 32, 92)
    },
    {
        "name": "Heritage Forest",
        "bg_top": (16, 34, 24), "bg_bot": (26, 54, 38), "card_bg": (22, 44, 32),
        "accent": (251, 191, 36), "accent_secondary": (167, 243, 208),
        "text_primary": (255, 255, 255), "text_secondary": (215, 245, 225), "border": (45, 85, 58)
    },
    {
        "name": "Kolkata Sunset",
        "bg_top": (35, 20, 35), "bg_bot": (65, 25, 45), "card_bg": (50, 20, 40),
        "accent": (251, 146, 60), "accent_secondary": (254, 240, 138),
        "text_primary": (255, 255, 255), "text_secondary": (254, 225, 210), "border": (110, 42, 65)
    },
    {
        "name": "Marigold Festive",
        "bg_top": (44, 14, 18), "bg_bot": (74, 22, 22), "card_bg": (58, 18, 20),
        "accent": (251, 191, 36), "accent_secondary": (244, 63, 94),
        "text_primary": (255, 255, 255), "text_secondary": (254, 230, 210), "border": (130, 40, 35)
    },
    {
        "name": "Vedic Ochre",
        "bg_top": (36, 20, 14), "bg_bot": (60, 32, 20), "card_bg": (48, 26, 16),
        "accent": (249, 115, 22), "accent_secondary": (253, 224, 71),
        "text_primary": (255, 255, 255), "text_secondary": (254, 235, 220), "border": (108, 52, 28)
    },
    {
        "name": "Kashmir Blossom",
        "bg_top": (32, 14, 28), "bg_bot": (54, 20, 44), "card_bg": (44, 18, 36),
        "accent": (244, 114, 182), "accent_secondary": (251, 191, 36),
        "text_primary": (255, 255, 255), "text_secondary": (251, 225, 240), "border": (92, 34, 76)
    },
    {
        "name": "Sundarban Jade",
        "bg_top": (14, 28, 22), "bg_bot": (22, 48, 36), "card_bg": (18, 40, 30),
        "accent": (52, 211, 153), "accent_secondary": (251, 191, 36),
        "text_primary": (255, 255, 255), "text_secondary": (209, 250, 229), "border": (38, 82, 60)
    },
    {
        "name": "Murshidabad Silk",
        "bg_top": (28, 12, 36), "bg_bot": (50, 18, 60), "card_bg": (40, 16, 48),
        "accent": (234, 179, 8), "accent_secondary": (216, 180, 254),
        "text_primary": (255, 255, 255), "text_secondary": (243, 225, 255), "border": (85, 30, 95)
    }
]

THEMES = THEMES_CYBER


def get_browser_executable():
    paths = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        "/usr/bin/google-chrome",
        "/usr/bin/google-chrome-stable",
        "/usr/bin/chromium",
        "/usr/bin/chromium-browser",
    ]
    for p in paths:
        if Path(p).exists():
            return str(p)
    return None


def render_carousel_slides_chrome(date_key, revision, settings, item, slot="afternoon", browser_exe=None):
    clean_date = date_key.split("_")[0]
    day = date.fromisoformat(clean_date)
    language = item.get("language", "English")
    slides_data = item.get("slides") or []
    if not slides_data:
        slides_data = [
            {"slide_number": 1, "tag": item.get("category", "IDEAS"), "headline": item["title"],
             "body": item["subtitle"], "swipe_prompt": "Swipe to explore ➡️"},
            {"slide_number": 2, "tag": "CONNECT", "headline": "FOUND THIS HELPFUL? 💡",
             "body": f"Follow @{settings['brand'].lower()} for more daily updates! 🚀🔥✨\n\n📌 Save this post\n↗️ Share with a friend"}
        ]
    total_slides = len(slides_data)
    brand = settings.get("brand", "CHOICEMATRIX").upper()
    rendered_paths = []

    # Fonts
    font_face_css = ""
    font_family_css = "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif"
    if language == "Bengali":
        f_b_path = ROOT / "fonts" / "NotoSansBengali-Bold.ttf"
        f_r_path = ROOT / "fonts" / "NotoSansBengali-Regular.ttf"
        if f_b_path.exists() and f_r_path.exists():
            b64_b = base64.b64encode(f_b_path.read_bytes()).decode()
            b64_r = base64.b64encode(f_r_path.read_bytes()).decode()
            font_face_css = f"""
            @font-face {{ font-family: 'NotoBengali'; src: url('data:font/truetype;charset=utf-8;base64,{b64_b}') format('truetype'); font-weight: bold; }}
            @font-face {{ font-family: 'NotoBengali'; src: url('data:font/truetype;charset=utf-8;base64,{b64_r}') format('truetype'); font-weight: normal; }}
            """
            font_family_css = "'NotoBengali', 'Nirmala UI', sans-serif"
    elif language == "Hindi":
        f_b_path = ROOT / "fonts" / "NotoSansDevanagari-Bold.ttf"
        f_r_path = ROOT / "fonts" / "NotoSansDevanagari-Regular.ttf"
        if f_b_path.exists() and f_r_path.exists():
            b64_b = base64.b64encode(f_b_path.read_bytes()).decode()
            b64_r = base64.b64encode(f_r_path.read_bytes()).decode()
            font_face_css = f"""
            @font-face {{ font-family: 'NotoDevanagari'; src: url('data:font/truetype;charset=utf-8;base64,{b64_b}') format('truetype'); font-weight: bold; }}
            @font-face {{ font-family: 'NotoDevanagari'; src: url('data:font/truetype;charset=utf-8;base64,{b64_r}') format('truetype'); font-weight: normal; }}
            """
            font_family_css = "'NotoDevanagari', 'Nirmala UI', sans-serif"

    theme_pool = THEMES_CYBER if slot == "afternoon" else THEMES_WARM
    used_ids = set()

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return None

    with sync_playwright() as p:
        launch_args = {'headless': True}
        if browser_exe:
            launch_args['executable_path'] = browser_exe
        browser = p.chromium.launch(**launch_args)
        page = browser.new_page(viewport={"width": 1080, "height": 1350})

        weekday = day.weekday()
        # Distinct rotating theme per day, slot, and revision:
        base_theme_idx = (weekday * 2 + (0 if slot == "afternoon" else 1) + (revision - 1)) % len(theme_pool)

        for idx, slide in enumerate(slides_data):
            slide_num = slide.get("slide_number", idx + 1)
            theme_idx = (base_theme_idx + (idx % 2)) % len(theme_pool)
            theme = theme_pool[theme_idx]
            tag = slide.get("tag", "HOT TOPIC").upper()
            headline = slide.get("headline", "")
            body = slide.get("body", "")
            audio = item.get("recommended_audio") or {}

            # Unsplash photo (slide-specific query for true variety across all slides)
            slide_tag = slide.get("tag", "")
            slide_img_q = slide.get("image_query") or f"{item.get('unsplash_query', '')} {slide_tag}".strip()
            bg_photo = fetch_unsplash_image(slide_img_q or item.get("title", "technology"), 1080, 1350, used_ids=used_ids)
            bg_b64 = None
            if bg_photo:
                buf = BytesIO()
                bg_photo.save(buf, format="JPEG", quality=90)
                bg_b64 = base64.b64encode(buf.getvalue()).decode()

            accent_hex = f"#{theme['accent'][0]:02x}{theme['accent'][1]:02x}{theme['accent'][2]:02x}"
            accent_sec_hex = f"#{theme['accent_secondary'][0]:02x}{theme['accent_secondary'][1]:02x}{theme['accent_secondary'][2]:02x}"
            card_bg_css = f"rgba({theme['card_bg'][0]}, {theme['card_bg'][1]}, {theme['card_bg'][2]}, 0.82)"
            border_css = f"rgba({theme['accent'][0]}, {theme['accent'][1]}, {theme['accent'][2]}, 0.45)"
            text_sec_css = f"rgb({theme['text_secondary'][0]}, {theme['text_secondary'][1]}, {theme['text_secondary'][2]})"

            if slot == "afternoon":
                counter_str = f"{slide_num:02d} / {total_slides:02d}"
                frame_html = f"""
                <div style="width:100%; height:100%; border:2px solid {border_css}; position:relative; padding:18px; backdrop-filter:blur(8px);">
                  <div style="position:absolute; top:-2px; left:-2px; width:36px; height:36px; border-top:4px solid {accent_hex}; border-left:4px solid {accent_hex};"></div>
                  <div style="position:absolute; top:-2px; right:-2px; width:36px; height:36px; border-top:4px solid {accent_hex}; border-right:4px solid {accent_hex};"></div>
                  <div style="position:absolute; bottom:-2px; left:-2px; width:36px; height:36px; border-bottom:4px solid {accent_hex}; border-left:4px solid {accent_hex};"></div>
                  <div style="position:absolute; bottom:-2px; right:-2px; width:36px; height:36px; border-bottom:4px solid {accent_hex}; border-right:4px solid {accent_hex};"></div>
                  <div style="width:100%; height:100%; display:flex; flex-direction:column; justify-content:space-between; padding:26px 32px;">
                """
                frame_close = "</div></div>"
                pill_style = f"background:{accent_hex}; color:#080c14; border-radius:10px; padding:8px 24px; font-size:20px; font-weight:bold; letter-spacing:1px; box-shadow:0 4px 14px rgba(0,0,0,0.4);"
                card_style = f"background:{card_bg_css}; border:1.5px solid {border_css}; border-radius:20px; padding:24px 30px; backdrop-filter:blur(16px); -webkit-backdrop-filter:blur(16px); box-shadow:0 20px 45px rgba(0,0,0,0.5), inset 0 1px 0 rgba(255,255,255,0.12);"
            else:
                s_ind = to_indic_digits(f"{slide_num:02d}", language)
                t_ind = to_indic_digits(f"{total_slides:02d}", language)
                counter_str = f"{s_ind} / {t_ind}"
                frame_html = f"""
                <div style="width:100%; height:100%; border:3px solid {border_css}; border-radius:36px; padding:12px; position:relative; backdrop-filter:blur(8px);">
                  <div style="width:100%; height:100%; border:1.5px solid {accent_hex}; border-radius:26px; padding:26px 32px; display:flex; flex-direction:column; justify-content:space-between;">
                """
                frame_close = "</div></div>"
                pill_style = f"background:{accent_hex}; color:#1e0c14; border-radius:24px; padding:8px 26px; font-size:20px; font-weight:bold; letter-spacing:0.8px; box-shadow:0 4px 14px rgba(0,0,0,0.4);"
                card_style = f"background:{card_bg_css}; border:1.5px solid {border_css}; border-radius:24px; padding:24px 30px; backdrop-filter:blur(16px); -webkit-backdrop-filter:blur(16px); box-shadow:0 20px 45px rgba(0,0,0,0.5), inset 0 1px 0 rgba(255,255,255,0.14);"

            # Topic domain detection for diagrams and clear photo showcases
            is_tech_topic = (
                slot == "afternoon"
                or any(w in (item.get("title", "") + " " + item.get("category", "") + " " + headline).lower()
                       for w in ["tech", "ai", "model", "quantum", "data", "chip", "software", "cluster", "hpc",
                                 "supercomputer", "startup", "প্রযুক্তি", "সুপারকম্পিউটার", "উদ্যোগ", "সফটওয়্যার",
                                 "तकनीक", "एआई", "सॉफ्टवेयर", "सुपरकंप्यूटर", "स्टार्टअप"])
                or slide.get("tag", "").upper() in ("HOW IT WORKS", "ARCHITECTURE", "SYSTEM", "PIPELINE")
            )

            if slide_num == 1:
                header_tag = "TODAY'S HOT BREAKDOWN" if slot == "afternoon" else ("আজকের বিশেষ অন্তর্দৃষ্টি" if language == "Bengali" else "आज का विशेष विश्लेषण")
                swipe_prompt = slide.get("swipe_prompt") or ("Swipe to explore ➡️" if slot == "afternoon" else ("বিস্তারিত দেখতে সোয়াইপ করুন ➡️" if language == "Bengali" else "आगे जानने के लिए स्वाइप करें ➡️"))
                audio_line = ""

                # PROMINENT 550px COVER HERO PHOTO
                photo_window_html = ""
                if bg_b64:
                    loc_badge = "📍 পশ্চিমবঙ্গ • আসল চিত্র" if language == "Bengali" else ("📍 भारत • लाइव अपडेट्स" if language == "Hindi" else "🌐 GLOBAL FRONTIER • LIVE")
                    ver_badge = "📸 আসল আলোকচিত্র" if language == "Bengali" else ("📸 वास्तविक चित्र" if language == "Hindi" else "📸 VERIFIED VISUAL")
                    photo_window_html = f"""
                    <div style="width:100%; height:550px; border-radius:20px; overflow:hidden; border:2.5px solid {accent_hex}; box-shadow:0 16px 40px rgba(0,0,0,0.75); position:relative; margin-bottom:14px;">
                      <img src="data:image/jpeg;base64,{bg_b64}" style="width:100%; height:100%; object-fit:cover; display:block;" />
                      <div style="position:absolute; bottom:0; left:0; right:0; background:linear-gradient(180deg, transparent 0%, rgba(0,0,0,0.88) 100%); padding:14px 22px; display:flex; justify-content:space-between; align-items:flex-end;">
                        <div style="font-size:21px; font-weight:bold; color:{accent_hex};">{loc_badge}</div>
                        <div style="font-size:15px; color:#ffffff; background:rgba(255,255,255,0.2); border:1px solid {accent_hex}; border-radius:10px; padding:4px 14px;">{ver_badge}</div>
                      </div>
                    </div>
                    """
                    card_body_style = f"background:{card_bg_css}; border:1.5px solid {border_css}; border-radius:18px; padding:18px 24px;"
                    body_font_size = "25px"
                else:
                    card_body_style = card_style
                    body_font_size = "30px"

                content_html = f"""
                <div style="flex:1; display:flex; flex-direction:column; justify-content:space-evenly; margin:8px 0;">
                  <div>
                    <div style="color:{accent_hex}; font-size:22px; font-weight:bold; margin-bottom:6px;">{header_tag}</div>
                    <div style="font-size:45px; font-weight:bold; line-height:1.24; color:#ffffff;">{headline}</div>
                  </div>
                  {photo_window_html}
                  <div style="{card_body_style}">
                    <div style="font-size:{body_font_size}; line-height:1.5; color:{text_sec_css};">{body}</div>
                    {audio_line}
                  </div>
                  <div style="background:{accent_hex}; color:#1a0f28; font-size:23px; font-weight:bold; padding:14px 28px; border-radius:14px; text-align:center;">
                    {swipe_prompt}
                  </div>
                </div>
                """
            elif slide_num == total_slides:
                header_tag = "KEY TAKEAWAY COMPLETED" if slot == "afternoon" else ("গুরুত্বপূর্ণ তথ্যাবলী সমাপ্ত" if language == "Bengali" else "मुख्य बिंदु संपन्न")
                if language == "Bengali":
                    sub_cta = "প্রতিদিনের সেরা টেক ও জ্ঞানমূলক আপডেটের জন্য! 🚀🔥✨"
                    b1, b2, b3 = "📌 ভবিষ্যতের জন্য পোস্টটি সেভ করে রাখুন", "💬 আপনার মতামত কমেন্টে জানান", "↗️ বন্ধুদের সাথে শেয়ার করুন!"
                elif language == "Hindi":
                    sub_cta = "रोज़ाना बेहतरीन टेक इनसाइट्स और स्मार्ट अपडेट्स के लिए! 🚀🔥✨"
                    b1, b2, b3 = "📌 इस पोस्ट को बाद के लिए सेव करें", "💬 कमेंट्स में अपनी राय साझा करें", "↗️ अपने दोस्तों के साथ ज़रूर शेयर करें!"
                else:
                    sub_cta = "For daily hot trends, tech breakthroughs & smart tips! 🚀🔥✨"
                    b1, b2, b3 = "📌 Save this post to revisit anytime", "💬 Share your thoughts in the comments", "↗️ Send to someone who needs this!"

                content_html = f"""
                <div style="flex:1; display:flex; flex-direction:column; justify-content:center; margin:14px 0;">
                  <div style="color:{accent_hex}; font-size:23px; font-weight:bold; margin-bottom:8px;">{header_tag}</div>
                  <div style="font-size:48px; font-weight:bold; line-height:1.24; margin-bottom:22px; color:#ffffff;">{headline}</div>
                  <div style="{card_style}">
                    <div style="font-size:38px; font-weight:bold; color:{accent_hex}; margin-bottom:12px;">👉 FOLLOW @{brand}</div>
                    <div style="font-size:23px; color:{text_sec_css}; margin-bottom:22px;">{sub_cta}</div>
                    <hr style="border:none; border-top:1.5px solid {border_css}; margin:18px 0;">
                    <div style="font-size:27px; line-height:1.95; color:#ffffff;">
                      <div>{b1}</div>
                      <div>{b2}</div>
                      <div>{b3}</div>
                    </div>
                  </div>
                </div>
                """
            elif slide_num == 2 and is_tech_topic:
                # Slide 2 for TECH: SHOW BOTH 280px PHOTO + 4-STEP PIPELINE DIAGRAM
                step_str = f"⚡ STEP 01" if slot == "afternoon" else ("⚡ ধাপ ০১" if language == "Bengali" else "⚡ चरण ०১")
                wf_title = "⚙️ সিস্টেম আর্কিটেকচার ওয়ার্কফ্লো" if language == "Bengali" else ("⚙️ सिस्टम आर्किटेक्चर वर्कफ़्लो" if language == "Hindi" else "⚙️ SYSTEM ARCHITECTURE WORKFLOW")
                live_tag = "লাইভ পাইপলাইন" if language == "Bengali" else ("लाइव पाइपलाइन" if language == "Hindi" else "LIVE PIPELINE")
                tech_tag = "হার্ডওয়্যার ও আর্কিটেকচার" if language == "Bengali" else ("हार्डवेयर विश्लेषण" if language == "Hindi" else "HARDWARE ARCHITECTURE")

                if language == "Bengali":
                    s1_i, s1_t, s1_d = "🚀", "১. ইনপুট", "ফ্রেমওয়ার্ক"
                    s2_i, s2_t, s2_d = "⚡", "২. প্রসেসর", "সুপারকম্পিউটিং"
                    s3_i, s3_t, s3_d = "🧠", "৩. ক্লাস্টার", "নিউরাল মেশ"
                    s4_i, s4_t, s4_d = "🎯", "৪. স্কেল", "বাস্তব প্রয়োগ"
                elif language == "Hindi":
                    s1_i, s1_t, s1_d = "🚀", "१. इनपुट", "फ़्रेमवर्क"
                    s2_i, s2_t, s2_d = "⚡", "२. प्रोसेसर", "क्लाउड कंप्यूटिंग"
                    s3_i, s3_t, s3_d = "🧠", "३. क्लस्टर", "हाई-स्पीड मेश"
                    s4_i, s4_t, s4_d = "🎯", "४. प्रभाव", "व्यावसायिक उपयोग"
                else:
                    s1_i, s1_t, s1_d = "🚀", "1. App Input", "Weights"
                    s2_i, s2_t, s2_d = "⚡", "2. Core Engine", "HPC Unit"
                    s3_i, s3_t, s3_d = "🧠", "3. AI Cluster", "Neural Mesh"
                    s4_i, s4_t, s4_d = "🎯", "4. Global Scale", "Real-Time API"

                diagram_html = f"""
                <div style="background: rgba({theme['accent'][0]}, {theme['accent'][1]}, {theme['accent'][2]}, 0.08); border: 1.5px solid {accent_hex}; border-radius: 16px; padding: 14px 16px; margin-bottom: 12px;">
                  <div style="font-size: 16px; font-weight: bold; color: {accent_hex}; margin-bottom: 10px; display: flex; justify-content: space-between; align-items: center;">
                    <span>{wf_title}</span>
                    <span style="font-size: 13px; color: {accent_sec_hex}; background: rgba(255, 255, 255, 0.1); border: 1px solid {accent_sec_hex}; border-radius: 6px; padding: 2px 8px;">{live_tag}</span>
                  </div>
                  <div style="display: flex; align-items: center; justify-content: space-between; gap: 8px;">
                    <div style="flex: 1; background: rgba({theme['card_bg'][0]}, {theme['card_bg'][1]}, {theme['card_bg'][2]}, 0.95); border: 1px solid {accent_hex}; border-radius: 10px; padding: 10px 4px; text-align: center;">
                      <div style="font-size: 22px;">{s1_i}</div>
                      <div style="font-size: 14px; font-weight: bold; color: #ffffff; margin-top: 3px;">{s1_t}</div>
                      <div style="font-size: 11px; color: {text_sec_css}; margin-top: 2px;">{s1_d}</div>
                    </div>
                    <div style="font-size: 16px; color: {accent_hex};">➔</div>
                    <div style="flex: 1; background: rgba({theme['card_bg'][0]}, {theme['card_bg'][1]}, {theme['card_bg'][2]}, 0.95); border: 1px solid {accent_sec_hex}; border-radius: 10px; padding: 10px 4px; text-align: center;">
                      <div style="font-size: 22px;">{s2_i}</div>
                      <div style="font-size: 14px; font-weight: bold; color: #ffffff; margin-top: 3px;">{s2_t}</div>
                      <div style="font-size: 11px; color: {accent_sec_hex}; margin-top: 2px;">{s2_d}</div>
                    </div>
                    <div style="font-size: 16px; color: {accent_hex};">➔</div>
                    <div style="flex: 1; background: rgba({theme['card_bg'][0]}, {theme['card_bg'][1]}, {theme['card_bg'][2]}, 0.95); border: 1px solid {accent_hex}; border-radius: 10px; padding: 10px 4px; text-align: center;">
                      <div style="font-size: 22px;">{s3_i}</div>
                      <div style="font-size: 14px; font-weight: bold; color: #ffffff; margin-top: 3px;">{s3_t}</div>
                      <div style="font-size: 11px; color: {text_sec_css}; margin-top: 2px;">{s3_d}</div>
                    </div>
                    <div style="font-size: 16px; color: {accent_hex};">➔</div>
                    <div style="flex: 1; background: rgba({theme['card_bg'][0]}, {theme['card_bg'][1]}, {theme['card_bg'][2]}, 0.95); border: 1px solid {accent_hex}; border-radius: 10px; padding: 10px 4px; text-align: center;">
                      <div style="font-size: 22px;">{s4_i}</div>
                      <div style="font-size: 14px; font-weight: bold; color: #ffffff; margin-top: 3px;">{s4_t}</div>
                      <div style="font-size: 11px; color: {text_sec_css}; margin-top: 2px;">{s4_d}</div>
                    </div>
                  </div>
                </div>
                """

                photo_block = f"""
                <div style="width:100%; height:280px; border-radius:18px; overflow:hidden; border:2px solid {accent_hex}; box-shadow:0 10px 28px rgba(0,0,0,0.6); position:relative; margin-bottom:12px;">
                  <img src="data:image/jpeg;base64,{bg_b64}" style="width:100%; height:100%; object-fit:cover; display:block;" />
                  <div style="position:absolute; bottom:0; left:0; right:0; background:linear-gradient(180deg, transparent 0%, rgba(0,0,0,0.85) 100%); padding:8px 18px; display:flex; justify-content:space-between;">
                    <div style="font-size:16px; font-weight:bold; color:{accent_hex};">🔬 {tech_tag}</div>
                  </div>
                </div>
                """ if bg_b64 else ""

                content_html = f"""
                <div style="flex:1; display:flex; flex-direction:column; justify-content:space-evenly; margin:8px 0;">
                  <div>
                    <div style="color:{accent_hex}; font-size:22px; font-weight:bold; margin-bottom:6px;">{step_str}</div>
                    <div style="font-size:44px; font-weight:bold; line-height:1.24; color:#ffffff;">{headline}</div>
                  </div>
                  {photo_block}
                  {diagram_html}
                  <div style="background:{card_bg_css}; border:1.5px solid {border_css}; border-radius:18px; padding:20px 26px;">
                    <div style="font-size:25px; line-height:1.55; color:{text_sec_css};">{body}</div>
                  </div>
                </div>
                """
            elif slide_num == 2 and not is_tech_topic:
                # Slide 2 for CULTURE / NEWS / HEALTH: 380px CLEAR PHOTO SHOWCASE
                step_str = f"⚡ STEP 01" if slot == "afternoon" else ("⚡ ধাপ ০১" if language == "Bengali" else "⚡ चरण ०१")
                loc_badge = "📍 পশ্চিমবঙ্গ • সমসাময়িক দৃশ্য" if language == "Bengali" else ("📍 भारत • प्रत्यक्ष दृश्य" if language == "Hindi" else "🌐 GROUND PERSPECTIVE")
                ver_badge = "📸 আসল দৃশ্য" if language == "Bengali" else ("📸 वास्तविक दृश्य" if language == "Hindi" else "📸 VERIFIED")
                photo_card_html = f"""
                <div style="width:100%; height:380px; border-radius:20px; overflow:hidden; border:2px solid {accent_hex}; box-shadow:0 12px 30px rgba(0,0,0,0.6); position:relative; margin-bottom:14px;">
                  <img src="data:image/jpeg;base64,{bg_b64}" style="width:100%; height:100%; object-fit:cover; display:block;" />
                  <div style="position:absolute; bottom:0; left:0; right:0; background:linear-gradient(180deg, transparent 0%, rgba(0,0,0,0.85) 100%); padding:10px 18px; display:flex; justify-content:space-between; align-items:flex-end;">
                    <div style="font-size:19px; font-weight:bold; color:{accent_hex};">{loc_badge}</div>
                    <div style="font-size:14px; color:#ffffff; background:rgba(255,255,255,0.2); border:1px solid {accent_hex}; border-radius:8px; padding:4px 12px;">{ver_badge}</div>
                  </div>
                </div>
                """ if bg_b64 else ""

                content_html = f"""
                <div style="flex:1; display:flex; flex-direction:column; justify-content:space-evenly; margin:8px 0;">
                  <div>
                    <div style="color:{accent_hex}; font-size:22px; font-weight:bold; margin-bottom:6px;">{step_str}</div>
                    <div style="font-size:44px; font-weight:bold; line-height:1.24; color:#ffffff;">{headline}</div>
                  </div>
                  {photo_card_html}
                  <div style="background:{card_bg_css}; border:1.5px solid {border_css}; border-radius:18px; padding:22px 28px;">
                    <div style="font-size:26px; line-height:1.55; color:{text_sec_css};">{body}</div>
                  </div>
                </div>
                """
            else:
                # Slides 3, 4, 5: 320px PHOTO CARD + METRIC BADGE + TEXT CARD
                if slot == "afternoon":
                    step_str = f"⚡ STEP {slide_num - 1:02d}" if slide_num < total_slides - 1 else "✨ SUMMARY"
                elif language == "Bengali":
                    step_str = f"⚡ ধাপ {to_indic_digits(f'{slide_num - 1:02d}', language)}" if slide_num < total_slides - 1 else "✨ সারসংক্ষেপ"
                else:
                    step_str = f"⚡ चरण {to_indic_digits(f'{slide_num - 1:02d}', language)}" if slide_num < total_slides - 1 else "✨ निष्कर्ष"

                photo_card_html = f"""
                <div style="width:100%; height:320px; border-radius:18px; overflow:hidden; border:2px solid {accent_hex}; box-shadow:0 12px 30px rgba(0,0,0,0.6); position:relative; margin-bottom:12px;">
                  <img src="data:image/jpeg;base64,{bg_b64}" style="width:100%; height:100%; object-fit:cover; display:block;" />
                  <div style="position:absolute; bottom:0; left:0; right:0; background:linear-gradient(180deg, transparent 0%, rgba(0,0,0,0.85) 100%); padding:8px 18px; font-size:16px; font-weight:bold; color:{accent_hex};">
                    🔍 {slide_img_q.upper()}
                  </div>
                </div>
                """ if bg_b64 else ""

                # Badge
                badge_html = ""
                custom_badge = slide.get("visual_badge")
                if custom_badge and isinstance(custom_badge, dict):
                    b_lbl = custom_badge.get("label", "KEY METRIC")
                    b_val = custom_badge.get("value", "")
                    b_type = custom_badge.get("type", "chart" if slot == "afternoon" else "badge")
                else:
                    if slot == "afternoon":
                        b_type = "chart"
                        defaults = [
                            ("📊 BENCHMARK EFFICIENCY", "+340% Performance Acceleration"),
                            ("⚡ SYSTEM LATENCY VECTOR", "< 12ms Real-Time Throughput"),
                            ("📈 SCALABILITY & ADOPTION", "84% Enterprise Integration"),
                            ("🎯 CORE FRONTIER MODEL", "Next-Gen System Deployed"),
                        ]
                        b_lbl, b_val = defaults[(slide_num - 2) % len(defaults)]
                    elif language == "Hindi":
                        b_type = "chart"
                        defaults = [
                            ("📊 भारतीय तकनीक आँकड़े", "+४৫০% नवाचार व विकास"),
                            ("🚀 डिजिटल भारत गति", "१०x तीव्र कार्यान्वयन"),
                            ("⚡ स्टार्टअप बाज़ार विस्तार", "₹১০০ বিলিয়ন अवसर"),
                            ("✨ मुख्य आत्मनिर्भर उपलब्धि", "भारतीय तकनीक क्रांति"),
                        ]
                        b_lbl, b_val = defaults[(slide_num - 2) % len(defaults)]
                    else:
                        b_type = "badge"
                        defaults = [
                            ("📍 পশ্চিমবঙ্গ সমসাময়িক মডেল", "১ টাকার অভিনব সহায়তা"),
                            ("🎭 শিল্পচর্চা ও আত্মবিশ্বাস", "১০x সামাজিক মুক্তি ও সাহস"),
                            ("🌱 মানবিক শিক্ষা বিপ্লব", "ভবিষ্যৎ প্রজন্মের স্বনির্ভরতা"),
                            ("✨ অনন্য সামাজিক দিশা", "বাংলার গর্ব ও অগ্রগতি"),
                        ]
                        b_lbl, b_val = defaults[(slide_num - 2) % len(defaults)]

                if b_type == "chart" or slot == "afternoon":
                    badge_html = f"""
                    <div style="margin-bottom: 12px; background: rgba({theme['accent'][0]}, {theme['accent'][1]}, {theme['accent'][2]}, 0.09); border: 1.5px solid {accent_hex}; border-radius: 16px; padding: 14px 22px; display: flex; align-items: center; justify-content: space-between;">
                      <div>
                        <div style="font-size: 17px; font-weight: bold; color: {accent_hex}; letter-spacing: 0.8px;">{b_lbl}</div>
                        <div style="font-size: 28px; font-weight: bold; color: #ffffff; margin-top: 2px;">{b_val}</div>
                      </div>
                      <svg width="130" height="46" viewBox="0 0 130 46">
                        <defs>
                          <linearGradient id="grad{slide_num}" x1="0%" y1="0%" x2="0%" y2="100%">
                            <stop offset="0%" stop-color="{accent_hex}" stop-opacity="0.45"/>
                            <stop offset="100%" stop-color="{accent_hex}" stop-opacity="0.0"/>
                          </linearGradient>
                        </defs>
                        <path d="M0 38 Q 32 32, 65 18 T 130 6 L 130 46 L 0 46 Z" fill="url(#grad{slide_num})" />
                        <path d="M0 38 Q 32 32, 65 18 T 130 6" fill="none" stroke="{accent_hex}" stroke-width="2.8" />
                        <circle cx="130" cy="6" r="4.5" fill="{accent_sec_hex}" />
                      </svg>
                    </div>
                    """
                else:
                    icon_e = "🎭" if "শিল্প" in b_lbl or "নাট্য" in b_lbl else ("📍" if "পশ্চিমবঙ্গ" in b_lbl else "💡")
                    badge_html = f"""
                    <div style="margin-bottom: 12px; background: rgba({theme['accent'][0]}, {theme['accent'][1]}, {theme['accent'][2]}, 0.09); border: 1.5px solid {accent_hex}; border-radius: 18px; padding: 14px 22px; display: flex; align-items: center; justify-content: space-between;">
                      <div>
                        <div style="font-size: 18px; font-weight: bold; color: {accent_hex};">{b_lbl}</div>
                        <div style="font-size: 28px; font-weight: bold; color: #ffffff; margin-top: 2px;">{b_val}</div>
                      </div>
                      <div style="font-size: 34px;">{icon_e}</div>
                    </div>
                    """

                content_html = f"""
                <div style="flex:1; display:flex; flex-direction:column; justify-content:space-evenly; margin:8px 0;">
                  <div>
                    <div style="color:{accent_hex}; font-size:22px; font-weight:bold; margin-bottom:6px;">{step_str}</div>
                    <div style="font-size:44px; font-weight:bold; line-height:1.24; color:#ffffff;">{headline}</div>
                  </div>
                  {photo_card_html}
                  {badge_html}
                  <div style="background:{card_bg_css}; border:1.5px solid {border_css}; border-radius:18px; padding:20px 26px;">
                    <div style="font-size:26px; line-height:1.55; color:{text_sec_css};">{body}</div>
                  </div>
                </div>
                """

            footer_tag = "GLOBAL TECH & SYSTEMS" if slot == "afternoon" else ("পশ্চিমবঙ্গ ইনোভেশন ও টেক ট্রেন্ড" if language == "Bengali" else "डिजिटल भारत व टेक ट्रेंड्स")

            bg_css = f"linear-gradient(180deg, rgba({theme['bg_top'][0]},{theme['bg_top'][1]},{theme['bg_top'][2]}, 0.88) 0%, rgba({theme['bg_bot'][0]},{theme['bg_bot'][1]},{theme['bg_bot'][2]}, 0.94) 100%), url('data:image/jpeg;base64,{bg_b64}') center/cover no-repeat" if bg_b64 else f"linear-gradient(180deg, rgb({theme['bg_top'][0]},{theme['bg_top'][1]},{theme['bg_top'][2]}) 0%, rgb({theme['bg_bot'][0]},{theme['bg_bot'][1]},{theme['bg_bot'][2]}) 100%)"

            slide_html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
  {font_face_css}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    width: 1080px; height: 1350px;
    background: {bg_css};
    font-family: {font_family_css};
    color: #ffffff; padding: 34px;
    display: flex; flex-direction: column; justify-content: center;
    overflow: hidden;
  }}
</style>
</head>
<body>
  {frame_html}
    <div style="display:flex; justify-content:space-between; align-items:center; border-bottom:2px solid {border_css}; padding-bottom:16px;">
      <div style="{pill_style}">{tag}</div>
      <div style="font-size:24px; font-weight:bold; color:{accent_sec_hex};">{counter_str}</div>
    </div>
    {content_html}
    <div style="display:flex; justify-content:space-between; align-items:center; border-top:2px solid {border_css}; padding-top:16px;">
      <div style="font-size:24px; font-weight:bold; color:{accent_hex};">@{brand}</div>
      <div style="font-size:21px; color:{accent_sec_hex};">{footer_tag}</div>
    </div>
  {frame_close}
</body>
</html>"""

            dest = IMAGE_DIR / f"{date_key}-v{revision}-slide{slide_num}.jpg"
            page.set_content(slide_html)
            page.screenshot(path=str(dest), type="jpeg", quality=92)
            rendered_paths.append(dest.relative_to(ROOT / "docs").as_posix())

        browser.close()

    return rendered_paths


def render_carousel_slides(date_key, revision, settings, item, slot="afternoon"):
    # Use Chromium layout engine for pixel-perfect typography, conjuncts & styling when available
    browser_exe = get_browser_executable()
    if browser_exe:
        try:
            res = render_carousel_slides_chrome(date_key, revision, settings, item, slot=slot, browser_exe=browser_exe)
            if res:
                return res
        except Exception as exc:
            print(f"Chromium render warning ({exc}), falling back to PIL: {exc}")

    """Render 5-6 high-res 1080x1350 carousel slides with dedicated frame & style per slot."""
    clean_date = date_key.split("_")[0]
    day = date.fromisoformat(clean_date)
    language = item.get("language", "English")
    slides_data = item.get("slides") or []
    if not slides_data:
        slides_data = [
            {"slide_number": 1, "tag": item.get("category", "IDEAS"), "headline": item["title"],
             "body": item["subtitle"], "swipe_prompt": "Swipe to explore ➡️"},
            {"slide_number": 2, "tag": "CONNECT", "headline": "FOUND THIS HELPFUL? 💡",
             "body": f"Follow @{settings['brand'].lower()} for more daily updates! 🚀🔥✨\n\n📌 Save this post\n↗️ Share with a friend"}
        ]
    total_slides = len(slides_data)
    rendered_paths = []
    brand = settings.get("brand", "Choicematrix")
    W, H = 1080, 1350
    IMAGE_DIR.mkdir(parents=True, exist_ok=True)

    theme_pool = THEMES_CYBER if slot == "afternoon" else THEMES_WARM

    for idx, slide in enumerate(slides_data):
        slide_num = slide.get("slide_number", idx + 1)
        if slide_num == total_slides:
            if slot == "afternoon":
                theme = {
                    "name": "Cyber Outro",
                    "bg_top": (10, 16, 32), "bg_bot": (18, 30, 58), "card_bg": (22, 36, 70),
                    "accent": (0, 242, 254), "accent_secondary": (255, 184, 0),
                    "text_primary": (255, 255, 255), "text_secondary": (225, 235, 255), "border": (45, 75, 120)
                }
            else:
                theme = {
                    "name": "Radiant Outro",
                    "bg_top": (28, 14, 32), "bg_bot": (52, 18, 55), "card_bg": (42, 16, 48),
                    "accent": (255, 184, 0), "accent_secondary": (244, 63, 94),
                    "text_primary": (255, 255, 255), "text_secondary": (245, 230, 250), "border": (95, 30, 95)
                }
        else:
            weekday = day.weekday()
            base_theme_idx = (weekday * 2 + (0 if slot == "afternoon" else 1) + (revision - 1)) % len(theme_pool)
            theme_idx = (base_theme_idx + (idx % 2)) % len(theme_pool)
            theme = theme_pool[theme_idx]

        slide_tag = slide.get("tag", "")
        photo_query = f"{item.get('unsplash_query', '')} {slide_tag}".strip()
        bg_photo = fetch_unsplash_image(photo_query or item.get("title", "technology"), W, H)

        if bg_photo:
            overlay = Image.new("RGBA", (W, H))
            draw_ol = ImageDraw.Draw(overlay)
            for y in range(H):
                ratio = y / H
                alpha = int(185 + 50 * ratio)
                r = int(theme["bg_top"][0]*(1-ratio) + theme["bg_bot"][0]*ratio)
                g = int(theme["bg_top"][1]*(1-ratio) + theme["bg_bot"][1]*ratio)
                b = int(theme["bg_top"][2]*(1-ratio) + theme["bg_bot"][2]*ratio)
                draw_ol.line((0, y, W, y), fill=(r, g, b, alpha))
            image = Image.alpha_composite(bg_photo.convert("RGBA"), overlay).convert("RGB")
            draw = ImageDraw.Draw(image)
        else:
            image = Image.new("RGB", (W, H))
            draw = ImageDraw.Draw(image)
            for y in range(H):
                ratio = y / H
                c = tuple(int(theme["bg_top"][i]*(1-ratio) + theme["bg_bot"][i]*ratio) for i in range(3))
                draw.line((0, y, W, y), fill=c)

        # Decorative Background Accents
        rnd = random.Random(int(hashlib.sha256(f"{clean_date}-{slot}-{revision}-{slide_num}-{theme['name']}".encode()).hexdigest()[:12], 16))
        for _ in range(6):
            rx, ry = rnd.randrange(0, W), rnd.randrange(0, H)
            rad = rnd.randrange(40, 160)
            alpha_color = tuple(min(255, int(v * 0.4)) for v in theme["border"])
            draw.ellipse((rx-rad, ry-rad, rx+rad, ry+rad), outline=alpha_color, width=2)

        # FRAME STYLES: Cyber Minimalist (Afternoon) vs Warm Editorial (Evening)
        if slot == "afternoon":
            # Sharp cyber double border with corner L-brackets
            draw.rectangle((36, 36, W - 36, H - 36), outline=theme["border"], width=2)
            # L-brackets at 4 corners (length 32px)
            brk = 32
            draw.line([(36, 36 + brk), (36, 36), (36 + brk, 36)], fill=theme["accent"], width=4)
            draw.line([(W - 36 - brk, 36), (W - 36, 36), (W - 36, 36 + brk)], fill=theme["accent"], width=4)
            draw.line([(36, H - 36 - brk), (36, H - 36), (36 + brk, H - 36)], fill=theme["accent"], width=4)
            draw.line([(W - 36 - brk, H - 36), (W - 36, H - 36), (W - 36 - brk, H - 36)], fill=theme["accent"], width=4)
        else:
            # Warm ornamental rounded double border with gold trim & corner diamonds
            draw.rounded_rectangle((36, 36, W - 36, H - 36), radius=32, outline=theme["border"], width=3)
            draw.rounded_rectangle((48, 48, W - 48, H - 48), radius=22, outline=theme["accent"], width=1)
            for (cx, cy) in [(62, 62), (W - 62, 62), (62, H - 62), (W - 62, H - 62)]:
                draw.polygon([(cx, cy - 6), (cx + 6, cy), (cx, cy + 6), (cx - 6, cy)], fill=theme["accent"])

        tag = slide.get("tag", "HOT TOPIC").upper()
        headline = slide.get("headline", "")
        body = slide.get("body", "")
        audio = item.get("recommended_audio") or {}

        # Top Header Bar
        pill_w = 260
        pill_radius = 8 if slot == "afternoon" else 20
        draw.rounded_rectangle((70, 75, 70 + pill_w, 135), radius=pill_radius, fill=theme["accent"])
        draw.text((90, 92), tag[:22], font=font(22, bold=True, language=language), fill=theme["bg_top"])

        if slot == "afternoon":
            counter_str = f"{slide_num:02d} / {total_slides:02d}"
        else:
            s_indic = to_indic_digits(f"{slide_num:02d}", language)
            t_indic = to_indic_digits(f"{total_slides:02d}", language)
            counter_str = f"{s_indic} / {t_indic}"
        draw.text((W - 190, 92), counter_str, font=font(24, bold=True, language=language), fill=theme["accent_secondary"])
        draw.line((70, 165, W - 70, 165), fill=theme["border"], width=2)

        if slide_num == 1:
            header_label = "TODAY'S HOT BREAKDOWN" if slot == "afternoon" else ("আজকের বিশেষ বিশ্লেষণ" if language == "Bengali" else "आज का विशेष विश्लेषण")
            draw.text((70, 220), header_label, font=font(22, bold=True, language=language), fill=theme["accent"])
            f_title = font(52, bold=True, language=language)
            title_lines = wrap_by_pixels(draw, headline, f_title, 940)
            y = 265
            for line in title_lines[:4]:
                draw.text((70, y), line, font=f_title, fill=theme["text_primary"])
                y += 66
            y += 15
            draw.rounded_rectangle((70, y, 70 + 200, y + 8), radius=4, fill=theme["accent"])
            y += 35
            card_h = 390
            card_r = 16 if slot == "afternoon" else 28
            draw.rounded_rectangle((70, y, W - 70, y + card_h), radius=card_r, fill=theme["card_bg"], outline=theme["border"], width=3)
            f_body = font(32, bold=False, language=language)
            body_lines = wrap_by_pixels(draw, body, f_body, 860)
            by = y + 36
            for line in body_lines[:7]:
                draw.text((110, by), line, font=f_body, fill=theme["text_secondary"])
                by += 46



            swipe_y = H - 235
            draw.rounded_rectangle((70, swipe_y, W - 70, swipe_y + 85), radius=22, fill=theme["accent"])
            swipe_text = slide.get("swipe_prompt")
            if not swipe_text:
                if language == "Bengali":
                    swipe_text = "বিস্তারিত দেখতে সোয়াইপ করুন ➡️"
                elif language == "Hindi":
                    swipe_text = "आगे जानने के लिए स्वाइप करें ➡️"
                else:
                    swipe_text = "Swipe to explore ➡️"
            draw.text((110, swipe_y + 26), swipe_text.upper(), font=font(28, bold=True, language=language), fill=theme["bg_top"])

        elif slide_num == total_slides:
            header_label = "KEY TAKEAWAY COMPLETED" if slot == "afternoon" else ("গুরুত্বপূর্ণ তথ্যাবলী সমাপ্ত" if language == "Bengali" else "मुख्य बिंदु संपन्न")
            draw.text((70, 220), header_label, font=font(22, bold=True, language=language), fill=theme["accent"])
            f_title = font(50, bold=True, language=language)
            draw.text((70, 270), headline, font=f_title, fill=theme["text_primary"])
            box_y = 360
            box_h = 510
            card_r = 16 if slot == "afternoon" else 28
            draw.rounded_rectangle((70, box_y, W - 70, box_y + box_h), radius=card_r, fill=theme["card_bg"], outline=theme["accent"], width=4)
            draw.text((110, box_y + 40), f"👉 FOLLOW @{brand.upper()}", font=font(38, bold=True, language=language), fill=theme["accent"])

            if language == "Bengali":
                sub_cta = "প্রতিদিনের সেরা টেক ও জ্ঞানমূলক আপডেটের জন্য! 🚀🔥✨"
                bullet1 = "📌  ভবিষ্যতের জন্য পোস্টটি সেভ করে রাখুন"
                bullet2 = "💬  আপনার মতামত কমেন্টে জানান"
                bullet3 = "↗️  বন্ধুদের সাথে পোস্টটি শেয়ার করুন!"
            elif language == "Hindi":
                sub_cta = "रोज़ाना बेहतरीन टेक इनसाइट्स और स्मार्ट अपडेट्स के लिए! 🚀🔥✨"
                bullet1 = "📌  इस पोस्ट को बाद के लिए सेव करें"
                bullet2 = "💬  कमेंट्स में अपनी राय साझा करें"
                bullet3 = "↗️  अपने दोस्तों के साथ ज़रूर शेयर करें!"
            else:
                sub_cta = "For daily hot trends, tech breakthroughs & smart tips! 🚀🔥✨"
                bullet1 = "📌  Save this post to revisit anytime"
                bullet2 = "💬  Share your thoughts in the comments"
                bullet3 = "↗️  Send to someone who needs this!"

            draw.text((110, box_y + 98), sub_cta, font=font(24, bold=False, language=language), fill=theme["text_secondary"])

            if audio.get("title"):
                draw.text((110, box_y + 148), f"🎵 Recommended Audio: {audio.get('title')} — {audio.get('artist')}", font=font(22, bold=True, language=language), fill=theme["accent_secondary"])
                draw.line((110, box_y + 195, W - 110, box_y + 195), fill=theme["border"], width=2)
                line_offset = 230
            else:
                draw.line((110, box_y + 165, W - 110, box_y + 165), fill=theme["border"], width=2)
                line_offset = 200

            draw.text((110, box_y + line_offset), bullet1, font=font(27, bold=False, language=language), fill=theme["text_primary"])
            draw.text((110, box_y + line_offset + 60), bullet2, font=font(27, bold=False, language=language), fill=theme["text_primary"])
            draw.text((110, box_y + line_offset + 120), bullet3, font=font(27, bold=False, language=language), fill=theme["text_primary"])

        else:
            if slot == "afternoon":
                step_str = f"STEP {slide_num - 1:02d}" if slide_num < total_slides - 1 else "SUMMARY"
            else:
                if language == "Bengali":
                    step_str = f"ধাপ {to_indic_digits(f'{slide_num - 1:02d}', language)}" if slide_num < total_slides - 1 else "সারসংক্ষেপ"
                else:
                    step_str = f"चरण {to_indic_digits(f'{slide_num - 1:02d}', language)}" if slide_num < total_slides - 1 else "निष्कर्ष"
            draw.text((70, 210), step_str, font=font(26, bold=True, language=language), fill=theme["accent"])
            f_title = font(46, bold=True, language=language)
            title_lines = wrap_by_pixels(draw, headline, f_title, 940)
            y = 260
            for line in title_lines[:3]:
                draw.text((70, y), line, font=f_title, fill=theme["text_primary"])
                y += 62
            y += 25
            card_h = 670
            card_r = 16 if slot == "afternoon" else 28
            draw.rounded_rectangle((70, y, W - 70, y + card_h), radius=card_r, fill=theme["card_bg"], outline=theme["border"], width=3)
            f_body = font(29, bold=False, language=language)
            body_lines = wrap_by_pixels(draw, body, f_body, 860)
            by = y + 38
            for line in body_lines[:14]:
                draw.text((110, by), line, font=f_body, fill=theme["text_secondary"])
                by += 44

        # Footer Brand Bar
        draw.line((70, H - 120, W - 70, H - 120), fill=theme["border"], width=2)
        draw.text((70, H - 90), f"@{brand.upper()}", font=font(24, bold=True, language=language), fill=theme["accent"])
        if slot == "afternoon":
            footer_tag = "GLOBAL TECH & SYSTEMS"
        elif language == "Bengali":
            footer_tag = "পশ্চিমবঙ্গ ইনোভেশন ও ট্রেন্ড"
        else:
            footer_tag = "डिजिटल भारत व टेक ट्रेंड्स"
        draw.text((W - 350, H - 90), footer_tag, font=font(21, bold=False, language=language), fill=theme["text_secondary"])

        dest = IMAGE_DIR / f"{date_key}-v{revision}-slide{slide_num}.jpg"
        image.save(dest, "JPEG", quality=90, optimize=True)
        rendered_paths.append(dest.relative_to(ROOT / "docs").as_posix())

    return rendered_paths


def image_art(date_key, revision, settings, item, slot="afternoon"):
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
        full = getenv("GITHUB_REPOSITORY") or "Subha760/choicematrix-autopilot"
        owner, repo = full.split("/", 1)
        base = f"https://raw.githubusercontent.com/{owner}/{repo}/main/docs"
    clean_path = str(relative_image_path).lstrip("/")
    return base.rstrip("/") + "/" + clean_path


def create_post(date_key, settings, posts, revision=1, slot=None):
    if slot is None:
        if "_evening" in date_key:
            slot = "evening"
        elif "_afternoon" in date_key:
            slot = "afternoon"
        else:
            slot = "afternoon"

    clean_date = date_key.split("_")[0]
    post_id = f"{clean_date}_{slot}" if ("_afternoon" in date_key or "_evening" in date_key or slot != "afternoon") else date_key
    publish_time = settings.get("slots", {}).get(slot, {}).get("publish_time_ist") or (
        "14:00" if slot == "afternoon" else "20:00"
    )

    content = generated_content(settings, posts, date_key, revision=revision, slot=slot)
    language = content.get("language", "English")
    slides = content.get("slides")
    if slides and isinstance(slides, list) and len(slides) >= 2:
        images = render_carousel_slides(date_key, revision, settings, content, slot=slot)
        primary_image = images[0]
    else:
        primary_image = image_art(date_key, revision, settings, content, slot=slot)
        images = [primary_image]

    entry = {
        "date": date_key,
        "clean_date": clean_date,
        "slot": slot,
        "publish_time_ist": publish_time,
        "language": language,
        "revision": revision,
        "title": content["title"],
        "subtitle": content["subtitle"],
        "caption": content["caption"],
        "category": content["category"],
        "content_type": content["content_type"],
        "source": content["source"],
        "slides": content.get("slides"),
        "unsplash_query": content.get("unsplash_query"),
        "image": primary_image,
        "images": images,
        "recommended_audio": content.get("recommended_audio"),
        "image_sha256": hashlib.sha256((ROOT / "docs" / primary_image).read_bytes()).hexdigest(),
        "status": "pending",
        "approved_at": None,
        "approval_hash": None,
        "fb": {"status": "not_started", "id": None},
        "ig": {"status": "not_started", "id": None, "container": None},
        "threads": {"status": "not_started", "id": None, "container": None},
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    posts[date_key] = entry
    try:
        mingit = r"C:\Users\bongo\.mingit\cmd\git.exe"
        git_bin = mingit if Path(mingit).exists() else "git"
        subprocess.run([git_bin, "-C", str(ROOT), "add", "docs/posts/", STATE.relative_to(ROOT).as_posix()], timeout=20, capture_output=True)
        subprocess.run([git_bin, "-C", str(ROOT), "-c", "user.name=social-autopilot[bot]",
                        "-c", "user.email=41898282+github-actions[bot]@users.noreply.github.com",
                        "commit", "-m", f"chore: persist slides for {date_key} [skip ci]"], timeout=20, capture_output=True)
        branch = os.getenv("GITHUB_REF_NAME") or "main"
        subprocess.run([git_bin, "-C", str(ROOT), "push", "origin", f"HEAD:{branch}"], timeout=35, capture_output=True)
    except Exception as exc:
        print(f"Git push slides warning: {exc}")
    try:
        send_preview(entry, settings)
    except Exception:
        posts.pop(date_key, None)
        raise
    return entry


def send_preview(post, settings):
    post_id = post["date"]
    clean_date = post.get("clean_date") or post_id.split("_")[0]
    slot = post.get("slot", "afternoon")
    revision = post["revision"]
    chat = getenv("TG_APPROVER_ID", True)
    images = post.get("images") or [post["image"]]
    slot_label = "🌞 AFTERNOON POST (2:00 PM IST)" if slot == "afternoon" else "🌙 EVENING POST (8:00 PM IST)"
    lang_label = post.get("language", "English")

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
            telegram("sendPhoto", {"chat_id": chat, "caption": f"DAILY DRAFT • {post_id}"}, {"photo": image_file})

    audio_info = ""

    publish_time = post.get("publish_time_ist") or settings.get("publish_time_ist", "14:00")
    full_text = (
        f"{slot_label} • {len(images)}-SLIDE CAROUSEL DRAFT\n"
        f"📅 Date: {clean_date} | 🌐 Language: {lang_label}\n\n"
        f"TITLE: {post['title']}\n\n"
        f"{audio_info}"
        f"{post['caption']}\n\n"
        f"⏰ Scheduled for {publish_time} IST.\n\n"
        "👉 To Approve: Reply 'approve afternoon' or 'approve evening' (or 'approve') 🚀\n"
        "👉 To Reject: Reply 'reject afternoon' or 'reject evening' (or 'reject') ❌\n"
        "👉 To Recreate: Reply 'recreate afternoon' or 'recreate evening' (or 'recreate') 🔄\n"
        "👉 To Check Status: Reply 'status' 📊"
    )
    main_keyboard = {
        "keyboard": [
            [{"text": "✅ Approve Afternoon"}, {"text": "✅ Approve Evening"}],
            [{"text": "🔄 Recreate Afternoon"}, {"text": "🔄 Recreate Evening"}],
            [{"text": "📊 Check Status"}, {"text": "❌ Reject All"}]
        ],
        "resize_keyboard": True,
        "is_persistent": True
    }
    telegram("sendMessage", {"chat_id": chat, "text": full_text[:4000], "reply_markup": json.dumps(main_keyboard)})


def take_approvals(posts, settings, now=None):
    if now is None:
        now = datetime.now(IST)
    tg = read_json(TG_STATE, {"offset": 0})
    try:
        updates = telegram("getUpdates", {"offset": tg["offset"], "timeout": 0,
                                           "allowed_updates": json.dumps(["callback_query", "message"])})
    except Exception as exc:
        if "409" in str(exc) or "Conflict" in str(exc):
            print("Telegram polling handled by active daemon (409 Conflict). Skipping duplicate poll.")
            return
        raise
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
            slot_name = entry.get("slot", "post").capitalize()
            pub_time = entry.get("publish_time_ist", "14:00")
            if action == "approve":
                if post_is_stale(entry, settings, now):
                    entry["status"] = "expired"
                else:
                    entry["status"] = "approved"
                    entry["approved_at"] = datetime.now(timezone.utc).isoformat()
                    entry["approval_hash"] = approval_digest(entry)
                    safe_notify(f"✅ APPROVED! {slot_name} carousel is scheduled to publish across Facebook, Instagram, and Threads at {pub_time} IST. 🚀")
            elif action == "reject":
                entry["status"] = "rejected"
                entry["approved_at"] = None
                entry["approval_hash"] = None
                safe_notify(f"❌ REJECTED {slot_name} post. This post will not be published.")
            elif action == "regen":
                try:
                    create_post(date_key, settings, posts, int(revision) + 1, slot=entry.get("slot"))
                    safe_notify(f"🔄 New {slot_name} carousel generated and sent for approval!")
                except Exception as exc:
                    safe_notify(f"Regeneration failed: {type(exc).__name__}")

        # Support direct chat text messages (approve, yes, ok, regen, reject)
        msg = update.get("message")
        if msg:
            user_id = str(msg.get("from", {}).get("id", ""))
            text = msg.get("text", "").strip().lower()
            if user_id == getenv("TG_APPROVER_ID", True) and text:
                target_slot = None
                if any(w in text for w in ("evening", "evenig", "eve", "8pm", "evng", "night")):
                    target_slot = "evening"
                elif any(w in text for w in ("afternoon", "after", "2pm", "day", "aft")):
                    target_slot = "afternoon"

                pending_keys = []
                for k in sorted(posts.keys(), reverse=True):
                    if any(w in text for w in ("reject", "no", "cancel")):
                        status_ok = posts[k].get("status") in ("pending", "approved", "needs_attention", "expired")
                    else:
                        status_ok = posts[k].get("status") in ("pending", "needs_attention", "expired")
                    if status_ok:
                        if target_slot is None or posts[k].get("slot") == target_slot:
                            pending_keys.append(k)

                if pending_keys:
                    if any(w in text for w in ("approve", "yes", "ok", "publish")):
                        for pk in pending_keys:
                            entry = posts[pk]
                            entry["status"] = "approved"
                            entry["approved_at"] = datetime.now(timezone.utc).isoformat()
                            entry["approval_hash"] = approval_digest(entry)
                            safe_notify(f"✅ APPROVED {entry.get('slot', 'post').capitalize()} post! Scheduled for {entry.get('publish_time_ist', '14:00')} IST. 🚀")
                    elif any(w in text for w in ("regen", "recreate", "new", "retry")):
                        for pk in pending_keys[:1]:
                            entry = posts[pk]
                            try:
                                create_post(pk, settings, posts, entry["revision"] + 1, slot=entry.get("slot"))
                                safe_notify(f"🔄 Generating new {entry.get('slot', 'post').capitalize()} carousel for you...")
                            except Exception as exc:
                                safe_notify(f"Regeneration failed: {type(exc).__name__}")
                    elif any(w in text for w in ("reject", "no", "cancel")):
                        for pk in pending_keys:
                            entry = posts[pk]
                            entry["status"] = "rejected"
                            safe_notify(f"❌ {entry.get('slot', 'post').capitalize()} post rejected. It will not be published.")

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
        err = payload.get("error", {})
        message = err.get("message", f"HTTP {response.status_code}")
        if err.get("code") == 190 or "access token" in message.lower() or "session has expired" in message.lower():
            alert_msg = (
                "🚨 ACTION REQUIRED: Meta Access Token Expired!\n\n"
                "Facebook and Instagram publishing stopped because your Meta token expired.\n"
                "👉 Refresh at: https://developers.facebook.com/tools/explorer/\n"
                "👉 Update META_PAGE_TOKEN in .env and restart daemon."
            )
            safe_notify(alert_msg)
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
                res_data = r.json()
                if not res_data.get("id"):
                    raise ValueError(f"Threads child upload error: {res_data}")
                child_ids.append(res_data["id"])

            # Wait for all child item containers to be FINISHED
            for cid in child_ids:
                for _ in range(12):
                    st = requests.get(f"https://graph.threads.net/v1.0/{cid}",
                                      headers={"Authorization": f"Bearer {token}"},
                                      params={"fields": "status"}).json().get("status")
                    if st == "FINISHED":
                        break
                    time.sleep(2)

            parent_url = f"https://graph.threads.net/v1.0/{threads_user}/threads"
            r = requests.post(parent_url, headers={"Authorization": f"Bearer {token}"},
                              data={"media_type": "CAROUSEL", "children": ",".join(child_ids), "text": caption}, timeout=45)
            parent_data = r.json()
            if not parent_data.get("id"):
                raise ValueError(f"Threads parent carousel error: {parent_data}")
            state["container"] = parent_data["id"]
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
    clean_date = entry.get("clean_date") or date_key.split("_")[0]
    publish_time = entry.get("publish_time_ist")
    if not publish_time:
        slot = entry.get("slot", "afternoon")
        publish_time = settings.get(f"publish_time_{slot}_ist") or settings.get("publish_time_ist", "14:00")
    scheduled = datetime.fromisoformat(f"{clean_date}T{publish_time}:00").replace(tzinfo=IST)
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
    allow_local = os.getenv("ALLOW_LOCAL_PUBLISH", "false").lower() == "true"
    if os.getenv("GITHUB_ACTIONS", "").lower() != "true" and not allow_local:
        raise RuntimeError("Publishing blocked outside GitHub Actions durable commit")
    mingit = r"C:\Users\bongo\.mingit\cmd\git.exe"
    git_bin = mingit if Path(mingit).exists() else "git"
    relative_state = STATE.relative_to(ROOT).as_posix()
    subprocess.run([git_bin, "-C", str(ROOT), "add", relative_state, "docs/posts/"], check=True, timeout=20)
    subprocess.run([git_bin, "-C", str(ROOT), "-c", "user.name=social-autopilot[bot]",
                    "-c", "user.email=41898282+github-actions[bot]@users.noreply.github.com",
                    "commit", "-m", f"chore: reserve {platform} {entry['date']} [skip ci]"],
                   check=True, timeout=20, capture_output=True)
    branch = getenv("GITHUB_REF_NAME") or "main"
    subprocess.run([git_bin, "-C", str(ROOT), "push", "origin", f"HEAD:{branch}"],
                   check=True, timeout=40, capture_output=True)


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
        if "409" in str(exc) or "Conflict" in str(exc):
            print("Telegram polling handled by active daemon (409 Conflict). Continuing...")
        else:
            print(f"Approval read warning: {type(exc).__name__}: {exc}")
    generation_at = datetime.fromisoformat(f"{today}T{settings['generation_time_ist']}:00").replace(tzinfo=IST)
    attempt_log = read_json(GEN_ERRORS, {})

    # Generate daily dual posts (afternoon: English Global @ 2pm, evening: Hindi/Bengali Regional @ 8pm)
    slots = ["afternoon", "evening"]
    if now >= generation_at and not settings.get("manual_publish_only"):
        for slot in slots:
            post_id = f"{today}_{slot}"
            if post_id in posts:
                continue
            if today in posts and posts[today].get("slot") == slot:
                continue

            attempt = attempt_log.get(post_id, {"count": 0, "next_after": "1970-01-01T00:00:00+00:00"})
            allowed_time = datetime.fromisoformat(attempt["next_after"]).astimezone(IST)
            if now >= allowed_time and attempt["count"] < 6:
                try:
                    create_post(post_id, settings, posts, slot=slot)
                    attempt_log.pop(post_id, None)
                except Exception as exc:
                    count = attempt["count"] + 1
                    attempt_log[post_id] = {
                        "count": count,
                        "next_after": (now + timedelta(minutes=min(2 * (2**(count-1)), 15))).isoformat()
                    }
                    print(f"Daily generation failed for {post_id}: {type(exc).__name__}: {exc}")
                    if count == 1 or count == 6:
                        safe_notify(f"⚠️ AI draft for {post_id} ({slot.capitalize()}) failed (attempt {count}/6): {type(exc).__name__}")

    # Small bounded history prevents unneeded public repo growth.
    attempt_log = {k: v for k, v in attempt_log.items() if k.split("_")[0] >= (now.date() - timedelta(days=14)).isoformat()}
    write_json(GEN_ERRORS, attempt_log)
    process_publications(posts, settings, now)
    write_json(STATE, posts)
    print(f"Finished social-autopilot cycle at {now.isoformat()} / {len(posts)} posts")


if __name__ == "__main__":
    main()
