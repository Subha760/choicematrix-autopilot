"""Test script to verify RSS feed fetching and Gemini 2.5 Flash carousel generation."""
import os
import json
import urllib.request
import xml.etree.ElementTree as ET
import requests
from pathlib import Path

def load_env():
    env_file = Path(".env")
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())

def fetch_trending_topics():
    feeds = [
        ("Tech & Innovation", "https://news.google.com/rss/headlines/section/topic/TECHNOLOGY?hl=en-IN&gl=IN&ceid=IN:en"),
        ("AI & Productivity", "https://news.google.com/rss/search?q=AI+tools+OR+productivity+hacks+OR+smart+tech&hl=en-IN&gl=IN&ceid=IN:en"),
        ("Science & Discovery", "https://news.google.com/rss/headlines/section/topic/SCIENCE?hl=en-IN&gl=IN&ceid=IN:en"),
    ]
    topics = []
    for category, url in feeds:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            xml_data = urllib.request.urlopen(req, timeout=8).read()
            root = ET.fromstring(xml_data)
            for item in root.findall(".//item")[:6]:
                title = item.find("title").text if item.find("title") is not None else ""
                clean_title = title.rsplit(" - ", 1)[0] if " - " in title else title
                link = item.find("link").text if item.find("link") is not None else ""
                if clean_title and len(clean_title) > 10:
                    topics.append({"category": category, "headline": clean_title, "url": link})
        except Exception as e:
            print(f"Feed error for {category}: {e}")
    return topics

def generate_carousel_copy(topic, brand="Choicematrix"):
    load_env()
    key = os.getenv("GEMINI_API_KEY")
    prompt = f"""You are a top-tier social media content strategist for the educational and tech brand '{brand}'.
Based on this trending topic from today's web feeds:
Category: {topic['category']}
Headline: {topic['headline']}

Create an engaging, viral, high-value 6-SLIDE CAROUSEL post for Instagram, Facebook, and Threads.

Rules:
1. Slide 1 (Hook): Must have a punchy tag (e.g. 'HOT TECH BREAKTHROUGH', 'SMART HABIT'), a short giant title (3-6 words), and a 1-sentence hook.
2. Slides 2 to 5 (Content): Each slide must have a step number ('01', '02', '03', '04'), a clear headline, and 2-3 concise, easily scannable sentences (max 180 characters) explaining the concept clearly.
3. Slide 6 (Outro / CTA): Must have a header 'FOUND THIS VALUABLE? 💡', follow callout 'Follow @{brand.lower()} for more daily hot trends & smart tips! 🚀🔥✨', and save/share prompts.
4. Caption: Write a complete, friendly social media caption summarizing the post with 3-5 high-traffic hashtags including #{brand}.

Return ONLY a valid JSON object with this exact structure:
{{
  "title": "Short Main Title (max 45 chars)",
  "category": "{topic['category']}",
  "theme_tag": "TAG (e.g. HOT TECH / SMART HABIT)",
  "slides": [
    {{
      "slide_number": 1,
      "tag": "HOT TREND",
      "headline": "Bold 3-6 Word Title",
      "body": "One compelling sentence hook that makes people swipe.",
      "swipe_prompt": "Swipe to explore ➡️"
    }},
    {{
      "slide_number": 2,
      "tag": "STEP 01",
      "headline": "The Core Concept / Why It Matters",
      "body": "Clear, practical explanation in 2 short sentences."
    }},
    {{
      "slide_number": 3,
      "tag": "STEP 02",
      "headline": "Key Detail / Practical Action",
      "body": "Actionable takeaway or smart technique."
    }},
    {{
      "slide_number": 4,
      "tag": "STEP 03",
      "headline": "The Pro Tip / Counter-Intuitive Angle",
      "body": "Surprising detail or insider tip that adds major value."
    }},
    {{
      "slide_number": 5,
      "tag": "SUMMARY",
      "headline": "The Big Takeaway",
      "body": "Quick 1-sentence summary to remember and apply."
    }},
    {{
      "slide_number": 6,
      "tag": "CONNECT",
      "headline": "FOUND THIS HELPFUL? 💡",
      "body": "Follow @{brand.lower()} for more daily hot trends, tech & life hacks! 🚀🔥✨\\n\\n📌 Save this post for later\\n↗️ Share with a friend"
    }}
  ],
  "caption": "Full social caption with friendly explanation and 3-5 hashtags..."
}}
"""
    url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent"
    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"responseMimeType": "application/json", "temperature": 0.7},
    }
    r = requests.post(url, headers={"x-goog-api-key": key}, json=body, timeout=40)
    if r.status_code != 200:
        raise RuntimeError(f"Gemini API error {r.status_code}: {r.text}")
    res = r.json()
    text = res["candidates"][0]["content"]["parts"][0]["text"]
    return json.loads(text)

