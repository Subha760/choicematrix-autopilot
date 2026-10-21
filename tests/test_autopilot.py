import copy
import hashlib
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import autopilot as app

SETTINGS = {
    "brand": "Daily Spark", "niche": "mixed", "audience": "adults", "language": "English",
    "publish_time_ist": "19:00", "generation_time_ist": "09:00", "publishing_enabled": True,
    "approval_required": True, "require_source_for_facts": True,
    "max_post_age_hours": 48, "content_style": "Accessible, helpful and modest",
}
NOW = datetime(2026, 10, 15, 21, tzinfo=app.IST)


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        for attr, path in (("ROOT", root), ("STATE", root / "state" / "posts.json"),
                           ("TG_STATE", root / "state" / "telegram.json"),
                           ("GEN_ERRORS", root / "state" / "generation-attempts.json"),
                           ("IMAGE_DIR", root / "docs" / "posts")):
            previous = getattr(app, attr)
            setattr(app, attr, path)
            self.addCleanup(setattr, app, attr, previous)
        (root / "docs" / "posts").mkdir(parents=True)
        (root / "docs" / "posts" / "2026-10-15-v1.jpg").write_bytes(b"test image bytes")

    def example(self, status="approved"):
        image = "posts/2026-10-15-v1.jpg"
        p = {
            "date": "2026-10-15", "revision": 1,
            "title": "Give Your Day Some Structure",
            "subtitle": "Choose one manageable task to begin.",
            "caption": "Start by naming a small task. #Learning #Tips #DailyIdeas",
            "category": "Everyday Life", "content_type": "practical", "source": None,
            "image": image, "image_sha256": hashlib.sha256((app.ROOT / 'docs' / image).read_bytes()).hexdigest(),
            "status": status,
            "approved_at": "2026-10-15T11:00:00+05:30" if status in ("approved", "publishing") else None,
            "approval_hash": None,
            "fb": {"status": "not_started", "id": None},
            "ig": {"status": "not_started", "id": None, "container": None},
            "threads": {"status": "not_started", "id": None, "container": None},
        }
        if p["approved_at"]:
            p["approval_hash"] = app.approval_digest(p)
        return p

    def test_seven_rotating_weekday_pillars(self):
        expected = ["Productivity", "Digital Safety", "Science & Nature", "Everyday Life",
                    "Sustainable Living", "Creativity & DIY", "Culture & Learning"]
        days = ["2026-10-12", "2026-10-13", "2026-10-14", "2026-10-15",
                "2026-10-16", "2026-10-17", "2026-10-18"]
        self.assertEqual([app.choose_topic(day)["category"] for day in days], expected)

    def test_facts_have_curated_sources_not_ai_fabricated(self):
        for day in ("2026-10-13", "2026-10-14", "2026-10-18"):
            selected = app.choose_topic(day)
            self.assertEqual(selected["type"], "sourced")
            self.assertTrue(selected["source"]["url"].startswith("https://"))
            self.assertTrue(selected["seed"])
        self.assertIsNone(app.choose_topic("2026-10-15")["source"])

    def test_revision_rotates_factual_seed(self):
        self.assertNotEqual(app.choose_topic("2026-10-14", 1)["seed"],
                            app.choose_topic("2026-10-14", 2)["seed"])

    def test_ai_validation_rejects_quote_statistics_and_copied_headlines(self):
        good = {"title": "Practice A Small Creative Habit", "subtitle": "Try drawing your coffee mug.",
                "caption": "Sketch something nearby. #Art #Creativity #SmallIdeas"}
        slot = app.choose_topic("2026-10-15")
        self.assertEqual(app.validate_ai_copy(dict(good), {}, slot)["title"], good["title"])
        for bad in ('As a new study says, learn!', 'This works 100% of the time.',
                    'Here is the “famous” quote.', 'Visit https://example.com now.'):
            altered = dict(good, caption=f"{bad} #Art #Creativity #SmallIdeas")
            with self.assertRaises(RuntimeError):
                app.validate_ai_copy(altered, {}, slot)
        old = {"yesterday": {"title": good["title"], "caption": "Other caption"}}
        with self.assertRaises(RuntimeError):
            app.validate_ai_copy(dict(good), old, slot)

    def test_ai_generation_attaches_exact_curated_source(self):
        class MockReply:
            status_code = 200
            def json(self):
                return {"candidates": [{"content": {"parts": [{"text": json.dumps({
                    "title": "Use A Safer Login Habit",
                    "subtitle": "Try enabling another sign-in check.",
                    "caption": "Another sign-in factor adds a barrier. #Safety #Passwords #DigitalTips"
                })}]}}]}
        with patch.object(app, 'getenv', side_effect=lambda name, required=False: 'fake' if name == 'GEMINI_API_KEY' else ''), \
             patch.object(app.requests, 'post', return_value=MockReply()):
            post = app.generated_content(SETTINGS, {}, "2026-10-13")
        self.assertEqual(post["category"], "Digital Safety")
        self.assertIn(post["source"]["url"], post["caption"])
        self.assertEqual(post["content_type"], "sourced")

    def test_groq_fallback_when_gemini_unavailable(self):
        class MockGeminiFail:
            status_code = 503
        class MockGroqSuccess:
            status_code = 200
            def json(self):
                return {"choices": [{"message": {"content": json.dumps({
                    "title": "Use A Safer Login Habit",
                    "subtitle": "Try enabling another sign-in check.",
                    "caption": "Another sign-in factor adds a barrier. #Safety #Passwords #DigitalTips"
                })}}]}
        def mock_post(url, *args, **kwargs):
            if "generativelanguage" in url:
                return MockGeminiFail()
            elif "groq.com" in url:
                return MockGroqSuccess()
            return MockGeminiFail()

        with patch.object(app, 'getenv', side_effect=lambda name, required=False: 'fake_gemini' if name == 'GEMINI_API_KEY' else ('fake_groq' if name == 'GROQ_API_KEY' else '')), \
             patch.object(app.time, 'sleep', return_value=None), \
             patch.object(app.requests, 'post', side_effect=mock_post):
            post = app.generated_content(SETTINGS, {}, "2026-10-13")
        self.assertEqual(post["title"], "Use A Safer Login Habit")
        self.assertEqual(post["category"], "Digital Safety")

    def test_code_draws_original_full_resolution_jpeg(self):
        from PIL import Image
        for category in ("Productivity", "Digital Safety", "Science & Nature", "Creativity & DIY"):
            path = app.image_art("2026-10-15", 3, SETTINGS, {
                "title": "Make One Idea Feel Clear", "subtitle": "Keep the next step simple and small.",
                "category": category
            })
            with Image.open(app.ROOT / "docs" / path) as out:
                self.assertEqual((out.format, out.size), ("JPEG", (1080, 1350)))

    def test_disabled_publishing_stops_before_network(self):
        p = self.example()
        settings = dict(SETTINGS, publishing_enabled=False)
        with patch.object(app, 'check_image_ready') as check:
            app.process_publications({p['date']: p}, settings, NOW)
            check.assert_not_called()

    def test_before_schedule_stays_approved_without_error(self):
        p = self.example()
        with patch.object(app, 'check_image_ready') as check:
            app.process_publications({p['date']: p}, SETTINGS,
                                     datetime(2026, 10, 15, 18, 45, tzinfo=app.IST))
            check.assert_not_called()
        self.assertEqual(p['status'], 'approved')

    def test_unapproved_and_rejected_never_publish(self):
        p = self.example('pending')
        rejected = self.example('rejected')
        with patch.object(app, 'check_image_ready') as check:
            app.process_publications({'a': p, 'b': rejected}, SETTINGS, NOW)
            check.assert_not_called()

    def test_approval_text_mutation_invalidates_authorization(self):
        p = self.example()
        self.assertTrue(app.eligible_for_publication(p, SETTINGS, NOW))
        p['caption'] += ' An unapproved sentence.'
        self.assertFalse(app.eligible_for_publication(p, SETTINGS, NOW))

    def test_image_tampering_invalidates_authorization(self):
        p = self.example()
        (app.ROOT / 'docs' / p['image']).write_bytes(b'a different image')
        self.assertFalse(app.eligible_for_publication(p, SETTINGS, NOW))

    def test_unsourced_fact_is_blocked(self):
        p = self.example()
        p['content_type'] = 'sourced'
        p['approval_hash'] = app.approval_digest(p)
        self.assertFalse(app.eligible_for_publication(p, SETTINGS, NOW))

    def test_stale_posts_expire_and_never_publish(self):
        p = self.example()
        older = datetime(2026, 10, 17, 1, tzinfo=app.IST)
        app.expire_old_posts({p['date']: p}, SETTINGS, older)
        self.assertEqual(p['status'], 'expired')
        self.assertIsNone(p['approval_hash'])

    def test_approved_post_only_publishes_once_per_platform(self):
        p = self.example()
        p['fb'] = {'status': 'published', 'id': 'fb123'}
        p['threads'] = {'status': 'published', 'id': 'th123'}
        posts = {p['date']: p}
        def fake_ig(e):
            e['ig']['status'] = 'published'
            e['ig']['id'] = 'ig123'
        def reserve(posts, e, platform):
            e[platform]['status'] = 'attempting'
        with patch.object(app, 'public_url', return_value='https://example.org/photo.jpg'), \
             patch.object(app, 'check_image_ready', return_value=True), \
             patch.object(app, 'durable_reserve', side_effect=reserve) as rs, \
             patch.object(app, 'fb_publish') as fb, \
             patch.object(app, 'ig_publish', side_effect=fake_ig) as ig, \
             patch.object(app, 'safe_notify'):
            app.process_publications(posts, SETTINGS, NOW)
            fb.assert_not_called()
            ig.assert_called_once()
            rs.assert_called_once()
        self.assertEqual(p['status'], 'published')

    def test_ambiguous_and_attempting_states_do_not_retry(self):
        for failure in ('ambiguous', 'attempting'):
            p = self.example()
            p['fb'] = {'status': failure, 'id': None}
            p['ig'] = {'status': 'published', 'id': 'ig123', 'container': 'c123'}
            p['threads'] = {'status': 'published', 'id': 'th123', 'container': 'c123'}
            with patch.object(app, 'public_url', return_value='https://example.org/photo.jpg'), \
                 patch.object(app, 'check_image_ready', return_value=True), \
                 patch.object(app, 'durable_reserve') as rs, \
                 patch.object(app, 'fb_publish') as fb:
                app.process_publications({p['date']: p}, SETTINGS, NOW)
                fb.assert_not_called()
                rs.assert_not_called()
            self.assertEqual(p['status'], 'needs_attention')

    def test_failed_reservation_prevents_any_external_publish(self):
        p = self.example()
        with patch.object(app, 'public_url', return_value='https://example.org/photo.jpg'), \
             patch.object(app, 'check_image_ready', return_value=True), \
             patch.object(app, 'durable_reserve', side_effect=RuntimeError('durable commit failed')), \
             patch.object(app, 'fb_publish') as fb, \
             patch.object(app, 'ig_publish') as ig, \
             patch.object(app, 'safe_notify'):
            app.process_publications({p['date']: p}, SETTINGS, NOW)
            fb.assert_not_called()
            ig.assert_not_called()
        self.assertEqual(p['status'], 'needs_attention')

    def test_approval_callback_denies_another_user(self):
        entry = self.example('pending')
        updates = [{'update_id': 40, 'callback_query': {
            'id': 'cb1', 'data': 'approve:2026-10-15:1', 'from': {'id': 9999}}}]
        with patch.object(app, 'telegram', side_effect=[updates, True]), \
             patch.object(app, 'getenv', return_value='1234'):
            app.take_approvals({entry['date']: entry}, SETTINGS, now=NOW)
        self.assertEqual(entry['status'], 'pending')
        self.assertEqual(json.loads(app.TG_STATE.read_text())['offset'], 41)

    def test_approval_callback_writes_digest_only_for_owner(self):
        entry = self.example('pending')
        updates = [{'update_id': 40, 'callback_query': {
            'id': 'cb1', 'data': 'approve:2026-10-15:1', 'from': {'id': 1234}}}]
        with patch.object(app, 'telegram', side_effect=[updates, True]), \
             patch.object(app, 'getenv', return_value='1234'):
            app.take_approvals({entry['date']: entry}, SETTINGS, now=NOW)
        self.assertEqual(entry['status'], 'approved')
        self.assertEqual(entry['approval_hash'], app.approval_digest(entry))

    def test_preview_sends_both_artwork_and_full_caption(self):
        entry = self.example('pending')
        with patch.object(app, 'getenv', return_value='1234'), \
             patch.object(app, 'telegram') as tg:
            app.send_preview(entry, SETTINGS)
        self.assertEqual([c.args[0] for c in tg.call_args_list], ['sendPhoto', 'sendMessage'])
        full = tg.call_args_list[1].args[1]
        self.assertIn(entry['caption'], full['text'])
        buttons = json.loads(full['reply_markup'])['inline_keyboard']
        self.assertIn('approve:2026-10-15:1', str(buttons))

    def test_old_preview_button_cannot_approve_regenerated_revision(self):
        entry = self.example('pending')
        entry['revision'] = 2
        updates = [{'update_id': 11, 'callback_query': {
            'id': 'cb2', 'data': 'approve:2026-10-15:1', 'from': {'id': 1234}}}]
        with patch.object(app, 'telegram', side_effect=[updates, True]), \
             patch.object(app, 'getenv', return_value='1234'):
            app.take_approvals({entry['date']: entry}, SETTINGS, now=NOW)
        self.assertEqual(entry['status'], 'pending')
        self.assertIsNone(entry['approval_hash'])

    def test_full_create_draft_remains_pending_until_human_click(self):
        mock_copy = {"title": "Choose One Fresh Action Today",
                     "subtitle": "Write one next step on paper.",
                     "caption": "Pick one clear step. #Plan #SmallHabits #Focus",
                     "category": "Productivity", "content_type": "practical", "source": None}
        posts = {}
        with patch.object(app, 'generated_content', return_value=mock_copy), \
             patch.object(app, 'send_preview') as preview:
            created = app.create_post('2026-10-15', SETTINGS, posts)
            preview.assert_called_once()
        self.assertEqual(created['status'], 'pending')
        self.assertFalse(created['approval_hash'])
        self.assertEqual(created['image_sha256'], hashlib.sha256(
            (app.ROOT / 'docs' / created['image']).read_bytes()).hexdigest())
        self.assertFalse(app.eligible_for_publication(created, SETTINGS, NOW))

    def test_due_only_after_time_and_approval(self):
        p = self.example()
        self.assertFalse(app.due(p, SETTINGS, datetime(2026, 10, 15, 18, 30, tzinfo=app.IST)))
        self.assertTrue(app.due(p, SETTINGS, NOW))

    def test_threads_publishing_lifecycle(self):
        p = self.example()
        p["threads"] = {"status": "not_started", "id": None, "container": None}
        mock_container_resp = unittest.mock.MagicMock(ok=True, status_code=200)
        mock_container_resp.headers = {"Content-Type": "application/json"}
        mock_container_resp.json.return_value = {"id": "th_container_123"}

        mock_status_resp = unittest.mock.MagicMock(ok=True, status_code=200)
        mock_status_resp.headers = {"Content-Type": "application/json"}
        mock_status_resp.json.return_value = {"status": "FINISHED"}

        mock_publish_resp = unittest.mock.MagicMock(ok=True, status_code=200)
        mock_publish_resp.headers = {"Content-Type": "application/json"}
        mock_publish_resp.json.return_value = {"id": "th_post_999"}

        with patch.object(app, "getenv", side_effect=lambda k, req=False: "th_user_1" if k == "THREADS_USER_ID" else ("mock_token" if "TOKEN" in k else "")), \
             patch.object(app.requests, "post", side_effect=[mock_container_resp, mock_publish_resp]), \
             patch.object(app.requests, "get", return_value=mock_status_resp), \
             patch.object(app, "public_url", return_value="https://example.com/test.jpg"):
            app.threads_publish(p)

        self.assertEqual(p["threads"]["status"], "published")
        self.assertEqual(p["threads"]["id"], "th_post_999")

    def test_dual_slots_alternation_hindi_bengali(self):
        self.assertEqual(app.get_evening_language("2026-10-20"), "Hindi")
        self.assertEqual(app.get_evening_language("2026-10-21"), "Bengali")
        self.assertEqual(app.get_evening_language("2026-10-22"), "Hindi")
        self.assertEqual(app.get_evening_language("2026-10-23"), "Bengali")

    def test_indic_digits_translation(self):
        self.assertEqual(app.to_indic_digits("0123456789", "Bengali"), "০১২৩৪৫৬৭৮৯")
        self.assertEqual(app.to_indic_digits("0123456789", "Hindi"), "\u0966\u0967\u0968\u0969\u096a\u096b\u096c\u096d\u096e\u096f")
        self.assertEqual(app.to_indic_digits("0123456789", "English"), "0123456789")

    def test_choose_topic_slots(self):
        afternoon = app.choose_topic("2026-10-21", slot_type="afternoon")
        self.assertEqual(afternoon["language"], "English")
        self.assertEqual(afternoon["slot"], "afternoon")

        evening_bengal = app.choose_topic("2026-10-21", slot_type="evening")
        self.assertEqual(evening_bengal["language"], "Bengali")
        self.assertEqual(evening_bengal["slot"], "evening")

        evening_hindi = app.choose_topic("2026-10-20", slot_type="evening")
        self.assertEqual(evening_hindi["language"], "Hindi")
        self.assertEqual(evening_hindi["slot"], "evening")


if __name__ == '__main__':
    unittest.main()
