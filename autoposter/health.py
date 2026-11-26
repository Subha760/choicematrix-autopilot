"""Independent account health checks with private caching and bounded alerts."""
import time


def threads_health(app, force=False):
    settings = app.read_json(app.ROOT / "settings.json", {})
    if "threads" in settings.get("paused_platforms", []):
        return True, "Threads publishing is paused by the owner"
    if "threads" not in app.configured_platforms(settings):
        return True, "Threads is not configured"
    store = app.state_store()
    health = store.read("account_health", {})
    previous = health.get("threads", {})
    now = time.time()
    if not force and now - previous.get("checked_at", 0) < 21600:
        return previous.get("ok", False), previous.get("detail", "Unknown Threads health")
    token, user = app.getenv("THREADS_ACCESS_TOKEN"), app.getenv("THREADS_USER_ID")
    ok, detail = False, "Threads credentials are missing"
    if token and user:
        try:
            response = app.requests.get("https://graph.threads.net/v1.0/me",
                headers={"Authorization": "Bearer " + token}, params={"fields": "id"}, timeout=(10, 20))
            payload = response.json()
            if not isinstance(payload, dict):
                raise ValueError("Invalid account response")
            ok = response.ok and str(payload.get("id")) == user
            if ok:
                detail = "Threads token is valid for the configured account"
            elif payload.get("error", {}).get("code") == 190:
                detail = "Threads authorization expired or was revoked; renew THREADS_ACCESS_TOKEN privately on the VM"
            else:
                detail = "Threads account check failed; inspect account permissions"
        except (app.requests.RequestException, ValueError):
            detail = "Threads account check could not reach the provider"
    record = {"checked_at": now, "ok": ok, "detail": detail,
              "last_alert_at": previous.get("last_alert_at", 0)}
    if not ok and (previous.get("ok", True) or now - previous.get("last_alert_at", 0) >= 86400):
        app.safe_notify(detail)
        record["last_alert_at"] = now
    health["threads"] = record
    store.write("account_health", health)
    return ok, detail
