"""Bounded, attributed H.264/AAC slideshow rendering for reviewed social videos."""
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from urllib.parse import urlparse, unquote
from PIL import Image, ImageDraw

MAX_AUDIO_BYTES = 32 * 1024 * 1024
MAX_VIDEO_BYTES = 48 * 1024 * 1024


def tools_available():
    return bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


def probe(path):
    result = subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
                            check=True, capture_output=True, timeout=30, text=True)
    return json.loads(result.stdout)


def allowed_audio_url(url):
    if not isinstance(url, str) or len(url) > 400 or any(ord(c) < 32 for c in url):
        return False
    parsed = urlparse(url)
    prefix = "/music/royalty-free/mp3-royaltyfree/"
    path = unquote(parsed.path)
    return bool(parsed.scheme == "https" and parsed.netloc == "incompetech.com"
                and not parsed.query and not parsed.fragment and path.startswith(prefix)
                and re.fullmatch(r"[A-Za-z0-9 _()-]+\.mp3", path[len(prefix):]))


def audio_file(app, track):
    url = track.get("audio_url", "")
    if not allowed_audio_url(url):
        raise ValueError("Automatic music downloads must use the curated composer MP3 endpoint")
    if not re.fullmatch(r"[a-z0-9-]{1,40}", track.get("id", "")):
        raise ValueError("Invalid music track identifier")
    directory = app.STATE.parent / "music_cache"
    directory.mkdir(parents=True, exist_ok=True)
    directory.chmod(0o700)
    # URL changes never reuse an unrelated cached file under the same track ID.
    path = directory / (track["id"] + "-" + hashlib.sha256(url.encode()).hexdigest()[:12] + ".mp3")
    if not path.exists():
        temporary = path.with_suffix(".tmp")
        try:
            with app.requests.get(url, stream=True, timeout=(10, 30), allow_redirects=False) as response:
                if response.status_code != 200:
                    raise RuntimeError("Music download rejected; no silent soundtrack substitution")
                length = 0
                with temporary.open("wb") as stream:
                    for chunk in response.iter_content(65536):
                        length += len(chunk)
                        if length > MAX_AUDIO_BYTES:
                            raise ValueError("Music download exceeds the cache limit")
                        stream.write(chunk)
                if not length:
                    raise ValueError("Music download was empty")
            temporary.chmod(0o600)
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
    if path.stat().st_size > MAX_AUDIO_BYTES:
        raise ValueError("Cached music exceeds the size limit")
    if not any(stream.get("codec_type") == "audio" for stream in probe(path).get("streams", [])):
        raise ValueError("The music file has no decodable audio stream")
    return path


