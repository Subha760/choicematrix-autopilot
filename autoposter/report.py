"""A private, offline operations report; no server or public draft export."""
from collections import Counter
from datetime import datetime, timezone
from html import escape
import json
from pathlib import Path


def write_report(app, destination):
    path = Path(destination).resolve()
    if path.exists():
        raise ValueError("Choose a new report filename")
    if path.is_relative_to((app.ROOT / "docs").resolve()):
        raise ValueError("Private reports cannot be saved in the public media directory")
    posts = app.read_json(app.STATE, {})
    counts = Counter(p.get("status", "unknown") for p in posts.values())
    rows = []
    for key, entry in sorted(posts.items(), reverse=True):
        platforms = "<br>".join(escape(line) for line in app.publication_summary(entry).splitlines()[1:])
        source = (entry.get("source") or {}).get("label", "Practical / owner topic")
        rows.append("<tr>" + "".join(f"<td>{value}</td>" for value in (
            escape(key), escape(entry.get("publish_time_ist", "")), escape(entry.get("language", "")),
            escape(entry.get("title", "")), escape(entry.get("status", "")), platforms, escape(source))) + "</tr>")
    with app.state_store().connect() as db:
        usage = db.execute("SELECT detail FROM events WHERE kind='ai_usage' ORDER BY id DESC LIMIT 100").fetchall()
        jobs = db.execute("SELECT id,action,status FROM jobs ORDER BY created_at DESC LIMIT 20").fetchall()
    providers = Counter(json.loads(row[0]).get("provider", "unknown") for row in usage)
    cards = "".join(f"<div><strong>{count}</strong><span>{escape(status)}</span></div>" for status, count in counts.items())
    job_rows = "".join("<tr>" + "".join("<td>" + escape(str(v)) + "</td>" for v in row) + "</tr>" for row in jobs)
    timestamp = datetime.now(timezone.utc).isoformat()
    document = f"""<!doctype html><html lang="en"><meta charset="utf-8">
    <meta name="viewport" content="width=device-width,initial-scale=1">
    <title>Choicematrix operations</title><style>
    body{{background:#101827;color:#edf2fa;font:15px/1.5 system-ui;margin:0;padding:32px}}
    h1{{font-size:30px;margin:0}}p{{color:#a8b8ce}}.cards{{display:flex;flex-wrap:wrap;gap:14px;margin:26px 0}}
    .cards div{{background:#1b2940;padding:18px 26px;border-radius:12px;min-width:130px}}
    strong{{display:block;font-size:30px;color:#79d7bd}}span{{color:#c0ccdd}}
    .scroll{{overflow:auto;background:#152135;border-radius:12px}}table{{border-collapse:collapse;width:100%;min-width:900px}}
    th,td{{padding:14px;text-align:left;border-bottom:1px solid #2a3a51;vertical-align:top}}th{{color:#79d7bd}}
    td:nth-child(4){{min-width:240px}}h2{{margin-top:36px}}code{{color:#c7dcff}}
    </style><h1>Choicematrix operations</h1><p>Private snapshot · generated {escape(timestamp)} · schedule in IST</p>
    <div class="cards">{cards}</div><h2>Content calendar and publication queue</h2>
    <div class="scroll"><table><thead><tr><th>Draft</th><th>Time</th><th>Language</th><th>Title</th><th>State</th><th>Platforms</th><th>Source</th></tr></thead>
    <tbody>{''.join(rows)}</tbody></table></div><h2>Recent command jobs</h2>
    <div class="scroll"><table><thead><tr><th>Job</th><th>Action</th><th>State</th></tr></thead><tbody>{job_rows}</tbody></table></div>
    <h2>Provider usage</h2><p>Calls among the latest 100 usage events: {escape(str(dict(providers)))}</p>
    <p>Review, edit and approve in the authenticated Telegram conversation. Uncertain results require account reconciliation.</p></html>"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(document, encoding="utf-8")
    path.chmod(0o600)
    return path
