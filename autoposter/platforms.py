"""Resumable platform adapters; checkpoint before and after every external mutation."""


def caption(entry, platform):
    return entry["captions"][platform]


def checkpoint(app, entry, platform, **values):
    entry[platform].update(values)
    app.publication_checkpoint(entry, platform)


def require_id(result):
    if not result.get("id"):
        raise RuntimeError("AMBIGUOUS mutation response without an ID")
    return result["id"]


def facebook(app, entry):
    token, page = app.getenv("META_PAGE_TOKEN", True), app.getenv("FB_PAGE_ID", True)
    state = entry["fb"]
    images = entry["images"]
    if len(images) == 1:
        checkpoint(app, entry, "fb", phase="publish_started", status="attempting")
        result = app.meta_request("POST", f"{page}/photos", token,
            {"url": app.public_url(images[0]), "message": caption(entry, "fb"), "published": "true"})
    else:
        ids = list(state.get("children", []))
        for image in images[len(ids):]:
            checkpoint(app, entry, "fb", phase="upload_started", status="attempting", children=ids)
            result = app.meta_request("POST", f"{page}/photos", token,
                {"url": app.public_url(image), "published": "false"})
            ids.append(require_id(result))
            checkpoint(app, entry, "fb", phase="uploads", status="container_created", children=list(ids))
        checkpoint(app, entry, "fb", phase="publish_started", status="attempting")
        result = app.meta_request("POST", f"{page}/feed", token, {
            "message": caption(entry, "fb"), "attached_media": app.json.dumps([{"media_fbid": value} for value in ids])})
    checkpoint(app, entry, "fb", status="published", phase="complete", id=require_id(result))


def instagram(app, entry):
    token, user = app.getenv("META_PAGE_TOKEN", True), app.getenv("IG_USER_ID", True)
    state = entry["ig"]
    if not state.get("container"):
        if len(entry["images"]) > 1:
            ids = list(state.get("children", []))
            for image in entry["images"][len(ids):]:
                checkpoint(app, entry, "ig", phase="upload_started", status="attempting")
                result = app.meta_request("POST", f"{user}/media", token,
                    {"image_url": app.public_url(image), "is_carousel_item": "true"})
                ids.append(require_id(result))
                checkpoint(app, entry, "ig", children=list(ids), phase="uploads", status="container_created")
            for child in ids:
                status = app.meta_request("GET", child, token, {"fields": "status_code"}).get("status_code")
                if status in ("ERROR", "EXPIRED"):
                    raise ValueError("Instagram child container failed")
                if status != "FINISHED":
                    checkpoint(app, entry, "ig", phase="uploads", status="container_created")
                    return
            data = {"media_type": "CAROUSEL", "children": ",".join(ids), "caption": caption(entry, "ig")}
        else:
            data = {"image_url": app.public_url(entry["images"][0]), "caption": caption(entry, "ig")}
        checkpoint(app, entry, "ig", phase="container_started", status="attempting")
        result = app.meta_request("POST", f"{user}/media", token, data)
        checkpoint(app, entry, "ig", container=require_id(result), phase="processing", status="awaiting_media")
    result = app.meta_request("GET", state["container"], token, {"fields": "status_code,status"})
    status = result.get("status_code")
    if status in ("ERROR", "EXPIRED"):
        raise ValueError("Instagram container failed; inspect account")
    if status != "FINISHED":
        checkpoint(app, entry, "ig", status="awaiting_media", phase="processing")
        return
    checkpoint(app, entry, "ig", phase="publish_started", status="attempting")
    result = app.meta_request("POST", f"{user}/media_publish", token, {"creation_id": state["container"]})
    checkpoint(app, entry, "ig", status="published", phase="complete", id=require_id(result))


def threads(app, entry):
    token, user = app.getenv("THREADS_ACCESS_TOKEN", True), app.getenv("THREADS_USER_ID", True)
    state = entry["threads"]
    if not state.get("container"):
        if len(entry["images"]) > 1:
            ids = list(state.get("children", []))
            for image in entry["images"][len(ids):]:
                checkpoint(app, entry, "threads", phase="upload_started", status="attempting")
                result = app.threads_request("POST", f"{user}/threads", token,
                    {"media_type": "IMAGE", "image_url": app.public_url(image), "is_carousel_item": "true"})
                ids.append(require_id(result))
                checkpoint(app, entry, "threads", children=list(ids), phase="uploads", status="container_created")
            for child in ids:
                status = app.threads_request("GET", child, token, {"fields": "status"}).get("status")
                if status in ("ERROR", "EXPIRED"):
                    raise ValueError("Threads child container failed")
                if status != "FINISHED":
                    checkpoint(app, entry, "threads", phase="uploads", status="container_created")
                    return
            data = {"media_type": "CAROUSEL", "children": ",".join(ids), "text": caption(entry, "threads")}
        else:
            data = {"media_type": "IMAGE", "image_url": app.public_url(entry["images"][0]), "text": caption(entry, "threads")}
        checkpoint(app, entry, "threads", phase="container_started", status="attempting")
        result = app.threads_request("POST", f"{user}/threads", token, data)
        checkpoint(app, entry, "threads", container=require_id(result), phase="processing", status="awaiting_media")
    status = app.threads_request("GET", state["container"], token, {"fields": "status"}).get("status")
    if status in ("ERROR", "EXPIRED"):
        raise ValueError("Threads container failed")
    if status != "FINISHED":
        checkpoint(app, entry, "threads", phase="processing", status="awaiting_media")
        return
    checkpoint(app, entry, "threads", phase="publish_started", status="attempting")
    result = app.threads_request("POST", f"{user}/threads_publish", token, {"creation_id": state["container"]})
    checkpoint(app, entry, "threads", status="published", phase="complete", id=require_id(result))
