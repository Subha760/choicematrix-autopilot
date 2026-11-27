import copy
import hashlib
import json
from pathlib import Path
import shutil
import unittest
from unittest.mock import Mock, patch
import wave

import test_autopilot as fixtures
import telegram_bot_daemon as daemon
from autoposter import music, video, platforms
from PIL import Image

app, SETTINGS, NOW = fixtures.app, fixtures.SETTINGS, fixtures.NOW
SOURCE_ROOT = Path(__file__).resolve().parents[1]
VIDEO_SETTINGS = dict(SETTINGS, music={"enabled": True, "mode": "video", "slide_seconds": 7})


class MusicVideoTests(unittest.TestCase):
    example = fixtures.WorkflowTests.example

    def setUp(self):
        fixtures.WorkflowTests.setUp(self)
        shutil.copyfile(SOURCE_ROOT / "music_catalog.json", app.ROOT / "music_catalog.json")

    def selection(self, category="Technology", posts=None):
        entry = self.example("pending")
        entry.update(category=category, title="A useful idea", caption="Useful context")
        return music.select_music(app.ROOT, entry, posts or {}, VIDEO_SETTINGS)

    def movie(self, status="approved"):
        entry = self.example(status)
        entry["music"] = self.selection()
        entry["music"]["attachment_status"] = "embedded"
        path = app.ROOT / "docs/posts/clip.mp4"
        path.write_bytes(b"\x00\x00\x00\x18ftypisom" + b"test video bytes")
        entry["media_format"] = "video"
        entry["video"] = {"path": "posts/clip.mp4", "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                          "attribution_embedded": True, "duration_seconds": 48}
        app.prepare_review(entry, SETTINGS)
        entry["preview_hash"] = app.approval_digest(entry)
        if status == "approved":
            entry["approval_hash"] = app.approval_digest(entry)
        app.write_json(app.STATE, {entry["date"]: entry})
        return entry

    def test_disabled_music_leaves_legacy_posts_unchanged(self):
        entry = self.example()
        before = app.approval_digest(entry)
        self.assertIsNone(music.select_music(app.ROOT, entry, {}, SETTINGS))
        music.prepare_media(app, entry, {}, SETTINGS)
        self.assertEqual(before, app.approval_digest(entry))

    def test_music_matches_the_primary_category(self):
        cases = {"Technology": "shiny-tech", "Science & Nature": "dreams-become-real",
                 "Productivity": "dreams-become-real", "Health": "dreams-become-real"}
        for category, expected in cases.items():
            self.assertEqual(self.selection(category)["track"]["id"], expected)

    def test_regional_instrumental_requires_matching_post_context(self):
        entry = self.example("pending")
        entry.update(category="Culture", title="Hanoi celebrates local crafts", caption="A craft festival")
        self.assertNotEqual(music.select_music(app.ROOT, entry, {}, VIDEO_SETTINGS)["track"]["id"], "dhaka")
        entry["title"] = "Kolkata celebrates Bengal crafts"
        self.assertEqual(music.select_music(app.ROOT, entry, {}, VIDEO_SETTINGS)["track"]["id"], "dhaka")

    def test_encoded_download_paths_cannot_escape_the_music_endpoint(self):
        for url in ("https://incompetech.com/music/royalty-free/mp3-royaltyfree/%2e%2e/other.mp3",
                    "https://incompetech.com:9999/music/royalty-free/mp3-royaltyfree/Carefree.mp3"):
            self.assertFalse(video.allowed_audio_url(url))

    def test_creative_music_avoids_recent_repeat_when_an_alternative_exists(self):
        previous = self.selection("Creativity")
        posts = {"2026-10-14": {"music": previous}}
        current = self.selection("Creativity", posts)
        self.assertNotEqual(previous["track"]["id"], current["track"]["id"])

    def test_selection_is_deterministic_and_instrumental_metadata_is_known(self):
        self.assertEqual(self.selection(), self.selection())
        for track in music.load_catalog(app.ROOT):
            self.assertEqual(track["artist"], "Kevin MacLeod")
            self.assertEqual(track["license"], "CC BY 4.0")
            self.assertTrue(track["audio_url"].startswith("https://incompetech.com/"))

    def test_sensitive_topics_choose_silence(self):
        for title in ("A memorial after a tragedy", "শোক ও স্মৃতি", "निधन पर श्रद्धांजलि"):
            entry = self.example("pending")
            entry["title"] = title
            self.assertEqual(music.select_music(app.ROOT, entry, {}, VIDEO_SETTINGS)["mode"], "none")

    def test_invalid_catalog_fails_without_selecting_random_music(self):
        path = app.ROOT / "music_catalog.json"
        data = json.loads(path.read_text())
        data["tracks"].append(copy.deepcopy(data["tracks"][0]))
        path.write_text(json.dumps(data))
        with self.assertRaises(ValueError):
            self.selection()

    def test_music_download_cannot_use_an_unapproved_host(self):
        track = self.selection()["track"]
        track["audio_url"] = "https://example.com/music.mp3"
        with patch.object(app.requests, "get") as request:
            with self.assertRaises(ValueError):
                video.audio_file(app, track)
        request.assert_not_called()

    def test_download_redirect_is_not_followed(self):
        response = Mock(status_code=302)
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        with patch.object(app.requests, "get", return_value=response) as request:
            with self.assertRaises(RuntimeError):
                video.audio_file(app, self.selection()["track"])
        self.assertFalse(request.call_args.kwargs["allow_redirects"])

    def test_video_tampering_blocks_publication(self):
        entry = self.movie()
        self.assertTrue(app.eligible_for_publication(entry, SETTINGS, NOW))
        (app.ROOT / "docs/posts/clip.mp4").write_bytes(b"changed")
        self.assertFalse(app.eligible_for_publication(entry, SETTINGS, NOW))

    def test_music_change_invalidates_existing_approval(self):
        entry = self.movie()
        entry["music"]["track"]["id"] = "carefree"
        self.assertFalse(app.eligible_for_publication(entry, SETTINGS, NOW))

    def test_missing_video_or_credit_cannot_be_approved(self):
        entry = self.movie("pending")
        entry["video"]["attribution_embedded"] = False
        with self.assertRaises(ValueError):
            app.prepare_review(entry, SETTINGS)
        entry["video"]["attribution_embedded"] = True
        (app.ROOT / "docs/posts/clip.mp4").unlink()
        with self.assertRaises(FileNotFoundError):
            app.prepare_review(entry, SETTINGS)

    def test_failed_video_delivery_never_sends_approval_buttons(self):
        entry = self.movie("pending")
        with patch.object(app, "telegram", return_value={}) as api:
            with self.assertRaises(RuntimeError):
                app.send_preview(entry, SETTINGS)
        self.assertEqual([c.args[0] for c in api.call_args_list], ["sendVideo"])
        self.assertFalse(entry["preview_delivered"])

    def test_complete_video_is_reviewed_before_images_and_buttons(self):
        entry = self.movie("pending")
        with patch.object(app, "telegram", side_effect=[{"message_id": 1}, {"message_id": 2}, {"message_id": 3}]) as api:
            app.send_preview(entry, SETTINGS)
        self.assertEqual([c.args[0] for c in api.call_args_list], ["sendVideo", "sendPhoto", "sendMessage"])
        text = api.call_args.args[1]["text"]
        self.assertIn("soundtrack", text)
        self.assertIn("Shiny Tech", text)
        self.assertIn("Instagram Reel", text)
        self.assertTrue(entry["preview_delivered"])

    def test_unavailable_public_video_prevents_any_reservation(self):
        entry = self.movie()
        with patch.object(app, "check_image_ready", return_value=True), patch.object(app, "check_video_ready", return_value=False), \
             patch.object(app, "durable_reserve") as reserve:
            app.process_publications({entry["date"]: entry}, SETTINGS, NOW)
        reserve.assert_not_called()

    def test_instagram_video_uses_reels_endpoint_without_carousel_children(self):
        entry = self.movie()
        with patch.object(app, "publication_checkpoint"), patch.object(app, "public_url", return_value="https://example.com/video.mp4"), \
             patch.object(app, "meta_request", side_effect=[{"id": "container"}, {"status_code": "FINISHED"}, {"id": "reel"}]) as request:
            platforms.instagram(app, entry)
        payload = request.call_args_list[0].args[3]
        self.assertEqual(payload["media_type"], "REELS")
        self.assertIn("video_url", payload)
        self.assertNotIn("children", payload)
        self.assertEqual(entry["ig"]["id"], "reel")

    def test_facebook_video_wait_resumes_without_duplicate_upload(self):
        entry = self.movie()
        replies = [{"id": "fb-video"}, {"status": {"video_status": "processing"}}, {"status": {"video_status": "ready"}}]
        with patch.object(app, "publication_checkpoint"), patch.object(app, "public_url", return_value="https://example.com/video.mp4"), \
             patch.object(app, "meta_request", side_effect=replies) as request:
            platforms.facebook(app, entry)
            self.assertEqual(entry["fb"]["status"], "awaiting_media")
            platforms.facebook(app, entry)
        self.assertEqual(sum(c.args[0] == "POST" for c in request.call_args_list), 1)
        self.assertEqual(request.call_args_list[0].args[1], "page/videos")
        self.assertEqual(entry["fb"]["id"], "fb-video")

    def test_threads_video_payload(self):
        entry = self.movie()
        entry["captions"]["threads"] = entry["caption"][:500]
        with patch.object(app, "getenv", return_value="fake"), patch.object(app, "publication_checkpoint"), \
             patch.object(app, "public_url", return_value="https://example.com/video.mp4"), \
             patch.object(app, "threads_request", side_effect=[{"id": "container"}, {"status": "FINISHED"}, {"id": "video"}]) as request:
            platforms.threads(app, entry)
        self.assertEqual(request.call_args_list[0].args[3]["media_type"], "VIDEO")
        self.assertEqual(entry["threads"]["id"], "video")

    def test_stale_or_unauthorized_music_reply_cannot_change_a_post(self):
        entry = self.movie("pending")
        for sender, reply in ((777, 91), (1234, 1), (1234, None)):
            update = {"message": {"from": {"id": sender}, "chat": {"id": sender, "type": "private"},
                "text": "/music none", "reply_to_message": {"message_id": reply}}}
            with patch.object(daemon, "tg_api"), patch("autoposter.music.change_music") as change:
                daemon.dispatch_update(update, {entry["date"]: entry}, SETTINGS, NOW)
            change.assert_not_called()

    def test_music_cannot_erase_started_publication(self):
        entry = self.movie()
        entry["fb"]["status"] = "ambiguous"
        before = copy.deepcopy(entry)
        ok, _ = music.change_music(app, {entry["date"]: entry}, entry["date"], "none", VIDEO_SETTINGS, NOW)
        self.assertFalse(ok)
        self.assertEqual(entry, before)

    def test_renderer_failure_revokes_previous_approval(self):
        entry = self.movie()
        posts = {entry["date"]: entry}
        with patch("autoposter.video.render", side_effect=RuntimeError("encoder failed")):
            with self.assertRaises(RuntimeError):
                music.change_music(app, posts, entry["date"], "carefree", VIDEO_SETTINGS, NOW)
        saved = app.read_json(app.STATE, {})[entry["date"]]
        self.assertIsNone(saved["approval_hash"])
        self.assertEqual(saved["status"], "needs_review")

    def test_real_ffmpeg_render_contains_h264_aac_and_full_attribution(self):
        self.assertTrue(video.tools_available(), "FFmpeg/FFprobe are required for video tests")
        entry = self.example("pending")
        Image.new("RGB", (360, 450), (35, 100, 130)).save(app.ROOT / "docs" / entry["image"], "JPEG")
        entry["music"] = self.selection()
        audio = app.STATE.parent / "test.wav"
        audio.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(audio), "wb") as stream:
            stream.setparams((1, 2, 48000, 0, "NONE", "not compressed"))
            stream.writeframes(b"\0\0" * 48000)
        result = video.render(app, entry, VIDEO_SETTINGS, audio_path=audio, dimensions=(180, 320), slide_seconds=0.5)
        metadata = video.probe(app.ROOT / "docs" / result["path"])
        self.assertTrue(result["attribution_embedded"])
        self.assertAlmostEqual(float(metadata["format"]["duration"]), 6.5, delta=0.5)
        self.assertEqual({s["codec_name"] for s in metadata["streams"]}, {"h264", "aac"})
        entry["video"] = result
        self.assertEqual(video.local_manifest(app.ROOT, entry)["sha256"], result["sha256"])


if __name__ == "__main__":
    unittest.main()
