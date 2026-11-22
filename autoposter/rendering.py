"""Shared four-theme rendering with complete text, escaped HTML and no invented data."""
import base64
from html import escape
from io import BytesIO
from pathlib import Path
from PIL import Image, ImageDraw


def browser_executable():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as playwright:
        path = Path(playwright.chromium.executable_path)
        if path.is_file():
            return str(path)
    for path in ("/usr/bin/chromium", "/usr/bin/google-chrome",
                 r"C:\Program Files\Google\Chrome\Application\chrome.exe"):
        if Path(path).is_file():
            return str(path)
    return None


def layout(app, date_key, revision, settings, item, slot, forced_design_mode):
    seed = int(date_key.split("_")[0].replace("-", ""))
    mode = int(forced_design_mode) % 4 if forced_design_mode is not None else (seed + revision + (slot == "evening")) % 4
    theme = app.THEMES_MASTER[(seed + revision * 3 + (7 if slot == "evening" else 0)) % len(app.THEMES_MASTER)]
    item.update(design_mode=mode, theme_name=theme["name"])
    return mode, theme


def hexcolor(value):
    return "#" + "".join(f"{c:02x}" for c in value[:3])


def slides_for(item, brand):
    return item.get("slides") or [
        {"slide_number": 1, "tag": item.get("category", "IDEAS"), "headline": item["title"], "body": item["subtitle"]},
        {"slide_number": 2, "tag": "CONNECT", "headline": "Follow " + brand, "body": "Save this post and share your thoughts."}]


def chrome(app, date_key, revision, settings, item, slot, browser_exe=None, forced_design_mode=None):
    from playwright.sync_api import sync_playwright
    mode, theme = layout(app, date_key, revision, settings, item, slot, forced_design_mode)
    language = item.get("language", "English")
    font_name = {"Hindi": "NotoSansDevanagari", "Bengali": "NotoSansBengali"}.get(language)
    font_css = ""
    if font_name:
        for weight, suffix in ((400, "Regular"), (700, "Bold")):
            data = base64.b64encode((app.ROOT / "fonts" / f"{font_name}-{suffix}.ttf").read_bytes()).decode()
            font_css += f"@font-face{{font-family:Indic;src:url(data:font/ttf;base64,{data});font-weight:{weight};}}"
    slides = slides_for(item, settings["brand"])
    paths = []
    used = set()
    app.IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        options = {"headless": True}
        if browser_exe:
            options["executable_path"] = browser_exe
        browser = playwright.chromium.launch(**options)
        try:
            context = browser.new_context(viewport={"width": 1080, "height": 1350}, device_scale_factor=1)
            context.route("**/*", lambda route: route.abort())
            page = context.new_page()
            for slide in slides:
                photo = app.fetch_unsplash_image(slide.get("image_query") or item.get("unsplash_query", ""),
                                                 900, 340, used_ids=used, orientation="landscape")
                photo_html = ""
                if photo:
                    if photo.info.get("credit"):
                        item.setdefault("photo_credits", []).append(photo.info["credit"])
                    stream = BytesIO()
                    photo.save(stream, format="JPEG", quality=88)
                    photo_html = '<img alt="Illustrative stock photo" src="data:image/jpeg;base64,' + base64.b64encode(stream.getvalue()).decode() + '">'
                headline = escape(app.clean_text_for_canvas(slide["headline"]))
                body = escape(app.clean_text_for_canvas(slide["body"])).replace("\n", "<br>")
                badge = slide.get("visual_badge") or {}
                badge_html = (f'<aside>{escape(badge.get("label", ""))}: {escape(badge.get("value", ""))}</aside>'
                              if badge.get("type") == "badge" else "")
                radius = (0, 28, 44, 18)[mode]
                border = ("solid", "double", "solid", "dashed")[mode]
                tag = escape(app.clean_text_for_canvas(slide.get("tag", "")))
                counter = f'{slide["slide_number"]} / {len(slides)}'
                document = f"""<!doctype html><html><head><meta charset="utf-8"><style>
                {font_css} *{{box-sizing:border-box}} body{{margin:0;width:1080px;height:1350px;padding:44px;
                background:{hexcolor(theme['bg_top'])};color:{hexcolor(theme['text_primary'])};
                font-family:Indic,Arial,sans-serif;}}
                main{{height:1262px;border:5px {border} {hexcolor(theme['accent'])};border-radius:{radius}px;
                padding:36px;display:flex;flex-direction:column;gap:24px;overflow:hidden}}
                header,footer{{display:flex;justify-content:space-between;gap:20px;color:{hexcolor(theme['accent'])};
                font-size:23px;flex-shrink:0;overflow-wrap:anywhere}}
                h1{{flex-shrink:0;font-size:54px;line-height:1.35;margin:0;overflow-wrap:anywhere}}
                img{{width:100%;height:340px;object-fit:cover;border-radius:18px;flex-shrink:0}}
                #copy{{flex-shrink:0;font-size:32px;line-height:1.6;overflow-wrap:anywhere;min-height:0}}
                aside{{font-size:24px;color:{hexcolor(theme['accent_secondary'])};overflow-wrap:anywhere}}
                footer{{margin-top:auto;border-top:1px solid {hexcolor(theme['accent'])};padding-top:18px}}
                </style></head><body><main><header><span>{tag}</span><span>{counter}</span></header>
                <h1>{headline}</h1>{photo_html}<div id="copy">{body}</div>{badge_html}
                <footer><span>@{escape(settings['brand'])}</span><span>Illustrative imagery</span></footer>
                </main></body></html>"""
                page.set_content(document, wait_until="load")
                page.evaluate("() => document.fonts.ready")
                fitted = page.evaluate("""() => {
                    const main = document.querySelector('main'), title = document.querySelector('h1'), copy = document.querySelector('#copy');
                    for(let i=0; i<20; i++){
                        const footer = document.querySelector('footer');
                        const bounds = main.getBoundingClientRect();
                        const ok = [...main.children].every(el => el.scrollWidth <= el.clientWidth + 1)
                            && footer.getBoundingClientRect().bottom <= bounds.bottom - 25;
                        if(ok) return true;
                        const size=parseFloat(getComputedStyle(copy).fontSize);
                        if(size<=23) return false;
                        copy.style.fontSize=(size-0.6)+'px';
                        title.style.fontSize=(parseFloat(getComputedStyle(title).fontSize)-0.8)+'px';
                    } return false;
                }""")
                if not fitted:
                    raise ValueError("Slide text does not fit; shorten the draft")
                dest = app.IMAGE_DIR / f"{date_key}-v{revision}-slide{slide['slide_number']}.jpg"
                page.screenshot(path=str(dest), type="jpeg", quality=92)
                paths.append(dest.relative_to(app.ROOT / "docs").as_posix())
        finally:
            browser.close()
    return paths


