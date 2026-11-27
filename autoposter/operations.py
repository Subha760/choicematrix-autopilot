"""Conservative restoration and read-only verification of uncertain publishes."""
import json
from pathlib import Path
import sqlite3
from .review import PLATFORMS, can_replace


def restore(store, source):
    source = Path(source).resolve(strict=True)
    if source == store.path.resolve():
        raise ValueError("Choose a separate backup")
    from contextlib import closing
    with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as backup:
        if backup.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("Backup is corrupt")
        rows = backup.execute("SELECT name,payload FROM documents").fetchall()
        attempts = backup.execute("SELECT * FROM attempts").fetchall()
    # Preserve a verified snapshot of the database being replaced, including newer attempt evidence.
    import uuid
    store.backup(store.directory / ("before-restore-" + uuid.uuid4().hex + ".sqlite3"))
    current = store.read("posts", {})
    values = {name: json.loads(payload) for name, payload in rows}
    posts = values.get("posts", {})
    for key, entry in current.items():
        if not can_replace(entry):
            posts[key] = entry  # Never roll a known attempt backward.
    for entry in posts.values():
        if entry.get("status") != "published":
            entry.update(status="needs_attention" if not can_replace(entry) else "needs_review",
                         approved_at=None, approval_hash=None, preview_delivered=False)
    values["posts"] = posts
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        for name, value in values.items():
            # Offset must never move backward and replay previously handled commands.
            if name == "telegram":
                offset = db.execute("SELECT payload FROM documents WHERE name='telegram'").fetchone()
                value["offset"] = max(value.get("offset", 0), json.loads(offset[0]).get("offset", 0) if offset else 0)
            db.execute("""INSERT INTO documents VALUES (?,?,1)
                ON CONFLICT(name) DO UPDATE SET payload=excluded.payload,version=version+1""",
                (name, json.dumps(value, ensure_ascii=False)))
        for attempt in attempts:
            db.execute("INSERT OR IGNORE INTO attempts VALUES (?,?,?,?,?)", attempt)
        store._event(db, "restore", {"file": source.name})


def reconcile(app, key, platform, published_id):
    if platform not in PLATFORMS or not published_id.replace("_", "").isdigit():
        raise ValueError("Invalid platform or published ID")
    posts = app.read_json(app.STATE, {})
    entry = posts[key]
    state = entry[platform]
    if state.get("status") not in ("ambiguous", "attempting", "error"):
        raise ValueError("Only an unresolved attempt can be reconciled")
    target = app.publish_targets([platform])[platform]
    if target != (entry.get("publish_targets") or {}).get(platform):
        raise ValueError("Publishing target changed")
    if platform == "fb":
        caption_field = "description" if entry.get("media_format") == "video" else "message"
        data = app.meta_request("GET", published_id, app.getenv("META_PAGE_TOKEN", True),
                                {"fields": f"id,{caption_field},from,permalink_url"})
        if str(data.get("from", {}).get("id")) != target or data.get(caption_field) != entry["captions"]["fb"]:
            raise ValueError("Facebook owner or exact caption does not match")
    else:
        request = app.meta_request if platform == "ig" else app.threads_request
        token = app.getenv("META_PAGE_TOKEN" if platform == "ig" else "THREADS_ACCESS_TOKEN", True)
        edge = "media" if platform == "ig" else "threads"
        field = "caption" if platform == "ig" else "text"
        data = request("GET", f"{target}/{edge}", token, {"fields": f"id,{field}", "limit": 100})
        matches = [item for item in data.get("data", []) if str(item.get("id")) == published_id
                   and item.get(field) == entry["captions"][platform]]
        if len(matches) != 1:
            raise ValueError("Published ID with exact caption not found on the target account")
    state.update(status="published", id=published_id, phase="reconciled")
    entry["status"] = ("published" if all(entry[p].get("status") == "published"
                                         for p in entry["enabled_platforms"]) else "needs_attention")
    app.write_json(app.STATE, posts)
    app.state_store().event("reconciled", {"post": key, "platform": platform, "published_id": published_id})
