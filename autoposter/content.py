"""Complete content validation and bounded primary/fallback generation."""
import copy
import json
import re
import time
import unicodedata
import requests


def numbers(text):
    text = "".join(str(unicodedata.digit(c)) if c.isdecimal() else c for c in text)
    return set(re.findall(r"\d+(?:[.,]\d+)?", text))


def validate_slides(item, slot):
    slides = item.get("slides")
    if slides is None:
        return item
    if not isinstance(slides, list) or len(slides) != 6:
        raise RuntimeError("A carousel requires exactly six slides")
    evidence = slot.get("seed") or (slot.get("source") or {}).get("evidence", "")  # Explicit curated text only; a headline is not article verification.
    all_text = []
    for index, slide in enumerate(slides, 1):
        if not isinstance(slide, dict) or slide.get("slide_number") != index:
            raise RuntimeError("Slides must be numbered sequentially")
        for field, limit in (("headline", 100), ("body", 650), ("tag", 40)):
            value = slide.get(field)
            if not isinstance(value, str) or not value.strip() or len(value) > limit:
                raise RuntimeError(f"Invalid slide {index} {field}")
            all_text.append(value)
        if len(slide["body"].split()) > 85:
            raise RuntimeError("Slide body is too dense")
        badge = slide.get("visual_badge")
        if badge:
            if not isinstance(badge, dict) or badge.get("type") not in ("badge", "chart"):
                raise RuntimeError("Invalid slide badge")
            if badge.get("type") == "chart":
                raise RuntimeError("Charts require a separate verified dataset; use a text badge")
            for field in ("label", "value"):
                if not isinstance(badge.get(field), str) or len(badge[field]) > 100:
                    raise RuntimeError("Invalid badge text")
                all_text.append(badge[field])
        for field in ("image_query", "swipe_prompt"):
            if field in slide and (not isinstance(slide[field], str) or len(slide[field]) > 160):
                raise RuntimeError(f"Invalid slide {field}")
    text = " ".join(all_text)
    if numbers(text.lower()) - numbers(evidence.lower()):
        raise RuntimeError("Slide metrics are absent from the supplied evidence")
    if re.search(r"guaranteed|proven cure|100%|breaking news|new study|scientists say", text, re.I):
        raise RuntimeError("Unsupported claim in slide content")
    language = slot.get("language", "English")
    script = {"Hindi": r"[\u0900-\u097f]", "Bengali": r"[\u0980-\u09ff]"}.get(language)
    if script:
        for slide in slides:
            text = slide["headline"] + " " + slide["body"]
            if len(re.findall(script, text)) < max(2, len(re.findall(r"[^\W\d_]", text)) // 3):
                raise RuntimeError(f"Slide is not predominantly {language}")
    return item


def generate(prompt, slot, getenv, validator, record):
    """Only validated candidates can win. Fallback avoids billing all providers each time."""
    providers = [x.strip() for x in (getenv("AI_PROVIDER_ORDER") or "gemini,groq").split(",")]
    failures = []
    for provider in providers:
        key = getenv("GEMINI_API_KEY" if provider == "gemini" else "GROQ_API_KEY")
        if provider not in ("gemini", "groq") or not key:
            continue
        model = getenv("GEMINI_MODEL" if provider == "gemini" else "GROQ_MODEL") or (
            "gemini-3.5-flash-lite" if provider == "gemini" else "openai/gpt-oss-120b")
        if not re.fullmatch(r"[A-Za-z0-9_./-]+", model):
            raise ValueError("Invalid model identifier")
        for attempt in range(2):
            start = time.monotonic()
            try:
                if provider == "gemini":
                    response = requests.post(
                        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                        headers={"x-goog-api-key": key},
                        json={"contents": [{"parts": [{"text": prompt}]}],
                              "generationConfig": {"responseMimeType": "application/json", "temperature": 0.55}},
                        timeout=(10, 45))
                    if response.status_code != 200:
                        raise RuntimeError(f"Provider HTTP {response.status_code}")
                    body = response.json()
                    raw = "".join(p.get("text", "") for p in body["candidates"][0]["content"]["parts"])
                    usage = body.get("usageMetadata", {})
                else:
                    response = requests.post("https://api.groq.com/openai/v1/chat/completions",
                        headers={"Authorization": f"Bearer {key}"},
                        json={"model": model, "messages": [
                            {"role": "system", "content": "Return valid JSON. Use only supplied evidence. Never invent facts or metrics."},
                            {"role": "user", "content": prompt}],
                            "response_format": {"type": "json_object"}, "temperature": 0.55}, timeout=(10, 45))
                    if response.status_code != 200:
                        raise RuntimeError(f"Provider HTTP {response.status_code}")
                    body = response.json()
                    raw = body["choices"][0]["message"]["content"]
                    usage = body.get("usage", {})
                item = json.loads(raw)
                if not isinstance(item, dict) or not isinstance(item.get("slides"), list) or len(item["slides"]) != 6:
                    raise RuntimeError("Provider must return six complete slides")
                item = validator(copy.deepcopy(item))
                record("ai_usage", {"provider": provider, "model": model, "usage": usage,
                                    "seconds": round(time.monotonic() - start, 2), "ok": True})
                return item
            except (ValueError, KeyError, IndexError, TypeError, RuntimeError, requests.RequestException):
                failures.append(provider)
                record("ai_usage", {"provider": provider, "model": model, "ok": False})
                if attempt == 0:
                    time.sleep(1)
    raise RuntimeError("No configured provider returned valid content")
