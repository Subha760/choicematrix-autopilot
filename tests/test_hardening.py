"""Regressions for the active daemon, manifest, storage and publication boundaries."""
import copy
import json
import os
import subprocess
import sys
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch
import test_autopilot as fixtures
SETTINGS, NOW, app = fixtures.SETTINGS, fixtures.NOW, fixtures.app
import unittest
import telegram_bot_daemon as daemon
from autoposter import review
from autoposter.content import validate_slides, numbers
from autoposter.operations import restore, reconcile
from autoposter.storage import StateStore, worker_lock
from autoposter.runtime import cycle, validate_settings


class HardeningTests(unittest.TestCase):
    setUp = fixtures.WorkflowTests.setUp
    example = fixtures.WorkflowTests.example

    def update(self, sender=1234, chat=1234, kind="private", action="approve", revision=1, message_id=91):
        return {"update_id": 100, "callback_query": {"id": "callback",
            "from": {"id": sender}, "data": f"{action}:2026-10-15:{revision}",
            "message": {"message_id": message_id, "chat": {"id": chat, "type": kind}}}}

    def dispatch(self, update, entry):
        with patch.object(daemon, "tg_api", return_value={}):
            daemon.dispatch_update(update, {entry["date"]: entry}, SETTINGS, NOW)

    def carousel(self):
        entry = self.example("pending")
        (app.ROOT / "docs/posts/second.jpg").write_bytes(b"second image")
        entry["images"].append("posts/second.jpg")
        app.prepare_review(entry, SETTINGS)
        entry["preview_hash"] = app.approval_digest(entry)
        return entry

    def test_foreign_callback_cannot_approve(self):
        entry = self.example("pending")
        self.dispatch(self.update(sender=777, chat=777), entry)
        self.assertEqual(entry["status"], "pending")

    def test_foreign_text_cannot_call_any_actions(self):
        for text in ("approve afternoon", "/topic hacked", "health", "recreate afternoon"):
            update = {"update_id": 1, "message": {"from": {"id": 777},
                "chat": {"id": 777, "type": "private"}, "text": text}}
            with patch.object(daemon, "tg_api") as api, patch.object(daemon, "create_custom_topic_post") as custom:
                daemon.dispatch_update(update, {}, SETTINGS, NOW)
            api.assert_not_called()
            custom.assert_not_called()

    def test_owner_in_group_cannot_approve(self):
        entry = self.example("pending")
        self.dispatch(self.update(chat=-100, kind="group"), entry)
        self.assertEqual(entry["status"], "pending")

    def test_old_callback_revision_rejected(self):
        entry = self.example("pending")
        entry["revision"] = 2
        self.dispatch(self.update(), entry)
        self.assertEqual(entry["status"], "pending")

    def test_old_callback_message_rejected(self):
        entry = self.example("pending")
        self.dispatch(self.update(message_id=90), entry)
        self.assertEqual(entry["status"], "pending")

    def test_slot_only_callback_rejected(self):
        entry = self.example("pending")
        update = self.update()
        update["callback_query"]["data"] = "approve:afternoon"
        self.dispatch(update, entry)
        self.assertEqual(entry["status"], "pending")

    def test_owner_current_callback_approves_exact_manifest(self):
        entry = self.example("pending")
        entry["publish_time_ist"] = "19:00"
        entry["preview_hash"] = app.approval_digest(entry)
        self.dispatch(self.update(), entry)
        self.assertEqual(entry["status"], "approved")
        self.assertTrue(app.eligible_for_publication(entry, SETTINGS, NOW))

    def test_text_approval_without_reply_is_rejected(self):
        entry = self.example("pending")
        update = {"update_id": 1, "message": {"from": {"id": 1234},
            "chat": {"id": 1234, "type": "private"}, "text": "approve afternoon"}}
        self.dispatch(update, entry)
        self.assertEqual(entry["status"], "pending")

    def test_reapproval_preserves_uncertain_checkpoint(self):
        for status in ("ambiguous", "attempting", "container_created", "published"):
            entry = self.example("pending")
            entry["fb"].update(status=status, id="known", container="container")
            previous = copy.deepcopy(entry["fb"])
            ok, _ = daemon.approve_pending_post({entry["date"]: entry}, target_key=entry["date"], now=NOW)
            self.assertFalse(ok)
            self.assertEqual(entry["fb"], previous)

    def test_caption_changed_after_preview_cannot_be_approved(self):
        entry = self.example("pending")
        entry["caption"] += " changed"
        with self.assertRaises(ValueError):
            review.approve(app.ROOT, entry, NOW, False)

    def test_second_slide_change_blocks_approval(self):
        entry = self.carousel()
        (app.ROOT / "docs/posts/second.jpg").write_bytes(b"tampered")
        with self.assertRaises(ValueError):
            review.approve(app.ROOT, entry, NOW, False)

    def test_second_slide_order_changes_digest(self):
        entry = self.carousel()
        digest = app.approval_digest(entry)
        entry["images"].reverse()
        self.assertNotEqual(digest, app.approval_digest(entry))

    def test_target_account_change_invalidates_approval(self):
        entry = self.example()
        with patch.dict(os.environ, {"FB_PAGE_ID": "different"}):
            self.assertFalse(app.eligible_for_publication(entry, SETTINGS, NOW))

    def test_platform_change_invalidates_approval(self):
        entry = self.example()
        self.assertFalse(app.eligible_for_publication(entry, dict(SETTINGS, platforms=["fb"]), NOW))

    def test_path_traversal_in_manifest_rejected(self):
        entry = self.example("pending")
        entry["images"] = ["../settings.json"]
        entry["image"] = entry["images"][0]
        with self.assertRaises((ValueError, OSError)):
            review.local_manifest(app.ROOT, entry)

    def test_missing_preview_ack_prevents_approval(self):
        entry = self.example("pending")
        with patch.object(app, "telegram", return_value={}):
            with self.assertRaises(RuntimeError):
                app.send_preview(entry, SETTINGS)
        self.assertFalse(entry["preview_delivered"])
        with self.assertRaises(ValueError):
            review.approve(app.ROOT, entry, NOW, False)

    def test_incomplete_album_never_sends_buttons(self):
        entry = self.carousel()
        with patch.object(app, "telegram", return_value=[{"message_id": 1}]) as api:
            with self.assertRaises(RuntimeError):
                app.send_preview(entry, SETTINGS)
        self.assertEqual([call.args[0] for call in api.call_args_list], ["sendMediaGroup"])

    def test_every_public_slide_must_match(self):
        entry = self.carousel()
        entry["status"] = "approved"
        entry["approved_at"] = NOW.isoformat()
        entry["approval_hash"] = app.approval_digest(entry)
        posts = {entry["date"]: entry}
        app.write_json(app.STATE, posts)
        with patch.object(app, "check_image_ready", side_effect=[True, False]) as ready, patch.object(app, "fb_publish") as publish:
            app.process_publications(posts, SETTINGS, NOW)
        self.assertEqual(ready.call_count, 2)
        publish.assert_not_called()
        self.assertEqual(entry["status"], "approved")

    def test_stale_memory_cannot_overwrite_new_reservation(self):
        entry = self.example()
        posts = {entry["date"]: entry}
        changed = copy.deepcopy(posts)
        changed[entry["date"]]["fb"]["status"] = "attempting"
        app.write_json(app.STATE, changed)
        with self.assertRaisesRegex(RuntimeError, "Stale"):
            app.process_publications(posts, SETTINGS, NOW)

    def test_atomic_unique_reservation(self):
        entry = self.example()
        posts = {entry["date"]: entry}
        app.state_store().reserve(posts, entry, "fb")
        stale = copy.deepcopy(posts)
        stale[entry["date"]]["fb"]["status"] = "not_started"
        app.write_json(app.STATE, stale)
        with self.assertRaisesRegex(RuntimeError, "already exists"):
            app.state_store().reserve(stale, stale[entry["date"]], "fb")

    def test_crash_after_publish_marker_cannot_retry(self):
        class Crash(BaseException):
            pass
        entry = self.example()
        posts = {entry["date"]: entry}
        def publish(e):
            e["fb"].update(status="attempting", phase="publish_started")
            app.publication_checkpoint(e, "fb")
            raise Crash()
        with patch.object(app, "check_image_ready", return_value=True), patch.object(app, "fb_publish", side_effect=publish):
            with self.assertRaises(Crash):
                app.process_publications(posts, SETTINGS, NOW)
        restarted = app.read_json(app.STATE, {})
        self.assertEqual(restarted[entry["date"]]["fb"]["phase"], "publish_started")
        with patch.object(app, "check_image_ready", return_value=True), patch.object(app, "fb_publish") as fb, patch.object(app, "ig_publish", side_effect=ValueError("rejected")):
            app.process_publications(restarted, SETTINGS, NOW)
        fb.assert_not_called()
        self.assertEqual(restarted[entry["date"]]["status"], "needs_attention")

    def test_container_wait_resumes_without_second_creation(self):
        entry = self.example()
        entry["enabled_platforms"] = ["ig"]
        entry["publish_targets"] = app.publish_targets(["ig"])
        entry["approval_hash"] = app.approval_digest(entry)
        posts = {entry["date"]: entry}
        app.write_json(app.STATE, posts)
        settings = dict(SETTINGS, platforms=["ig"])
        replies = [{"id": "c1"}, {"status_code": "IN_PROGRESS"}, {"status_code": "FINISHED"}, {"id": "ig1"}]
        with patch.object(app, "check_image_ready", return_value=True), patch.object(app, "meta_request", side_effect=replies) as request:
            app.process_publications(posts, settings, NOW)
            self.assertEqual(entry["ig"]["status"], "awaiting_media")
            app.process_publications(posts, settings, NOW + timedelta(seconds=30))
        self.assertEqual(entry["ig"]["id"], "ig1")
        self.assertEqual([call.args[1] for call in request.call_args_list].count("ig/media"), 1)

    def test_worker_disabled_blocks_container_resume(self):
        entry = self.example("publishing")
        entry["ig"].update(status="awaiting_media", container="known")
        posts = {entry["date"]: entry}
        app.write_json(app.STATE, posts)
        with patch.dict(os.environ, {"ALLOW_LOCAL_PUBLISH": "false"}), patch.object(app, "ig_publish") as ig:
            with self.assertRaises(RuntimeError):
                app.process_publications(posts, SETTINGS, NOW)
        ig.assert_not_called()

    def test_legacy_approval_is_invalidated_on_import(self):
        directory = app.ROOT / "legacy"
        directory.mkdir()
        legacy = directory / "posts.json"
        legacy.write_text(json.dumps({"old": {"status": "approved", "approval_hash": "old"}}), encoding="utf-8")
        loaded = StateStore(directory).read("posts", {}, legacy)
        self.assertEqual(loaded["old"]["status"], "needs_review")
        self.assertIsNone(loaded["old"]["approval_hash"])

    def test_corrupt_legacy_json_stops_import(self):
        directory = app.ROOT / "corrupt"
        directory.mkdir()
        legacy = directory / "posts.json"
        legacy.write_text("{bad", encoding="utf-8")
        with self.assertRaises(ValueError):
            StateStore(directory).read("posts", {}, legacy)

    def test_backup_restore_preserves_newer_attempts_and_offsets(self):
        entry = self.example()
        store = app.state_store()
        backup = app.ROOT / "backup.sqlite3"
        store.write("telegram", {"offset": 10})
        store.backup(backup)
        entry["fb"].update(status="ambiguous", container="known")
        store.write("posts", {entry["date"]: entry})
        store.write("telegram", {"offset": 99})
        restore(store, backup)
        loaded = store.read("posts", {})
        self.assertEqual(loaded[entry["date"]]["fb"]["status"], "ambiguous")
        self.assertIsNone(loaded[entry["date"]]["approval_hash"])
        self.assertEqual(store.read("telegram", {})["offset"], 99)

    def test_duplicate_update_is_not_dispatched_twice(self):
        entry = self.example("pending")
        update = self.update()
        with patch.object(daemon, "tg_api", return_value=[update]), patch.object(daemon, "dispatch_update") as dispatch:
            daemon.poll_once(posts={entry["date"]: entry}, settings=SETTINGS, now=NOW)
            daemon.poll_once(posts={entry["date"]: entry}, settings=SETTINGS, now=NOW)
        dispatch.assert_called_once()

    def test_queue_jobs_are_claimed_once(self):
        store = app.state_store()
        token = store.enqueue("draft_now")
        self.assertEqual(store.take_job(), {"id": token, "action": "draft_now"})
        self.assertIsNone(store.take_job())
        store.interrupt_jobs()
        self.assertIsNone(store.take_job())

    def test_other_process_cannot_take_worker_lock(self):
        code = ("import sys;from autoposter.storage import worker_lock\n"
                "try:\n with worker_lock(sys.argv[1]): pass\n"
                "except (OSError,BlockingIOError): sys.exit(3)\n")
        with worker_lock(app.STATE.parent):
            result = subprocess.run([sys.executable, "-c", code, str(app.STATE.parent)],
                cwd=Path(__file__).resolve().parents[1], capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 3, result.stderr.decode(errors="replace"))

    def test_success_summary_never_invents_post_ids(self):
        entry = self.example("publishing")
        text = review.result_text(entry)
        self.assertNotIn("posted", text)
        self.assertNotIn("PUBLISHED LIVE", text)
        self.assertIn("not_started", text)

    def test_full_slide_schema_and_metrics(self):
        slides = [{"slide_number": i, "tag": "IDEA", "headline": "One Clear Idea", "body": "Try a small practical step."}
                  for i in range(1, 7)]
        item = {"slides": slides}
        validate_slides(item, {})
        slides[2]["body"] = "This gives +340% performance."
        with self.assertRaises(RuntimeError):
            validate_slides(item, {})

    def test_indic_metrics_cannot_bypass_validation(self):
        self.assertTrue(numbers("৪৫০%"))
        self.assertTrue(numbers("१०x"))

    def test_chart_requires_real_dataset(self):
        slides = [{"slide_number": i, "tag": "IDEA", "headline": "One Clear Idea", "body": "Try a practical step."}
                  for i in range(1, 7)]
        slides[2]["visual_badge"] = {"type": "chart", "label": "Growth", "value": "up"}
        with self.assertRaises(RuntimeError):
            validate_slides({"slides": slides}, {})

    def test_missing_or_reordered_slide_is_rejected(self):
        for slides in ([], [{"slide_number": 7}] * 6):
            with self.assertRaises(RuntimeError):
                validate_slides({"slides": slides}, {})

    def test_disabled_approval_configuration_rejected(self):
        with self.assertRaises(ValueError):
            validate_settings(app, dict(SETTINGS, approval_required=False))

    def test_bad_schedule_configuration_rejected(self):
        with self.assertRaises(ValueError):
            validate_settings(app, dict(SETTINGS, slots={"afternoon": {"publish_time_ist": "25:00"}}))

    def test_unknown_queued_action_rejected(self):
        with self.assertRaises(ValueError):
            app.state_store().enqueue("bypass_approval")

    def test_reconcile_requires_target_and_exact_caption(self):
        entry = self.example()
        entry["fb"]["status"] = "ambiguous"
        posts = {entry["date"]: entry}
        app.write_json(app.STATE, posts)
        with patch.object(app, "meta_request", return_value={"id": "123", "from": {"id": "other"}, "message": entry["captions"]["fb"]}):
            with self.assertRaises(ValueError):
                reconcile(app, entry["date"], "fb", "123")
        self.assertEqual(app.read_json(app.STATE, {})[entry["date"]]["fb"]["status"], "ambiguous")

    def test_exact_command_parsing_does_not_approve_incidental_words(self):
        entry = self.example("pending")
        update = {"update_id": 1, "message": {"from": {"id": 1234},
            "chat": {"id": 1234, "type": "private"}, "text": "this looks okay, do not approve"}}
        self.dispatch(update, entry)
        self.assertEqual(entry["status"], "pending")

    def test_draft_now_creates_missing_slots_without_publishing(self):
        settings = dict(SETTINGS, slots={"afternoon": {"publish_time_ist": "14:00"},
                                        "evening": {"publish_time_ist": "20:00"}})
        app.write_json(app.ROOT / "settings.json", settings)
        with patch.object(app, "create_post") as create, patch.object(app, "process_publications") as publish:
            cycle(app, "draft_now", NOW.replace(hour=7))
        self.assertEqual(create.call_count, 2)
        publish.assert_not_called()

    def test_publish_now_never_generates_or_bypasses_approval(self):
        settings = dict(SETTINGS, slots={"afternoon": {"publish_time_ist": "14:00"}})
        app.write_json(app.ROOT / "settings.json", settings)
        with patch.object(app, "create_post") as create, patch.object(app, "process_publications") as publish:
            cycle(app, "publish_now", NOW)
        create.assert_not_called()
        publish.assert_called_once()

    def test_rss_without_valid_timestamp_is_not_labeled_fresh(self):
        from io import BytesIO
        xml = b"<rss><channel><item><title>An interesting technology idea - Example</title><link>https://example.com/story</link></item></channel></rss>"
        with patch("urllib.request.urlopen", side_effect=lambda *a, **k: BytesIO(xml)):
            topic = app.fetch_web_feed_topic("2026-10-15", weekday_idx=0)
        self.assertIsNone(topic)

    def test_report_escapes_draft_text_and_rejects_public_destination(self):
        from autoposter.report import write_report
        entry = self.example()
        entry["title"] = "<script>unsafe</script>"
        app.write_json(app.STATE, {entry["date"]: entry})
        report = write_report(app, app.ROOT / "private.html")
        text = report.read_text(encoding="utf-8")
        self.assertIn("&lt;script&gt;", text)
        self.assertNotIn("<script>", text)
        with self.assertRaises(ValueError):
            write_report(app, app.ROOT / "docs/report.html")

    def test_explicit_auth_rejection_retries_bounded_without_resetting_attempt(self):
        from autoposter.errors import RequestRejected
        entry = self.example()
        posts = {entry["date"]: entry}
        def ig(e):
            e["ig"].update(status="published", id="ig-known")
        with patch.object(app, "check_image_ready", return_value=True), patch.object(app, "safe_notify"), patch.object(
                app, "fb_publish", side_effect=RequestRejected(400, 190)), patch.object(app, "ig_publish", side_effect=ig):
            app.process_publications(posts, SETTINGS, NOW)
        self.assertEqual(entry["fb"]["status"], "retry_pending")
        attempt_token = entry["fb"]["attempt_token"]
        with patch.object(app, "check_image_ready", return_value=True), patch.object(app, "fb_publish") as fb:
            app.process_publications(posts, SETTINGS, NOW + timedelta(minutes=1))
        fb.assert_not_called()
        def fb(e):
            e["fb"].update(status="published", id="fb-known")
        with patch.object(app, "check_image_ready", return_value=True), patch.object(app, "safe_notify"), patch.object(app, "fb_publish", side_effect=fb):
            app.process_publications(posts, SETTINGS, NOW + timedelta(minutes=16))
        self.assertEqual(entry["status"], "published")
        self.assertEqual(entry["fb"]["attempt_token"], attempt_token)

    def test_theme_change_revokes_approval_and_uses_new_revision_files(self):
        entry = self.example()
        entry["slides"] = [{"slide_number": 1, "tag": "IDEA", "headline": "One Clear Idea", "body": "A small step."}]
        posts = {entry["date"]: entry}
        def render(*args, **kwargs):
            self.assertEqual(args[1], 2)
            args[3]["design_mode"] = kwargs["forced_design_mode"]
            (app.ROOT / "docs/posts/new-r2.jpg").write_bytes(b"new revision")
            return ["posts/new-r2.jpg"]
        with patch.object(app, "render_carousel_slides", side_effect=render), patch.object(app, "persist_media"), patch.object(app, "send_preview"):
            ok, _ = daemon.switch_theme_and_send_post(posts, target_key=entry["date"], mode=3)
        self.assertTrue(ok)
        updated = posts[entry["date"]]
        self.assertEqual(updated["revision"], 2)
        self.assertEqual(updated["image"], "posts/new-r2.jpg")
        self.assertEqual(updated["asset_manifest"], review.local_manifest(app.ROOT, updated))
        self.assertIsNone(updated["approval_hash"])
        self.assertEqual(updated["status"], "pending")

    def test_malformed_provider_response_falls_back_to_validated_six_slides(self):
        from autoposter.content import generate
        from unittest.mock import Mock
        bad = Mock(status_code=200)
        bad.json.return_value = {"candidates": []}
        good = Mock(status_code=200)
        good.json.return_value = {"choices": [{"message": {"content": json.dumps({
            "title": "A Valid Fresh Idea", "slides": [{}] * 6})}}]}
        def getenv(name):
            return {"GEMINI_API_KEY": "fake", "GROQ_API_KEY": "fake", "AI_PROVIDER_ORDER": "gemini,groq"}.get(name, "")
        with patch("autoposter.content.requests.post", side_effect=[bad, bad, good]), patch("autoposter.content.time.sleep"):
            item = generate("prompt", {}, getenv, lambda item: item, lambda *args: None)
        self.assertEqual(item["title"], "A Valid Fresh Idea")