def get_font(size, bold=True):
    from PIL import ImageFont
    families = [
        "C:\\Windows\\Fonts\\arialbd.ttf" if bold else "C:\\Windows\\Fonts\\arial.ttf",
        "C:\\Windows\\Fonts\\segoeuib.ttf" if bold else "C:\\Windows\\Fonts\\segoeui.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for item in families:
        if Path(item).exists():
            return ImageFont.truetype(item, size)
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()

def wrap_text(draw, text, font, max_width):
    lines = []
    for paragraph in text.split("\n"):
        if not paragraph.strip():
            lines.append("")
            continue
        current_line = ""
        for word in paragraph.split():
            test_line = f"{current_line} {word}".strip()
            bbox = draw.textbbox((0, 0), test_line, font=font)
            if bbox[2] - bbox[0] <= max_width:
                current_line = test_line
            else:
                if current_line:
                    lines.append(current_line)
                current_line = word
        if current_line:
            lines.append(current_line)
    return lines

THEMES = [
    {
        "name": "Cyber Obsidian",
        "bg_top": (10, 15, 26),
        "bg_bot": (18, 28, 48),
        "card_bg": (22, 34, 58),
        "accent": (0, 242, 254),
        "accent_secondary": (255, 184, 0),
        "text_primary": (255, 255, 255),
        "text_secondary": (170, 185, 205),
        "border": (40, 60, 95)
    },
    {
        "name": "Clean Editorial",
        "bg_top": (248, 247, 244),
        "bg_bot": (242, 240, 235),
        "card_bg": (255, 255, 255),
        "accent": (225, 29, 72),
        "accent_secondary": (15, 23, 42),
        "text_primary": (15, 23, 42),
        "text_secondary": (71, 85, 105),
        "border": (226, 232, 240)
    },
    {
        "name": "Royal Sunset",
        "bg_top": (30, 27, 75),
        "bg_bot": (59, 7, 100),
        "card_bg": (48, 20, 95),
        "accent": (245, 158, 11),
        "accent_secondary": (251, 113, 133),
        "text_primary": (255, 255, 255),
        "text_secondary": (226, 232, 240),
        "border": (88, 28, 135)
    },
    {
        "name": "Emerald Horizon",
        "bg_top": (6, 35, 29),
        "bg_bot": (11, 59, 50),
        "card_bg": (14, 75, 64),
        "accent": (16, 185, 129),
        "accent_secondary": (167, 243, 208),
        "text_primary": (255, 255, 255),
        "text_secondary": (209, 250, 229),
        "border": (20, 90, 75)
    },
    {
        "name": "Swiss Monochrome",
        "bg_top": (10, 10, 12),
        "bg_bot": (18, 18, 22),
        "card_bg": (28, 28, 34),
        "accent": (250, 204, 21),
        "accent_secondary": (255, 255, 255),
        "text_primary": (255, 255, 255),
        "text_secondary": (161, 161, 170),
        "border": (45, 45, 55)
    },
    {
        "name": "Deep Cobalt",
        "bg_top": (11, 25, 44),
        "bg_bot": (30, 62, 98),
        "card_bg": (25, 50, 80),
        "accent": (56, 189, 248),
        "accent_secondary": (0, 173, 181),
        "text_primary": (255, 255, 255),
        "text_secondary": (224, 231, 255),
        "border": (45, 85, 135)
    }
]

def render_carousel_slide(slide_data, total_slides, theme, date_key, brand="Choicematrix"):
    from PIL import Image, ImageDraw
    W, H = 1080, 1350
    image = Image.new("RGB", (W, H))
    draw = ImageDraw.Draw(image)
    
    # 1. Background gradient
    for y in range(H):
        ratio = y / H
        c = tuple(int(theme["bg_top"][i]*(1-ratio) + theme["bg_bot"][i]*ratio) for i in range(3))
        draw.line((0, y, W, y), fill=c)
        
    slide_num = slide_data.get("slide_number", 1)
    tag = slide_data.get("tag", "HOT TOPIC").upper()
    headline = slide_data.get("headline", "")
    body = slide_data.get("body", "")
    
    # 2. Top Header Bar
    # Tag pill
    pill_w = 260
    draw.rounded_rectangle((70, 75, 70 + pill_w, 135), radius=16, fill=theme["accent"])
    draw.text((90, 92), tag[:22], font=get_font(22, bold=True), fill=theme["bg_top"])
    
    # Slide Counter
    counter_str = f"{slide_num:02d} / {total_slides:02d}"
    draw.text((W - 190, 92), counter_str, font=get_font(24, bold=True), fill=theme["accent_secondary"])
    
    # Top divider
    draw.line((70, 165, W - 70, 165), fill=theme["border"], width=2)
    
    # 3. Slide Content
    if slide_num == 1:
        # HOOK SLIDE
        draw.text((70, 230), "TODAY'S HOT BREAKDOWN", font=get_font(22, bold=True), fill=theme["accent"])
        
        # Big Headline
        f_title = get_font(56, bold=True)
        title_lines = wrap_text(draw, headline, f_title, 940)
        y = 280
        for line in title_lines[:4]:
            draw.text((70, y), line, font=f_title, fill=theme["text_primary"])
            y += 72
            
        y += 20
        draw.rounded_rectangle((70, y, 70 + 200, y + 8), radius=4, fill=theme["accent"])
        y += 50
        
        # Subtitle / Hook Card
        card_h = 320
        draw.rounded_rectangle((70, y, W - 70, y + card_h), radius=28, fill=theme["card_bg"], outline=theme["border"], width=3)
        f_body = get_font(34, bold=False)
        body_lines = wrap_text(draw, body, f_body, 860)
        by = y + 45
        for line in body_lines[:5]:
            draw.text((110, by), line, font=f_body, fill=theme["text_secondary"])
            by += 48
            
        # Swipe Callout
        swipe_y = H - 230
        draw.rounded_rectangle((70, swipe_y, W - 70, swipe_y + 90), radius=24, fill=theme["accent"])
        draw.text((110, swipe_y + 28), slide_data.get("swipe_prompt", "Swipe to explore ➡️").upper(), font=get_font(28, bold=True), fill=theme["bg_top"])
        
    elif slide_num == total_slides:
        # OUTRO / CTA SLIDE (User's special request!)
        draw.text((70, 230), "KEY TAKEAWAY COMPLETED", font=get_font(22, bold=True), fill=theme["accent"])
        
        f_title = get_font(54, bold=True)
        draw.text((70, 280), headline, font=f_title, fill=theme["text_primary"])
        
        # Channel Follow Box
        box_y = 390
        box_h = 420
        draw.rounded_rectangle((70, box_y, W - 70, box_y + box_h), radius=32, fill=theme["card_bg"], outline=theme["accent"], width=4)
        
        draw.text((110, box_y + 45), f"👉 FOLLOW @{brand.upper()}", font=get_font(38, bold=True), fill=theme["accent"])
        draw.text((110, box_y + 105), "For daily hot trends, tech breakdowns & smart tips! 🚀🔥✨", font=get_font(26, bold=False), fill=theme["text_secondary"])
        
        draw.line((110, box_y + 175, W - 110, box_y + 175), fill=theme["border"], width=2)
        
        draw.text((110, box_y + 215), "📌  Save this post to revisit anytime", font=get_font(28, bold=False), fill=theme["text_primary"])
        draw.text((110, box_y + 280), "💬  Share your thoughts in the comments", font=get_font(28, bold=False), fill=theme["text_primary"])
        draw.text((110, box_y + 345), "↗️  Send to someone who needs this!", font=get_font(28, bold=False), fill=theme["text_primary"])
        
    else:
        # CONTENT SLIDES (2 to 5)
        # Big Step Number
        step_str = f"STEP {slide_num - 1:02d}" if slide_num < total_slides - 1 else "SUMMARY"
        draw.text((70, 220), step_str, font=get_font(26, bold=True), fill=theme["accent"])
        
        # Headline
        f_title = get_font(50, bold=True)
        title_lines = wrap_text(draw, headline, f_title, 940)
        y = 275
        for line in title_lines[:3]:
            draw.text((70, y), line, font=f_title, fill=theme["text_primary"])
            y += 68
            
        y += 35
        # Content Box
        card_h = 440
        draw.rounded_rectangle((70, y, W - 70, y + card_h), radius=32, fill=theme["card_bg"], outline=theme["border"], width=3)
        
        f_body = get_font(35, bold=False)
        body_lines = wrap_text(draw, body, f_body, 860)
        by = y + 55
        for line in body_lines[:7]:
            draw.text((110, by), line, font=f_body, fill=theme["text_secondary"])
            by += 52
            
    # 4. Footer Brand Bar
    draw.line((70, H - 120, W - 70, H - 120), fill=theme["border"], width=2)
    draw.text((70, H - 90), f"@{brand.upper()}", font=get_font(24, bold=True), fill=theme["accent"])
    draw.text((W - 320, H - 90), "DAILY HOT TRENDS", font=get_font(22, bold=False), fill=theme["text_secondary"])
    
    return image

if __name__ == "__main__":
    topics = fetch_trending_topics()
    print(f"Fetched {len(topics)} topics. Picking top topic:")
    chosen = topics[0]
    print(json.dumps(chosen, indent=2))
    print("\nGenerating 6-slide carousel with Gemini 2.5 Flash...")
    data = generate_carousel_copy(chosen)
    
    out_dir = Path("docs/posts/test_carousel")
    out_dir.mkdir(parents=True, exist_ok=True)
    
    theme = THEMES[0] # Cyber Obsidian
    print(f"\nRendering 6 carousel slides with theme: {theme['name']}...")
    rendered_files = []
    for slide in data["slides"]:
        img = render_carousel_slide(slide, len(data["slides"]), theme, "2026-10-19")
        filename = out_dir / f"slide_{slide['slide_number']}.jpg"
        img.save(filename, "JPEG", quality=90, optimize=True)
        rendered_files.append(filename)
        print(f"  Rendered slide {slide['slide_number']}: {filename}")
        
    print("\nAll 6 slides successfully rendered!")

