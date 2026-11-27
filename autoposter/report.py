"""A private, offline operations report; no server or public draft export."""
from collections import Counter
from datetime import datetime, timezone
from html import escape
import json
from pathlib import Path


def usage_summary(events):
    """Aggregate reported tokens; failed calls without usage are never estimates."""
    result = {}
    for row in events:
        try:
            detail = json.loads(row[0])
        except (ValueError, TypeError):
            continue
        if not isinstance(detail, dict):
            continue
        provider = str(detail.get("provider", "unknown"))
        total = result.setdefault(provider, {"calls": 0, "successes": 0, "failures": 0,
                                             "input_tokens": 0, "output_tokens": 0, "reported_calls": 0,
                                             "input_tokens_reports": 0, "output_tokens_reports": 0})
        total["calls"] += 1
        total["successes"] += detail.get("ok") is True
        total["failures"] += detail.get("ok") is False
        usage = detail.get("usage")
        if not isinstance(usage, dict):
            continue
        fields = {"input_tokens": ("promptTokenCount", "prompt_tokens"),
                  "output_tokens": ("candidatesTokenCount", "completion_tokens")}
        reported = False
        for field, names in fields.items():
            value = next((usage[name] for name in names if name in usage), None)
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                total[field] += value
                total[field + "_reports"] += 1
                reported = True
        total["reported_calls"] += reported
    return result


