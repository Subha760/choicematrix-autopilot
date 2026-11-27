"""Post-specific track selection and owner-reviewed embedded video soundtracks."""
import copy
import hashlib
import json
import re
from urllib.parse import urlparse

PROFILES = {
    "culture": r"culture|festival|heritage|tradition|শিল্প|উৎসব|সংস্কৃতি|त्योहार|संस्कृति",
    "science": r"science|space|cosmos|astronom|nature|বিজ্ঞান|মহাকাশ|विज्ञान|अंतरिक्ष",
    "technology": r"\bai\b|tech|digital|software|cyber|innovation|প্রযুক্তি|तकनीक",
    "focus": r"productiv|habit|focus|career|organis|organiz|অভ্যাস|মনোযোগ|आदत|ध्यान",
    "calm": r"health|wellness|longevity|sustainab|environment|স্বাস্থ্য|পরিবেশ|स्वास्थ्य|पर्यावरण",
    "creative": r"creativ|\bdiy\b|craft|design|সৃজন|शिल्प|रचनात्मक",
    "uplifting": r"startup|business|entrepreneur|wealth|উদ্যোগ|व्यवसाय",
}
SENSITIVE = r"\b(death|died|funeral|memorial|tragedy|war|suicide|massacre|fatal|disaster)\b|মৃত্যু|শোক|निधन|शोक"
REGIONAL_TRACKS = {"dhaka": r"\bindia(?:n)?\b|bengal|kolkata|বাংলা|ভারত|भारत|बंगाल"}


def validate_config(settings):
    value = settings.get("music", {})
    if not isinstance(value, dict) or not isinstance(value.get("enabled", False), bool):
        raise ValueError("music.enabled must be a boolean")
    if value.get("mode", "suggestion") not in ("suggestion", "video"):
        raise ValueError("Music mode must be suggestion or video")
    seconds = value.get("slide_seconds", 7)
    if not isinstance(seconds, (int, float)) or isinstance(seconds, bool) or not 3 <= seconds <= 15:
        raise ValueError("Music video slide_seconds must be between 3 and 15")
    return value


def load_catalog(root):
    data = json.loads((root / "music_catalog.json").read_text(encoding="utf-8-sig"))
    tracks = data.get("tracks")
    if data.get("version") != 1 or not isinstance(tracks, list) or not tracks or len(tracks) > 100:
        raise ValueError("Invalid music catalog")
    seen = set()
    for track in tracks:
        if not isinstance(track, dict) or not re.fullmatch(r"[a-z0-9-]{1,40}", track.get("id", "")) or track["id"] in seen:
            raise ValueError("Music track IDs must be unique slugs")
        seen.add(track["id"])
        for field, limit in (("title", 80), ("artist", 80), ("mood", 100), ("license", 50),
                             ("source_url", 300), ("license_url", 200)):
            value = track.get(field)
            if not isinstance(value, str) or not value.strip() or len(value) > limit or any(ord(c) < 32 for c in value):
                raise ValueError(f"Invalid music {field}")
        for field in ("source_url", "license_url"):
            parsed = urlparse(track[field])
            if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
                raise ValueError("Music references must use public HTTPS URLs")
        from .video import allowed_audio_url
        if not allowed_audio_url(track.get("audio_url")):
            raise ValueError("Catalog audio must use the approved composer endpoint")
        if not isinstance(track.get("profiles"), list) or not track["profiles"] or any(p not in PROFILES and p != "reflective" for p in track["profiles"]):
            raise ValueError("Invalid music profile")
    return tracks


def no_music(reason="Owner chose no music"):
    return {"mode": "none", "reason": reason, "attachment_status": "not_attached"}