def local_manifest(root, entry):
    video = entry.get("video") or {}
    path = (Path(root) / "docs" / video["path"]).resolve(strict=True)
    if not path.is_relative_to((Path(root) / "docs/posts").resolve()) or path.suffix != ".mp4":
        raise ValueError("Video is outside the post media directory")
    if not 0 < path.stat().st_size <= MAX_VIDEO_BYTES:
        raise ValueError("Invalid video size")
    return {"path": video["path"], "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def credit_image(app, track, destination, width, height):
    image = Image.new("RGB", (width, height), (16, 24, 39))
    draw = ImageDraw.Draw(image)
    lines = ["MUSIC CREDITS", track["title"], track["artist"], "incompetech.com",
             track["license"], track["license_url"], "Excerpt with fades; paired with original slides."]
    size = max(12, width // 27)
    font = app.font(size, bold=False)
    rendered = []
    for text in lines:
        line = ""
        for character in text:
            if draw.textlength(line + character, font=font) > width - width // 8 and line:
                rendered.append(line.rstrip())
                line = character.lstrip()
            else:
                line += character
        rendered.append(line)
    y = max(20, (height - len(rendered) * size * 2) // 2)
    for line in rendered:
        draw.text(((width - draw.textlength(line, font=font)) / 2, y), line, fill=(237, 242, 250), font=font)
        y += size * 2
    image.save(destination, "JPEG", quality=92)


def render(app, entry, settings, *, audio_path=None, dimensions=(1080, 1920), slide_seconds=None):
    """Original carousel art is retained and padded, never cropped into portrait."""
    from .review import local_manifest as image_manifest
    if not tools_available():
        raise RuntimeError("Install FFmpeg and FFprobe before enabling music video mode")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}(?:_(?:afternoon|evening))?", entry["date"]):
        raise ValueError("Invalid video post identifier")
    images = image_manifest(app.ROOT, entry)
    width, height = dimensions
    seconds = slide_seconds if slide_seconds is not None else settings.get("music", {}).get("slide_seconds", 7)
    if not isinstance(seconds, (int, float)) or isinstance(seconds, bool) or not 0.25 <= seconds <= 15:
        raise ValueError("Invalid slide duration")
    track = (entry.get("music") or {}).get("track")
    audio = Path(audio_path) if audio_path is not None else (audio_file(app, track) if track else None)
    if audio is not None and not track:
        raise ValueError("Embedded audio requires its track and attribution metadata")
    credit_seconds = 6 if track else 0
    duration = len(images) * seconds + credit_seconds
    app.IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    app.STATE.parent.mkdir(parents=True, exist_ok=True)
    destination = app.IMAGE_DIR / f"{entry['date']}-v{int(entry['revision'])}-music.mp4"
    with tempfile.TemporaryDirectory(prefix="video-", dir=app.STATE.parent) as temporary:
        directory = Path(temporary)
        frames = [(app.ROOT / "docs" / image["path"], seconds) for image in images]
        if track:
            credits = directory / "credits.jpg"
            credit_image(app, track, credits, width, height)
            frames.append((credits, credit_seconds))
        concat = directory / "frames.txt"
        lines = []
        for path, frame_duration in frames + [(frames[-1][0], 0)]:
            normalized = path.resolve().as_posix()
            if any(c in normalized for c in "'\n\r"):
                raise ValueError("Frame paths cannot contain quotes or line breaks")
            lines.append(f"file '{normalized}'")
            if frame_duration:
                lines.append(f"duration {frame_duration}")
        concat.write_text("\n".join(lines) + "\n", encoding="utf-8")
        output = directory / "finished.mp4"
        command = ["ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(concat)]
        if audio:
            command += ["-stream_loop", "-1", "-i", str(audio)]
        command += ["-map", "0:v:0"]
        if audio:
            command += ["-map", "1:a:0"]
        command += ["-vf", f"scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=0x101827,setsar=1,fps=25",
                    "-c:v", "libx264", "-preset", "veryfast", "-threads", "1", "-crf", "24", "-pix_fmt", "yuv420p", "-t", str(duration)]
        if audio:
            command += ["-af", f"volume=0.35,afade=t=in:st=0:d=0.75,afade=t=out:st={max(0, duration - 2)}:d=2",
                        "-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-ac", "2"]
        else:
            command += ["-an"]
        command += ["-movflags", "+faststart", str(output)]
        subprocess.run(command, check=True, capture_output=True, timeout=600)
        metadata = probe(output)
        streams = metadata.get("streams", [])
        video = next((s for s in streams if s.get("codec_type") == "video"), {})
        if video.get("codec_name") != "h264" or video.get("width") != width or video.get("height") != height:
            raise ValueError("Rendered video failed codec/dimension verification")
        if audio and not any(s.get("codec_name") == "aac" for s in streams):
            raise ValueError("Rendered video is missing its AAC soundtrack")
        if abs(float(metadata["format"]["duration"]) - duration) > 0.5 or output.stat().st_size > MAX_VIDEO_BYTES:
            raise ValueError("Rendered video failed duration or size verification")
        output.replace(destination)
    return {"path": destination.relative_to(app.ROOT / "docs").as_posix(),
            "sha256": hashlib.sha256(destination.read_bytes()).hexdigest(), "duration_seconds": duration,
            "width": width, "height": height, "audio_sha256": hashlib.sha256(audio.read_bytes()).hexdigest() if audio else None,
            "attribution_embedded": bool(track)}
