"""Illustrative stock media with retained provenance and checked download tracking."""
from io import BytesIO
from urllib.parse import urlparse
from PIL import Image


def fetch(app, query, width=1080, height=1350, used_ids=None, orientation=None):
    key = app.getenv("UNSPLASH_ACCESS_KEY")
    if not key or not query:
        return None
    orientation = orientation or ("landscape" if width > height else "portrait")
    cleaned = app.re.sub(r"[^\w\s]", " ", query).strip()[:150]
    headers = {"Authorization": f"Client-ID {key}"}
    try:
        response = app.requests.get("https://api.unsplash.com/search/photos", params={"query": cleaned,
            "per_page": 6, "orientation": orientation, "content_filter": "high"}, headers=headers, timeout=(5, 12))
        response.raise_for_status()
        photos = [p for p in response.json().get("results", []) if used_ids is None or p.get("id") not in used_ids]
        if not photos:
            return None
        photo = photos[0]
        url = photo["urls"]["regular"]
        parsed = urlparse(url)
        if parsed.scheme != "https" or parsed.hostname != "images.unsplash.com":
            return None
        downloaded = app.requests.get(url, timeout=(5, 15))
        downloaded.raise_for_status()
        if len(downloaded.content) > 20 * 1024 * 1024:
            return None
        image = app.smart_card_fit(Image.open(BytesIO(downloaded.content)).convert("RGB"), width, height)
        credit = {"id": photo["id"], "photographer": photo.get("user", {}).get("name", "Unsplash contributor"),
            "profile": photo.get("user", {}).get("links", {}).get("html", "https://unsplash.com"),
            "url": photo.get("links", {}).get("html", "https://unsplash.com"), "license": "Unsplash", "illustrative": True}
        image.info["credit"] = credit
        tracking = photo.get("links", {}).get("download_location", "")
        track_url = urlparse(tracking)
        if track_url.scheme != "https" or track_url.hostname != "api.unsplash.com":
            return None
        tracked = app.requests.get(tracking, headers=headers, timeout=(5, 10))
        tracked.raise_for_status()
        if used_ids is not None:
            used_ids.add(photo["id"])
        return image
    except (app.requests.RequestException, KeyError, ValueError, OSError):
        return None
