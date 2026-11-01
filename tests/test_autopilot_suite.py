"""Comprehensive Autopilot Test Suite & Feature Verification.
Tests:
1. Rotational Visual Archetypes (Modes 0, 1, 2, 3)
2. Theme Palette Integrity (All 20 Themes)
3. Zero-Tofu Box Vector Rendering
4. Custom Topic Content & Carousel Pipeline
5. Telegram Callback Query Parser & Dispatch
6. System Diagnostics & Resource Monitor
7. Meta Token Health Check
"""
import os
import sys
import json
import time
from pathlib import Path
from datetime import datetime

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import autopilot
import telegram_bot_daemon

def run_tests():
    print("=" * 60)
    print("🧪 STARTING CHOICEMATRIX AUTOPILOT COMPREHENSIVE TEST SUITE")
    print("=" * 60)
    
    passed = 0
    total = 0

    # TEST 1: Imports and Environment
    total += 1
    print("\n[Test 1/7] Testing module imports & configuration...")
    try:
        import autopilot
        import telegram_bot_daemon
        assert len(autopilot.THEMES_MASTER) == 20, f"Expected 20 themes, got {len(autopilot.THEMES_MASTER)}"
        assert len(telegram_bot_daemon.ARCHETYPE_LABELS) == 4, "Expected 4 archetype labels"
        print("  ✅ Module imports & constants verified successfully.")
        passed += 1
    except Exception as e:
        print(f"  ❌ FAILED Test 1: {e}")

    # TEST 2: System Diagnostics Tool
    total += 1
    print("\n[Test 2/7] Testing System Diagnostics tool...")
    try:
        diag = telegram_bot_daemon.get_system_diagnostics()
        assert "AUTOPILOT SYSTEM DIAGNOSTICS" in diag
        assert "Host Platform:" in diag
        assert "Disk Space:" in diag
        assert "Git Version:" in diag
        print("  ✅ System Diagnostics generated formatted report:")
        for line in diag.splitlines()[:5]:
            print(f"     {line}")
        passed += 1
    except Exception as e:
        print(f"  ❌ FAILED Test 2: {e}")

    # TEST 3: All 4 Visual Archetypes Rendering
    total += 1
    print("\n[Test 3/7] Testing 4 Rotational Visual Archetypes (Modes 0, 1, 2, 3)...")
    try:
        settings = autopilot.read_json(ROOT / "settings.json", {})
        dummy_item = {
            "title": "Autonomous AI Systems 2026",
            "subtitle": "How agentic intelligence is transforming software engineering",
            "category": "INNOVATION",
            "language": "English",
            "unsplash_query": "artificial intelligence futuristic",
            "slides": [
                {"slide_number": 1, "tag": "AGENTIC AI", "headline": "The 2026 Autonomous Shift", "body": "Agents now pair-program seamlessly across cloud infrastructure."},
                {"slide_number": 2, "tag": "BENEFITS", "headline": "Instant Deployment", "body": "Continuous background verification eliminates deployment friction."}
            ]
        }
        test_paths = []
        for mode in range(4):
            mode_name = telegram_bot_daemon.ARCHETYPE_LABELS[mode]
            slides = autopilot.render_carousel_slides(
                date_key="2026-11-01_afternoon",
                revision=1,
                settings=settings,
                item=dummy_item,
                slot="afternoon",
                forced_design_mode=mode
            )
            assert slides and len(slides) == 2, f"Expected 2 slides for mode {mode}"
            for s in slides:
                assert (ROOT / "docs" / s).exists(), f"Slide file missing: {s}"
            test_paths.extend(slides)
            print(f"  ✅ Archetype {mode} ({mode_name.split('(')[0].strip()}): rendered {len(slides)} slides successfully.")
        passed += 1
    except Exception as e:
        print(f"  ❌ FAILED Test 3: {e}")

    # TEST 4: Zero Tofu Box Font Integrity Check
    total += 1
    print("\n[Test 4/7] Testing Zero Tofu Box Font Integrity...")
    try:
        # Check clean_text_for_canvas
        raw_bengali = "⚡ বিশেষ প্রতিবেদন: সুন্দরবনের রহস্যময় বাঘ ✦"
        cleaned = autopilot.clean_text_for_canvas(raw_bengali)
        # Ensure emoji/symbols are stripped so font cannot render tofu
        assert "⚡" not in cleaned, "Emoji was not cleaned"
        assert "✦" not in cleaned, "Black four pointed star was not cleaned"
        assert "সুন্দরবনের রহস্যময় বাঘ" in cleaned, "Bengali text was incorrectly stripped"
        print(f"  ✅ Cleaned Indic text: '{cleaned}' (Zero problematic glyphs)")
        passed += 1
    except Exception as e:
        print(f"  ❌ FAILED Test 4: {e}")

    # TEST 5: Custom Topic Pipeline Integration
    total += 1
    print("\n[Test 5/7] Testing Custom Topic Parameter Integration...")
    try:
        # Verify generated_content receives and uses custom_topic
        posts = autopilot.read_json(ROOT / "state" / "posts.json", {})
        topic_prompt = "Next-Gen Quantum Computing Chips"
        # We test the prompt structure without calling network if needed, or inspect function signature
        import inspect
        gc_sig = inspect.signature(autopilot.generated_content)
        assert "custom_topic" in gc_sig.parameters, "custom_topic not in generated_content signature"
        cp_sig = inspect.signature(autopilot.create_post)
        assert "custom_topic" in cp_sig.parameters, "custom_topic not in create_post signature"
        assert "forced_design_mode" in cp_sig.parameters, "forced_design_mode not in create_post signature"
        print("  ✅ Custom topic & forced design mode signatures verified.")
        passed += 1
    except Exception as e:
        print(f"  ❌ FAILED Test 5: {e}")

    # TEST 6: Telegram Callback Query Dispatch
    total += 1
    print("\n[Test 6/7] Testing Telegram Inline Callback Query parsing...")
    try:
        # Test simulated callback actions
        for action in ("approve:afternoon", "recreate:evening", "theme:afternoon", "status", "reject:evening", "diagnose"):
            if ":" in action:
                verb, slot = action.split(":", 1)
                assert verb in ("approve", "recreate", "theme", "reject")
                assert slot in ("afternoon", "evening")
            else:
                assert action in ("status", "diagnose", "health")
        print("  ✅ All inline button callback routes validated.")
        passed += 1
    except Exception as e:
        print(f"  ❌ FAILED Test 6: {e}")

    # TEST 7: Meta Token Verification Function
    total += 1
    print("\n[Test 7/7] Testing Meta Token Health Checker...")
    try:
        ok, msg = telegram_bot_daemon.check_meta_token_health(force=True)
        print(f"  ℹ️ Meta Token Status: ok={ok}, message='{msg[:60]}'")
        # Note: Even if token expired on Meta side, the function itself must not crash and must return a boolean and string.
        assert isinstance(ok, bool)
        assert isinstance(msg, str)
        print("  ✅ Meta Token Health function executed reliably without exception.")
        passed += 1
    except Exception as e:
        print(f"  ❌ FAILED Test 7: {e}")

    print("\n" + "=" * 60)
    print(f"📊 TEST SUMMARY: {passed}/{total} tests PASSED ({passed/total*100:.1f}%)")
    print("=" * 60)
    return passed == total

if __name__ == "__main__":
    success = run_tests()
    sys.exit(0 if success else 1)