def select_music(root, entry, posts, settings, choice="auto", *, explicit=False):
    config = validate_config(settings)
    if choice == "none":
        return no_music()
    if not explicit and not config.get("enabled", False):
        return None
    text = " ".join(str(entry.get(k, "")) for k in ("category", "title", "subtitle", "caption"))
    if choice == "auto" and re.search(SENSITIVE, text, re.I):
        return no_music("Sensitive subject; no automatic music recommendation")
    tracks = load_catalog(root)
    if choice == "auto":
        profile = next((name for name, pattern in PROFILES.items() if re.search(pattern, str(entry.get("category", "")), re.I)), None)
        profile = profile or next((name for name, pattern in PROFILES.items() if re.search(pattern, text, re.I)), "reflective")
        candidates = [t for t in tracks if profile in t["profiles"]]
        regional = [t for t in candidates if t["id"] in REGIONAL_TRACKS and re.search(REGIONAL_TRACKS[t["id"]], text, re.I)]
        candidates = regional or [t for t in candidates if t["id"] not in REGIONAL_TRACKS]
        if not candidates:
            return no_music("No catalog track matches this post")
        recent = [(p.get("music") or {}).get("track", {}).get("id")
                  for key, p in sorted(posts.items())[-14:] if key != entry.get("date")]
        candidates = [t for t in candidates if t["id"] not in recent] or candidates
        seed = f"{entry.get('date')}:{entry.get('revision')}:{entry.get('title')}".encode()
        track = sorted(candidates, key=lambda t: t["id"])[int(hashlib.sha256(seed).hexdigest(), 16) % len(candidates)]
        reason = f"Matched the post's {profile} theme"
    else:
        track = next((t for t in tracks if t["id"] == choice), None)
        if not track:
            raise ValueError("Choose auto, none, or a track ID from the music catalog")
        reason = "Selected by the owner"
    video = config.get("mode") == "video"
    return {"mode": "video" if video else "suggestion", "track": copy.deepcopy(track), "reason": reason,
            "attachment_status": "pending_render" if video else "manual_required",
            "library_availability": "not_verified"}


def prepare_media(app, entry, posts, settings):
    config = validate_config(settings)
    if not config.get("enabled", False):
        entry.pop("video", None)
        if "media_format" in entry:
            entry["media_format"] = "carousel"
        if (entry.get("music") or {}).get("track"):
            entry["music"].update(mode="suggestion", attachment_status="manual_required")
        return
    if "music" not in entry:
        entry["music"] = select_music(app.ROOT, entry, posts, settings)
    if config.get("mode") == "video":
        from .video import render
        entry["media_format"] = "video"
        entry.pop("video", None)
        entry["video"] = render(app, entry, settings)
        if entry["music"].get("track"):
            entry["music"].update(mode="video", attachment_status="embedded")
    else:
        entry.pop("video", None)
        entry["media_format"] = "carousel"
        if entry["music"].get("track"):
            entry["music"].update(mode="suggestion", attachment_status="manual_required")


def preview_text(plan):
    if not plan:
        return ""
    if plan.get("mode") == "none":
        return "\n\nMusic: none. " + plan["reason"]
    track = plan["track"]
    delivery = ("The reviewed video includes this soundtrack and an attribution end card."
                if plan.get("attachment_status") == "embedded" else
                "Suggestion only: this photo carousel publishes WITHOUT audio. Add music manually in the app when available; library availability is not verified.")
    return (f"\n\nMusic: {track['title']} — {track['artist']}\n"
            f"Mood: {track['mood']}\n{plan['reason']}\nSource/listen: {track['source_url']}\n"
            f"Composer lists {track['license']}: {track['license_url']}\n"
            f"{delivery}\n"
            "Reply /music auto, /music none, or /music <track-id> to change the choice.")


def change_music(app, posts, key, choice, settings, now):
    from . import review
    entry = posts.get(key)
    if not entry or not review.can_replace(entry) or app.post_is_stale(entry, settings, now):
        return False, "Choose an unexpired draft without publication attempts."
    candidate = copy.deepcopy(entry)
    candidate["revision"] += 1
    try:
        candidate["music"] = select_music(app.ROOT, candidate, posts, settings, choice, explicit=True)
    except ValueError as exc:
        return False, str(exc)
    candidate.update(status="pending", approved_at=None, approval_hash=None, preview_hash=None,
                     preview_delivered=False, review_message_id=None, review_chat_id=None)
    entry.update(status="needs_review", approved_at=None, approval_hash=None, preview_delivered=False)
    app.write_json(app.STATE, posts)
    prepare_media(app, candidate, posts, settings)
    app.prepare_review(candidate, settings)
    posts[key] = candidate
    app.write_json(app.STATE, posts)
    app.state_store().event("music_changed", {"post": key, "revision": candidate["revision"], "choice": choice})
    try:
        app.persist_media(candidate)
        app.send_preview(candidate, settings)
    except Exception:
        candidate.update(status="preview_failed", preview_delivered=False, approval_hash=None, approved_at=None)
        app.write_json(app.STATE, posts)
        raise
    app.write_json(app.STATE, posts)
    return True, "Music choice updated. Review the complete new preview and approve again."
