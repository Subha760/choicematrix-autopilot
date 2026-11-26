"""One persistent worker; manual invocations enqueue commands on that same host."""
import argparse
from datetime import datetime, timedelta
import json
import re
import time
from .storage import worker_lock


def validate_settings(app, settings):
    if settings.get("approval_required") is not True:
        raise ValueError("approval_required must remain true")
    for name in ("brand", "audience", "niche", "generation_time_ist"):
        if not isinstance(settings.get(name), str) or not settings[name].strip():
            raise ValueError(f"Missing setting: {name}")
    for value in [settings["generation_time_ist"], *(s.get("publish_time_ist", "") for s in settings.get("slots", {}).values())]:
        if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", value):
            raise ValueError("Schedule must use HH:MM in Asia/Kolkata")
    if not settings.get("slots") or set(settings["slots"]) - {"afternoon", "evening"}:
        raise ValueError("Configure afternoon/evening slots")
    app.configured_platforms(settings)


def cycle(app, action="auto", now=None):
    if action not in ("auto", "draft_now", "publish_now"):
        raise ValueError("Invalid action")
    with worker_lock(app.STATE.parent):
        settings = app.read_json(app.ROOT / "settings.json", {})
        validate_settings(app, settings)
        now = now or datetime.now(app.IST)
        posts = app.read_json(app.STATE, {})
        app.expire_old_posts(posts, settings, now)
        app.write_json(app.STATE, posts)
        today = now.date().isoformat()
        attempts = app.read_json(app.GEN_ERRORS, {})
        generation_at = datetime.fromisoformat(f"{today}T{settings['generation_time_ist']}:00").replace(tzinfo=app.IST)
        generate = action == "draft_now" or (action == "auto" and now >= generation_at and not settings.get("manual_publish_only"))
        if generate:
            for slot in settings["slots"]:
                key = f"{today}_{slot}"
                existing = posts.get(key)
                if existing and existing.get("status") != "preview_failed":
                    continue
                attempt = attempts.get(key, {"count": 0, "next_after": "1970-01-01T00:00:00+00:00"})
                if now < datetime.fromisoformat(attempt["next_after"]) or attempt["count"] >= 6:
                    continue
                try:
                    if existing:
                        app.persist_media(existing)
                        app.send_preview(existing, settings)
                        app.write_json(app.STATE, posts)
                    else:
                        app.create_post(key, settings, posts, slot=slot)
                    attempts.pop(key, None)
                except Exception as exc:
                    count = attempt["count"] + 1
                    attempts[key] = {"count": count, "next_after": (now + timedelta(minutes=min(2**count, 30))).isoformat()}
                    app.state_store().event("draft_failed", {"post": key, "error_type": type(exc).__name__})
                    if count in (1, 6):
                        app.safe_notify(f"Draft {key} failed ({count}/6). Check diagnostics.")
        attempts = {k: v for k, v in attempts.items() if k.split("_")[0] >= (now.date() - timedelta(days=14)).isoformat()}
        app.write_json(app.GEN_ERRORS, attempts)
        app.write_json(app.STATE, posts)
        if action != "draft_now":
            app.process_publications(posts, settings, now)
        return posts


