import copy
import json
import sys
from pathlib import Path
import unittest
from unittest.mock import patch

import test_autopilot as fixtures
from autoposter.drafts import edit_caption
from autoposter.report import usage_summary, write_report
from autoposter.runtime import cli
import telegram_bot_daemon as daemon

app, SETTINGS, NOW = fixtures.app, fixtures.SETTINGS, fixtures.NOW


class FollowupTests(unittest.TestCase):
    setUp = fixtures.WorkflowTests.setUp
    example = fixtures.WorkflowTests.example

    def edit(self, entry, text="An owner-edited caption", platform="fb", settings=None):
        posts = {entry["date"]: entry}
        with patch.object(app, "send_preview") as preview, patch.object(app, "process_publications") as publish:
            result = edit_caption(app, posts, entry["date"], platform, text, settings or SETTINGS, NOW)
        publish.assert_not_called()
        return result, posts[entry["date"]], preview

    def test_caption_edit_creates_revision_and_revokes_approval(self):
        entry = self.example()
        previous_ig = entry["captions"]["ig"]
        result, updated, preview = self.edit(entry, "New caption\nWith a second line")
        self.assertTrue(result[0])
        self.assertEqual(updated["revision"], 2)
        self.assertEqual(updated["captions"]["fb"], "New caption\nWith a second line")
        self.assertEqual(updated["captions"]["ig"], previous_ig)
        self.assertEqual(updated["images"], entry["images"])
        self.assertEqual(updated["status"], "pending")
        self.assertIsNone(updated["approval_hash"])
        self.assertIsNone(updated["review_message_id"])
        self.assertFalse(updated["preview_delivered"])
        self.assertEqual(app.read_json(app.STATE, {})[entry["date"]]["revision"], 2)
        preview.assert_called_once()

    def test_all_updates_each_platform_without_truncation(self):
        entry = self.example()
        result, updated, _ = self.edit(entry, "New shared caption", "all")
        self.assertTrue(result[0])
        self.assertEqual(set(updated["captions"].values()), {"New shared caption"})
        self.assertEqual(updated["caption"], "New shared caption")

    def test_invalid_caption_leaves_draft_and_approval_unchanged(self):
        for text in ("", " \n ", "x" * 2201, "hidden\x00text"):
            entry = self.example()
            before = copy.deepcopy(entry)
            result, updated, preview = self.edit(entry, text)
            self.assertFalse(result[0])
            self.assertEqual(updated, before)
            preview.assert_not_called()

    def test_disabled_platform_cannot_be_edited(self):
        entry = self.example()
        result, updated, preview = self.edit(entry, "Thread caption", "threads")
        self.assertFalse(result[0])
        self.assertEqual(updated, entry)
        preview.assert_not_called()

    def test_threads_and_all_respect_exact_caption_limit(self):
        settings = dict(SETTINGS, platforms=["fb", "ig", "threads"])
        for target in ("threads", "all"):
            entry = self.example()
            entry.pop("captions")
            app.prepare_review(entry, settings)
            result, _, preview = self.edit(entry, "x" * 501, target, settings)
            self.assertFalse(result[0])
            preview.assert_not_called()
            result, updated, _ = self.edit(entry, "x" * 500, target, settings)
            self.assertTrue(result[0])
            self.assertEqual(updated["captions"]["threads"], "x" * 500)

    def test_started_publication_blocks_caption_replacement(self):
        for status in ("published", "ambiguous", "attempting", "awaiting_media", "retry_pending"):
            entry = self.example()
            entry["fb"]["status"] = status
            before = copy.deepcopy(entry)
            result, updated, preview = self.edit(entry)
            self.assertFalse(result[0])
            self.assertEqual(updated, before)
            preview.assert_not_called()

    def test_expired_draft_cannot_be_edited(self):
        entry = self.example()
        entry["date"] = "2026-01-01"
        result, _, preview = self.edit(entry)
        self.assertFalse(result[0])
        preview.assert_not_called()

    def test_failed_edit_preview_is_persisted_without_approval(self):
        entry = self.example()
        posts = {entry["date"]: entry}
        with patch.object(app, "send_preview", side_effect=RuntimeError("delivery failed")):
            with self.assertRaises(RuntimeError):
                edit_caption(app, posts, entry["date"], "fb", "New caption", SETTINGS, NOW)
        saved = app.read_json(app.STATE, {})[entry["date"]]
        self.assertEqual(saved["revision"], 2)
        self.assertEqual(saved["status"], "preview_failed")
        self.assertFalse(saved["preview_delivered"])
        self.assertIsNone(saved["approval_hash"])

    def test_caption_command_requires_current_review_reply_and_owner(self):
        entry = self.example("pending")
        for sender, reply in ((777, 91), (1234, 90), (1234, None)):
            update = {"message": {"from": {"id": sender}, "chat": {"id": sender, "type": "private"},
                                  "text": "/caption fb A new caption", "reply_to_message": {"message_id": reply}}}
            with patch.object(daemon, "tg_api"), patch("autoposter.drafts.edit_caption") as edit:
                daemon.dispatch_update(update, {entry["date"]: entry}, SETTINGS, NOW)
            edit.assert_not_called()

    def test_owner_caption_command_dispatches_exact_draft_and_newlines(self):
        entry = self.example("pending")
        update = {"message": {"from": {"id": 1234}, "chat": {"id": 1234, "type": "private"},
            "text": "/caption ig First line\nSecond line", "reply_to_message": {"message_id": 91}}}
        with patch.object(daemon, "tg_api"), patch("autoposter.drafts.edit_caption", return_value=(True, "updated")) as edit:
            daemon.dispatch_update(update, {entry["date"]: entry}, SETTINGS, NOW)
        self.assertEqual(edit.call_args.args[2:5], (entry["date"], "ig", "First line\nSecond line"))

    def test_backup_rejects_public_directory_without_creating_file(self):
        destination = app.ROOT / "docs/backup.sqlite3"
        with patch.object(sys, "argv", ["autopilot.py", "--backup", str(destination)]):
            with self.assertRaisesRegex(ValueError, "Private backups"):
                cli(app)
        self.assertFalse(destination.exists())

    def test_usage_normalizes_both_providers_without_estimating_missing_tokens(self):
        rows = [(json.dumps(value),) for value in (
            {"provider": "gemini", "ok": True, "usage": {"promptTokenCount": 100, "candidatesTokenCount": 25}},
            {"provider": "gemini", "ok": False},
            {"provider": "groq", "ok": True, "usage": {"prompt_tokens": 80, "completion_tokens": 12}},
            {"provider": "groq", "ok": True, "usage": {"prompt_tokens": -10, "completion_tokens": "unknown"}})]
        rows += [("malformed",), ("[]",)]
        totals = usage_summary(rows)
        self.assertEqual(totals["gemini"]["calls"], 2)
        self.assertEqual(totals["gemini"]["failures"], 1)
        self.assertEqual(totals["gemini"]["input_tokens"], 100)
        self.assertEqual(totals["groq"]["output_tokens"], 12)
        self.assertEqual(totals["groq"]["reported_calls"], 1)

    def test_report_filters_work_without_external_requests(self):
        self.example()
        posts = app.read_json(app.STATE, {})
        second = copy.deepcopy(next(iter(posts.values())))
        second.update(date="2026-10-15_evening", title="A Bengali Thought", language="Bengali", status="needs_attention")
        second["fb"]["status"] = "ambiguous"
        posts[second["date"]] = second
        app.write_json(app.STATE, posts)
        report = write_report(app, app.ROOT / "report.html")
        browser_path = app.get_browser_executable()
        if not browser_path:
            self.skipTest("Chromium is required for report interaction checks")
        from playwright.sync_api import sync_playwright
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(executable_path=browser_path, headless=True)
            try:
                page = browser.new_page()
                page.route("**/*", lambda route: route.abort())
                page.set_content(report.read_text(encoding="utf-8"))
                self.assertEqual(page.locator("#drafts tr:visible").count(), 2)
                page.locator("#state").select_option("attention")
                self.assertEqual(page.locator("#drafts tr:visible").count(), 1)
                page.locator("#language").select_option("Bengali")
                page.locator("#search").fill("no matching draft")
                self.assertTrue(page.locator("#empty").is_visible())
                page.locator("#reset").click()
                self.assertEqual(page.locator("#drafts tr:visible").count(), 2)
                self.assertEqual(page.locator("#result-count").inner_text(), "Showing 2 of 2 drafts")
                page.locator("#search").fill("Bengali Thought")
                self.assertEqual(page.locator("#drafts tr:visible").count(), 1)
                self.assertFalse(page.locator("#empty").is_visible())
            finally:
                browser.close()