def write_report(app, destination):
    path = Path(destination).resolve()
    if path.exists():
        raise ValueError("Choose a new report filename")
    if path.is_relative_to((app.ROOT / "docs").resolve()):
        raise ValueError("Private reports cannot be saved in the public media directory")
    posts = app.read_json(app.STATE, {})
    counts = Counter(p.get("status", "unknown") for p in posts.values())
    languages = sorted({p.get("language", "unknown") for p in posts.values()})
    rows = []
    for key, entry in sorted(posts.items(), reverse=True):
        platforms = "<br>".join(escape(line) for line in app.publication_summary(entry).splitlines()[1:])
        source = (entry.get("source") or {}).get("label", "Practical / owner topic")
        music = entry.get("music") or {}
        music_label = ((music.get("track") or {}).get("title", "No music") +
                       (" (embedded)" if music.get("attachment_status") == "embedded" else " (suggestion)")) if music.get("track") else "No music"
        attention = (entry.get("status") in ("needs_attention", "preview_failed") or any(
            entry.get(p, {}).get("status") in ("ambiguous", "attempting", "error", "retry_pending")
            for p in ("fb", "ig", "threads")))
        attrs = (f'data-state="{escape(entry.get("status", "unknown"))}" '
                 f'data-language="{escape(entry.get("language", "unknown"))}" '
                 f'data-attention="{str(attention).lower()}"')
        rows.append(f"<tr {attrs}>" + "".join(f"<td>{value}</td>" for value in (
            escape(key), escape(entry.get("publish_time_ist", "")), escape(entry.get("language", "")),
            escape(entry.get("title", "")), escape(entry.get("status", "")), platforms, escape(source), escape(music_label))) + "</tr>")
    with app.state_store().connect() as db:
        usage = db.execute("SELECT detail FROM events WHERE kind='ai_usage' ORDER BY id DESC LIMIT 100").fetchall()
        jobs = db.execute("SELECT id,action,status FROM jobs ORDER BY created_at DESC LIMIT 20").fetchall()
    providers = usage_summary(usage)
    provider_rows = "".join("<tr>" + "".join(f"<td>{escape(str(value))}</td>" for value in (
        provider, values["calls"], values["successes"], values["failures"],
        values["input_tokens"] if values["input_tokens_reports"] else "Not reported",
        values["output_tokens"] if values["output_tokens_reports"] else "Not reported",
        values["reported_calls"])) + "</tr>" for provider, values in sorted(providers.items()))
    state_options = "".join(f'<option value="{escape(state)}">{escape(state)}</option>' for state in sorted(counts))
    language_options = "".join(f'<option value="{escape(language)}">{escape(language)}</option>' for language in languages)
    cards = "".join(f"<div><strong>{count}</strong><span>{escape(status)}</span></div>" for status, count in counts.items())
    job_rows = "".join("<tr>" + "".join("<td>" + escape(str(v)) + "</td>" for v in row) + "</tr>" for row in jobs)
    timestamp = datetime.now(timezone.utc).isoformat()
    document = f"""<!doctype html><html lang="en"><meta charset="utf-8">
    <meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; base-uri 'none'; form-action 'none'">
    <meta name="viewport" content="width=device-width,initial-scale=1">
    <title>Choicematrix operations</title><style>
    body{{background:#101827;color:#edf2fa;font:15px/1.5 system-ui;margin:0;padding:32px}}
    h1{{font-size:30px;margin:0}}p{{color:#a8b8ce}}.cards{{display:flex;flex-wrap:wrap;gap:14px;margin:26px 0}}
    .cards div{{background:#1b2940;padding:18px 26px;border-radius:12px;min-width:130px}}
    strong{{display:block;font-size:30px;color:#79d7bd}}span{{color:#c0ccdd}}
    .scroll{{overflow:auto;background:#152135;border-radius:12px}}table{{border-collapse:collapse;width:100%;min-width:900px}}
    th,td{{padding:14px;text-align:left;border-bottom:1px solid #2a3a51;vertical-align:top}}th{{color:#79d7bd}}
    td:nth-child(4){{min-width:240px}}h2{{margin-top:36px}}code{{color:#c7dcff}}
    .filters{{display:flex;flex-wrap:wrap;gap:14px;align-items:end;margin:18px 0}}
    label{{display:flex;flex-direction:column;gap:6px;color:#c0ccdd}}
    input,select,button{{font:inherit;color:#edf2fa;background:#1b2940;border:1px solid #53657e;border-radius:6px;padding:10px}}
    input{{min-width:220px}}button{{cursor:pointer}}:focus-visible{{outline:3px solid #79d7bd;outline-offset:3px}}
    tr[hidden]{{display:none}}#empty{{padding:20px}}@media(max-width:600px){{body{{padding:18px}}}}
    </style><h1>Choicematrix operations</h1><p>Private snapshot · generated {escape(timestamp)} · schedule in IST</p>
    <div class="cards">{cards}</div><h2>Content calendar and publication queue</h2>
    <div class="filters"><label>Search drafts<input id="search" type="search" placeholder="Title, date, platform or source"></label>
    <label>State<select id="state"><option value="">All states</option><option value="attention">Needs attention</option>{state_options}</select></label>
    <label>Language<select id="language"><option value="">All languages</option>{language_options}</select></label>
    <button id="reset" type="button">Clear filters</button></div>
    <p id="result-count" role="status" aria-live="polite">Showing {len(rows)} of {len(rows)} drafts</p>
    <div class="scroll"><table><thead><tr><th>Draft</th><th>Time</th><th>Language</th><th>Title</th><th>State</th><th>Platforms</th><th>Source</th><th>Music</th></tr></thead>
    <tbody id="drafts">{''.join(rows)}</tbody></table><p id="empty" hidden>No drafts match these filters.</p></div><h2>Recent command jobs</h2>
    <div class="scroll"><table><thead><tr><th>Job</th><th>Action</th><th>State</th></tr></thead><tbody>{job_rows}</tbody></table></div>
    <h2>Provider usage</h2><p>Latest {len(usage)} usage events, up to 100. Tokens include only usage reported by the provider; these are not billing totals.</p>
    <div class="scroll"><table><thead><tr><th>Provider</th><th>Calls</th><th>Succeeded</th><th>Failed</th><th>Input tokens</th><th>Output tokens</th><th>Calls with token usage</th></tr></thead>
    <tbody>{provider_rows}</tbody></table></div>
    <p>Review, edit and approve in the authenticated Telegram conversation. Uncertain results require account reconciliation. This is a private offline snapshot; it does not update automatically.</p>
    <script>
    const search = document.getElementById('search'), state = document.getElementById('state'), language = document.getElementById('language');
    const rows = [...document.querySelectorAll('#drafts tr')];
    function filterDrafts() {{
      const query = search.value.trim().toLocaleLowerCase(); let visible = 0;
      for (const row of rows) {{
        const stateMatches = !state.value || (state.value === 'attention' ? row.dataset.attention === 'true' : row.dataset.state === state.value);
        row.hidden = !(stateMatches && (!language.value || row.dataset.language === language.value) && row.textContent.toLocaleLowerCase().includes(query));
        if (!row.hidden) visible++;
      }}
      document.getElementById('result-count').textContent = `Showing ${{visible}} of ${{rows.length}} drafts`;
      document.getElementById('empty').hidden = visible !== 0;
    }}
    search.addEventListener('input', filterDrafts); state.addEventListener('change', filterDrafts); language.addEventListener('change', filterDrafts);
    document.getElementById('reset').addEventListener('click', () => {{search.value = ''; state.value = ''; language.value = ''; filterDrafts(); search.focus();}});
    </script></html>"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(document, encoding="utf-8")
    path.chmod(0o600)
    return path