def fit_lines(app, draw, text, width, height, size, language, bold=False):
    if len(text) > 1000:
        raise ValueError("Slide text exceeds the render limit")
    for chosen in range(size, 21, -1):
        lines = app.wrap_mixed_by_pixels(draw, text, chosen, width, bold=bold, language=language)
        line_height = int(chosen * 1.65)
        if len(lines) * line_height <= height and all(
                app.get_mixed_text_width(draw, line, chosen, bold=bold, language=language) <= width for line in lines):
            return lines, chosen, line_height
    raise ValueError("Slide text does not fit; shorten the draft")


def pillow(app, date_key, revision, settings, item, slot, forced_design_mode=None):
    mode, theme = layout(app, date_key, revision, settings, item, slot, forced_design_mode)
    language = item.get("language", "English")
    slides = slides_for(item, settings["brand"])
    paths, used = [], set()
    app.IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    for slide in slides:
        image = Image.new("RGB", (1080, 1350), theme["bg_top"])
        draw = ImageDraw.Draw(image)
        radius = (0, 28, 44, 18)[mode]
        draw.rounded_rectangle((40, 40, 1040, 1310), radius=radius, outline=theme["accent"], width=4)
        if mode == 1:
            draw.rounded_rectangle((50, 50, 1030, 1300), radius=20, outline=theme["accent"], width=2)
        elif mode == 3:
            for x in (65, 1015):
                draw.ellipse((x-6, 65-6, x+6, 65+6), fill=theme["accent"])
        app.draw_mixed_text(draw, (80, 85), app.clean_text_for_canvas(slide.get("tag", "")),
                            23, language=language, fill=theme["accent"])
        app.draw_mixed_text(draw, (900, 85), f'{slide["slide_number"]}/{len(slides)}',
                            23, language=language, fill=theme["accent"])
        headline = app.clean_text_for_canvas(slide["headline"])
        lines, size, step = fit_lines(app, draw, headline, 920, 190, 46, language, True)
        y = 155
        for line in lines:
            app.draw_mixed_text(draw, (80, y), line, size, language=language, fill=theme["text_primary"])
            y += step
        photo = app.fetch_unsplash_image(slide.get("image_query") or item.get("unsplash_query", ""),
                                         920, 340, used_ids=used, orientation="landscape")
        if photo:
            if photo.info.get("credit"):
                item.setdefault("photo_credits", []).append(photo.info["credit"])
            image.paste(app.smart_card_fit(photo, 920, 340), (80, 365))
        y = 745 if photo else 380
        body = app.clean_text_for_canvas(slide["body"])
        lines, size, step = fit_lines(app, draw, body, 920, 1190-y, 30, language)
        for line in lines:
            app.draw_mixed_text(draw, (80, y), line, size, bold=False, language=language, fill=theme["text_secondary"])
            y += step
        app.draw_mixed_text(draw, (80, 1240), "@" + settings["brand"] + " | Illustrative imagery",
                            21, language=language, fill=theme["accent"])
        dest = app.IMAGE_DIR / f"{date_key}-v{revision}-slide{slide['slide_number']}.jpg"
        image.save(dest, "JPEG", quality=92, optimize=True)
        paths.append(dest.relative_to(app.ROOT / "docs").as_posix())
    return paths
