"""Owner-directed caption changes that always require a fresh review."""
import copy


def edit_caption(app, posts, key, platform, text, settings, now):
    from . import review
    entry = posts.get(key)
    if not entry or not review.can_replace(entry):
        return False, "This draft has publication history. Its captions cannot be replaced."
    if app.post_is_stale(entry, settings, now):
        return False, "This draft has expired. Generate a fresh draft first."
    platforms = app.configured_platforms(settings)
    if platform != "all" and platform not in platforms:
        return False, "Choose an enabled platform: " + ", ".join(platforms) + ", or all."
    text = text.strip()
    targets = platforms if platform == "all" else [platform]
    if not text or any(ord(c) < 32 and c not in "\n\t" for c in text):
        return False, "Caption must contain text without control characters."
    limit = min(500 if p == "threads" else 2200 for p in targets)
    if len(text) > limit:
        return False, f"This selection allows at most {limit} characters. Nothing was changed."

    candidate = copy.deepcopy(entry)
    # Bind the existing artwork and current enabled destinations before editing.
    app.prepare_review(candidate, settings)
    for target in targets:
        candidate["captions"][target] = text
    if platform == "all":
        candidate["caption"] = text
    candidate.update(revision=entry["revision"] + 1, status="pending", approved_at=None,
                     approval_hash=None, preview_hash=None, preview_delivered=False,
                     review_message_id=None, review_chat_id=None)
    app.prepare_review(candidate, settings)
    posts[key] = candidate
    app.write_json(app.STATE, posts)  # Revoke approval before sending new buttons.
    app.state_store().event("caption_edited", {"post": key, "revision": candidate["revision"],
                                               "platforms": targets})
    try:
        app.send_preview(candidate, settings)
    except Exception:
        candidate.update(status="preview_failed", preview_delivered=False,
                         approved_at=None, approval_hash=None)
        app.write_json(app.STATE, posts)
        raise
    app.write_json(app.STATE, posts)
    return True, f"Caption updated in revision {candidate['revision']}. Review the new preview and approve again."