def preflight(app, online=False):
    settings = app.read_json(app.ROOT / "settings.json", {})
    validate_settings(app, settings)
    checks = {"configuration": True, "chromium": bool(app.get_browser_executable()),
              "telegram_credentials": bool(app.getenv("TG_BOT_TOKEN") and app.getenv("TG_APPROVER_ID").isdigit()),
              "ai_credentials": bool(app.getenv("GEMINI_API_KEY") or app.getenv("GROQ_API_KEY")),
              "publish_runtime": app.getenv("ALLOW_LOCAL_PUBLISH").lower() == "true" and app.getenv("GITHUB_ACTIONS").lower() != "true"}
    for platform in app.configured_platforms(settings):
        required = {"fb": ("FB_PAGE_ID", "META_PAGE_TOKEN"), "ig": ("IG_USER_ID", "META_PAGE_TOKEN"),
                    "threads": ("THREADS_USER_ID", "THREADS_ACCESS_TOKEN")}[platform]
        checks[platform + "_credentials"] = all(app.getenv(key) for key in required)
    if online:
        if checks["telegram_credentials"]:
            checks["telegram_polling"] = not bool(app.telegram("getWebhookInfo").get("url"))
        for provider, key_name, url, model in (
            ("gemini", "GEMINI_API_KEY", "https://generativelanguage.googleapis.com/v1beta/models", app.getenv("GEMINI_MODEL") or "gemini-3.5-flash-lite"),
            ("groq", "GROQ_API_KEY", "https://api.groq.com/openai/v1/models", app.getenv("GROQ_MODEL") or "openai/gpt-oss-120b")):
            key = app.getenv(key_name)
            if not key:
                continue
            try:
                headers = {"x-goog-api-key": key} if provider == "gemini" else {"Authorization": f"Bearer {key}"}
                response = app.requests.get(url, headers=headers, timeout=(10, 20))
                response.raise_for_status()
                body = response.json()
                choices = body.get("models", []) if provider == "gemini" else body.get("data", [])
                checks[provider + "_model"] = any(
                    m.get("name", "").removeprefix("models/") == model if provider == "gemini"
                    else m.get("id") == model for m in choices)
            except (app.requests.RequestException, ValueError):
                checks[provider + "_model"] = False
    return checks


def run_worker(app):
    import telegram_bot_daemon
    with worker_lock(app.STATE.parent):
        app.state_store().interrupt_jobs()
        next_cycle, next_health = 0, 0
        while True:
            # Telegram availability must not prevent an already approved due job from progressing.
            try:
                telegram_bot_daemon.poll_once()
            except Exception as exc:
                app.state_store().event("poll_error", {"error_type": type(exc).__name__})
            try:
                job = app.state_store().take_job()
                if job:
                    try:
                        cycle(app, job["action"])
                        app.state_store().finish_job(job["id"], "complete")
                    except Exception:
                        app.state_store().finish_job(job["id"], "failed")
                        raise
                if time.monotonic() >= next_cycle:
                    next_cycle = time.monotonic() + 30
                    cycle(app)
                if time.monotonic() >= next_health:
                    next_health = time.monotonic() + 21600
                    telegram_bot_daemon.check_meta_token_health()
            except Exception as exc:
                app.state_store().event("worker_error", {"error_type": type(exc).__name__})
                print(f"Worker error: {type(exc).__name__}", flush=True)
            time.sleep(3)


def cli(app):
    parser = argparse.ArgumentParser(description="Choicematrix single-host autoposter")
    parser.add_argument("--action", choices=("auto", "draft_now", "publish_now"), default="auto")
    parser.add_argument("--daemon", action="store_true")
    parser.add_argument("--once", action="store_true", help="Exclusive maintenance cycle; stop daemon first")
    parser.add_argument("--preflight", action="store_true")
    parser.add_argument("--online", action="store_true")
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--report", metavar="NEW_HTML_FILE", help="Private calendar and operations report")
    parser.add_argument("--backup", metavar="NEW_FILE")
    parser.add_argument("--restore", metavar="BACKUP_FILE")
    parser.add_argument("--reconcile", nargs=3, metavar=("POST_ID", "PLATFORM", "PUBLISHED_ID"))
    args = parser.parse_args()
    if args.preflight:
        checks = preflight(app, args.online)
        print(json.dumps(checks, indent=2))
        return 0 if all(checks.values()) else 1
    if args.status:
        posts = app.read_json(app.STATE, {})
        print("\n\n".join(app.publication_summary(p) for _, p in sorted(posts.items())))
        return 0
    if args.report:
        from .report import write_report
        print(write_report(app, args.report))
        return 0
    if args.backup:
        from pathlib import Path
        if Path(args.backup).resolve().is_relative_to((app.ROOT / 'docs').resolve()):
            raise ValueError("Private backups cannot be saved in the public media directory")
        app.state_store().backup(args.backup)
        return 0
    if args.restore:
        from .operations import restore
        with worker_lock(app.STATE.parent):
            restore(app.state_store(), args.restore)
        return 0
    if args.reconcile:
        from .operations import reconcile
        with worker_lock(app.STATE.parent):
            reconcile(app, *args.reconcile)
        return 0
    if args.daemon:
        run_worker(app)
    elif args.once:
        cycle(app, args.action)
    else:
        print("Queued job:", app.state_store().enqueue(args.action))
    return 0
