"""Pure approval policy shared by every Telegram entry point."""
import hashlib
import json
import re
from pathlib import Path

PLATFORMS = ("fb", "ig", "threads")
MANIFEST_VERSION = 3


def authorized(sender, chat, owner):
    return (str(owner).isdigit() and str(sender.get("id", "")) == str(owner)
            and chat.get("type") == "private" and str(chat.get("id", "")) == str(owner))


def callback(action, entry):
    value = f"{action}:{entry['date']}:{entry['revision']}"
    if len(value.encode()) > 64:
        raise ValueError("Telegram callback too long")
    return value


def parse_callback(value):
    match = re.fullmatch(r"(approve|reject|recreate|regen|theme):([0-9]{4}-[0-9]{2}-[0-9]{2}(?:_(?:afternoon|evening))?):([1-9][0-9]*)", value)
    return (match[1], match[2], int(match[3])) if match else None


def local_manifest(root, entry):
    base = (Path(root) / "docs" / "posts").resolve(strict=True)
    images = entry.get("images") or [entry["image"]]
    if not isinstance(images, list) or not 1 <= len(images) <= 10 or len(set(images)) != len(images):
        raise ValueError("Invalid carousel image list")
    if entry["image"] != images[0]:
        raise ValueError("Primary image does not match carousel")
    result = []
    for relative in images:
        path = (Path(root) / "docs" / relative).resolve(strict=True)
        if not path.is_relative_to(base) or path.suffix.lower() not in (".jpg", ".jpeg"):
            raise ValueError("Image outside media directory")
        result.append({"path": relative, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    return result


def digest(entry):
    keys = ("date", "clean_date", "revision", "slot", "language", "title", "subtitle", "caption",
            "category", "content_type", "source", "slides", "images", "image", "image_sha256",
            "asset_manifest", "captions", "enabled_platforms", "publish_time_ist", "manifest_version",
            "design_mode", "theme_name", "photo_credits", "publish_targets")
    return hashlib.sha256(json.dumps({key: entry.get(key) for key in keys},
                         sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def prepare(root, entry, platforms, targets):
    entry["asset_manifest"] = local_manifest(root, entry)
    entry["image_sha256"] = entry["asset_manifest"][0]["sha256"]
    entry["images"] = [asset["path"] for asset in entry["asset_manifest"]]
    entry["manifest_version"] = MANIFEST_VERSION
    entry["enabled_platforms"] = list(platforms)
    caption = entry["caption"]
    # This exact platform text is displayed during review; publishers never silently truncate it.
    entry.setdefault("captions", {p: caption if p != "threads" or len(caption) <= 500
                                  else caption[:496].rstrip() + "..." for p in platforms})
    if set(entry["captions"]) != set(platforms):
        raise ValueError("Captions must match the enabled platforms")
    for platform, text in entry["captions"].items():
        if not isinstance(text, str) or not text.strip() or len(text) > (500 if platform == "threads" else 2200):
            raise ValueError("Invalid platform caption")
    entry["publish_targets"] = targets


def can_replace(entry):
    return not entry or all(entry.get(p, {}).get("status", "not_started") == "not_started" for p in PLATFORMS)


def approve(root, entry, now, stale):
    if entry.get("status") != "pending" or stale:
        raise ValueError("Only a current pending draft can be approved")
    if not entry.get("preview_delivered") or not entry.get("review_message_id"):
        raise ValueError("Deliver the complete preview first")
    if entry.get("manifest_version") != MANIFEST_VERSION or local_manifest(root, entry) != entry.get("asset_manifest"):
        raise ValueError("Artwork changed; send a new preview")
    if entry.get("preview_hash") != digest(entry):
        raise ValueError("Draft changed since its preview; send a new preview")
    if not can_replace(entry):
        raise ValueError("A publication attempt exists; reconcile it before any further action")
    entry.update(status="approved", approved_at=now.isoformat(), approval_hash=digest(entry))


def result_text(entry):
    lines = [f"{entry['date']}: {entry.get('status', 'unknown')}"]
    for name in entry.get("enabled_platforms", PLATFORMS):
        state = entry.get(name, {})
        line = f"{name.upper()}: {state.get('status', 'not_started')}"
        if state.get("status") == "published" and state.get("id"):
            line += f" (ID {state['id']})"
        lines.append(line)
    return "\n".join(lines)
