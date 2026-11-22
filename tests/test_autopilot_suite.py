"""Deterministic typography and layout verification; never calls live services."""
import copy
import hashlib
from pathlib import Path
import shutil
import unittest
from unittest.mock import patch
import test_autopilot as fixtures
from PIL import Image

app = fixtures.app
SOURCE_ROOT = Path(__file__).resolve().parents[1]


class RenderingTests(unittest.TestCase):
    setUp = fixtures.WorkflowTests.setUp

    def item(self, language='English'):
        texts = {
            'English': ('Make Space for Clear Thinking', 'A small pause can help you choose your next step. Write down one task, put away a distraction, and give that task a little room. Return to your plan when you need a reminder.'),
            'Hindi': ('अपने अगले कदम को स्पष्ट बनाएं', 'एक छोटा विराम लेकर अपने अगले काम के बारे में सोचें। कागज़ पर एक आसान कदम लिखें और ध्यान भटकाने वाली चीज़ों को थोड़ी देर के लिए दूर रखें। अपनी योजना को देखकर फिर से शुरुआत करें।'),
            'Bengali': ('পরের কাজটি সহজ করে নিন', 'একটু থেমে নিজের পরবর্তী কাজটি নিয়ে ভাবুন। কাগজে একটি সহজ পদক্ষেপ লিখে রাখুন এবং মনোযোগ সরিয়ে নেওয়া জিনিসগুলো কিছুক্ষণ দূরে রাখুন। প্রয়োজন হলে নিজের পরিকল্পনাটি আবার পড়ুন। AI-এর সাহায্য নিলেও নিজের সিদ্ধান্ত নিজে নিন।'),
        }
        headline, body = texts[language]
        return {'title': headline, 'subtitle': body, 'category': 'IDEAS', 'language': language,
                'slides': [{'slide_number': i, 'tag': 'IDEAS', 'headline': headline, 'body': body}
                           for i in range(1, 7)]}

    def prepare_fonts(self):
        shutil.copytree(SOURCE_ROOT / 'fonts', app.ROOT / 'fonts')

    def test_chromium_all_three_languages_fit_and_render(self):
        self.prepare_fonts()
        browser = app.get_browser_executable()
        if not browser:
            self.skipTest('Playwright Chromium must be installed for visual tests')
        for language in ('English', 'Hindi', 'Bengali'):
            item = self.item(language)
            with patch.object(app, 'fetch_unsplash_image', return_value=Image.new('RGB', (920, 340), (35, 100, 130))):
                paths = app.render_carousel_slides('2026-10-15_evening', 1, {'brand': 'Choicematrix'}, item,
                    slot='evening', forced_design_mode=2)
            self.assertEqual(len(paths), 6)
            self.assertEqual(item['design_mode'], 2)
            for path in paths:
                with Image.open(app.ROOT / 'docs' / path) as image:
                    self.assertEqual(image.size, (1080, 1350))
                    self.assertEqual(image.format, 'JPEG')

    def test_chromium_theme_override_changes_actual_image(self):
        self.prepare_fonts()
        if not app.get_browser_executable():
            self.skipTest('Playwright Chromium must be installed for visual tests')
        digests = []
        for mode in range(4):
            item = self.item()
            with patch.object(app, 'fetch_unsplash_image', return_value=None):
                paths = app.render_carousel_slides('2026-10-15_afternoon', mode+1, {'brand': 'Choicematrix'}, item,
                                                   forced_design_mode=mode)
            self.assertEqual(item['design_mode'], mode)
            digests.append(hashlib.sha256((app.ROOT / 'docs' / paths[0]).read_bytes()).hexdigest())
        self.assertEqual(len(set(digests)), 4)

    def test_html_input_is_displayed_as_text_and_cannot_execute(self):
        self.prepare_fonts()
        if not app.get_browser_executable():
            self.skipTest('Playwright Chromium must be installed for visual tests')
        item = self.item()
        item['slides'][0]['headline'] = '<script>throw new Error("unsafe")</script>'
        with patch.object(app, 'fetch_unsplash_image', return_value=None):
            paths = app.render_carousel_slides('2026-10-15_afternoon', 1, {'brand': '<Choicematrix>'}, item)
        self.assertEqual(len(paths), 6)

    def test_pillow_keeps_complete_text_in_all_themes(self):
        self.prepare_fonts()
        for mode in range(4):
            item = self.item()
            with patch.object(app, 'fetch_unsplash_image', return_value=None):
                paths = app.render_carousel_slides('2026-10-15_afternoon', mode+1,
                    {'brand': 'Choicematrix', 'renderer': 'pillow'}, item, forced_design_mode=mode)
            self.assertEqual(len(paths), 6)
            self.assertEqual(item['design_mode'], mode)

    def test_overflow_is_rejected_instead_of_truncating(self):
        self.prepare_fonts()
        item = self.item()
        item['slides'][0]['body'] = 'Unbounded content ' * 1000
        with patch.object(app, 'fetch_unsplash_image', return_value=None):
            with self.assertRaises(ValueError):
                app.render_carousel_slides('2026-10-15_afternoon', 1,
                    {'brand': 'Choicematrix', 'renderer': 'pillow'}, item)

    def test_script_runs_preserve_mixed_latin_indic_text(self):
        runs = app.split_script_runs('AI-এর প্রভাব')
        self.assertTrue(any('AI-' in text and not indic for text, indic in runs))
        self.assertTrue(any('এর' in text and indic for text, indic in runs))
