import json
from pathlib import Path
import autopilot as app

def main():
    settings = json.loads(Path("settings.json").read_text(encoding="utf-8"))
    today = "2026-10-18"
    slot = app.choose_topic(today)
    
    mock_post = {
        "title": "Protect Your Digital World",
        "subtitle": "Enable multi-factor authentication on every essential account.",
        "category": slot["category"],
    }
    
    img_path = app.image_art(today, 1, settings, mock_post)
    print(f"Sample card generated successfully at: docs/{img_path}")

if __name__ == "__main__":
    main()
