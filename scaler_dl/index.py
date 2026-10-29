"""The library home page: every item of every module, in the same order as the Scaler dashboard.

Rendered two ways from the same code:
- live (served by `python main.py`): with buttons that refresh the list and download/transcribe
  classes, plus a progress panel;
- static (library/index.html, opened as a file): the same list and notes links, no buttons.
"""

from __future__ import annotations

import html
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

from .config import BASE_URL
from .library import LIBRARY_DIR, class_dir, in_display_order, is_class, load_catalog, tag
from .util import human_duration, read_json

MODULE_URL = BASE_URL + "/academy/mentee-dashboard/core-curriculum/m/{}/classes"
CLASS_URL = BASE_URL + "/academy/mentee-dashboard/class/{}"


def build_index() -> Path:
    """Write the static library/index.html (used when the library is opened without main.py)."""
    out = LIBRARY_DIR / "index.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_index(), encoding="utf-8")
    return out


def _when(iso: str | None) -> str:
    if not iso:
        return ""
    t = datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone()  # your local time, like the dashboard
    return f"{t.day} {t:%b %Y}"


def _row(c: dict, now: str) -> tuple[str, bool, bool]:
    """HTML for one item, plus (is a recorded class, has notes)."""
    e = html.escape
    label = tag(c)
    day = f"Day {c['day_number']}" if c.get("day_number") else ""
    scaler_link = f'<a class="ext" href="{CLASS_URL.format(c["class_id"])}" target="_blank" rel="noopener" title="Open on Scaler">↗</a>'
    title = e(c["title"])
    sub, chips, action = [], "", ""
    recorded = notes = False
    classes = ["row"]

    if is_class(c) and c.get("folder"):
        d = class_dir(c)
        have = {name: (d / file).exists() for name, file in (("video", "video.mp4"), ("transcript", "transcript.json"), ("notes", "notes.html"))}
        notes, recorded = have["notes"], bool(c.get("has_recording"))
        info = read_json(d / "class.json") or {}
        dur = (info.get("recording") or {}).get("duration")
        if c.get("no"):
            sub.append(f"#{c['no']}")
        if dur:
            sub.append(human_duration(dur))
        chips = "".join(f'<span class="chip{" on" if ok else ""}">{name}</span>' for name, ok in have.items())
        href = f'{quote(c["folder"])}/notes.html'
        if notes:
            title = f'<a href="{href}">{title}</a>'
            action = f'<a class="btn" href="{href}">Open notes</a>'
            classes.append("done")
        elif recorded:
            what = "Transcribe &amp; make notes" if have["video"] else "Download &amp; make notes"
            action = f'<button class="btn primary live-only" data-act="process" data-id="{c["class_id"]}">{what}</button>'
            action += f'<code class="offline-only">main.py run {c["no"]}</code>'
        elif (c.get("date") or "9") > now:
            action, classes = '<span class="muted">Upcoming</span>', classes + ["later"]
        else:
            action = '<span class="muted">Recording not out yet</span>'
    else:
        classes.append("other")
        if c.get("kind") == "test":
            action = '<span class="muted">Test, no recording</span>'
        if (c.get("date") or "9") > now:
            classes.append("later")

    tag_html = f' <span class="tag {e(label)}">{e(label)}</span>' if label else ""
    row = (
        f'<tr class="{" ".join(classes)}" data-id="{c["class_id"]}" data-title="{e(c["title"].lower())}">'
        f'<td class="day">{day}<small>{_when(c.get("date"))}</small></td>'
        f'<td class="title">{title}{tag_html}<div class="sub">{" · ".join(sub)}</div></td>'
        f'<td class="chips">{chips}</td>'
        f'<td class="act"><span class="state"></span>{action} {scaler_link}</td></tr>'
    )
    return row, recorded, notes


def render_index(token: str = "") -> str:
    catalog = load_catalog()
    now = datetime.now(timezone.utc).isoformat()
    by_module: dict[int, list[dict]] = {}
    for c in in_display_order(catalog):
        by_module.setdefault(c.get("module_id"), []).append(c)

    sections, nav = [], []
    recorded_total = notes_total = upcoming = 0
    for m in catalog.get("modules", []):
        items = by_module.get(m["id"])
        if not items:
            continue
        rows, rec, done = [], 0, 0
        for c in items:
            row, recorded, notes = _row(c, now)
            rows.append(row)
            rec += recorded
            done += notes
            upcoming += is_class(c) and (c.get("date") or "9") > now
        recorded_total += rec
        notes_total += done
        state = {"completed": "", "locked": " · not started yet"}.get(m.get("status"), f" · {m.get('status')}")
        nav.append(f'<a href="#m{m["id"]}">{html.escape(m["name"])}</a>')
        sections.append(
            f'<section class="module" id="m{m["id"]}"><h2>{html.escape(m["name"])}'
            f' <small class="muted">notes {done}/{rec}{state}</small>'
            f' <a class="ext" href="{MODULE_URL.format(m["id"])}" target="_blank" rel="noopener">open on Scaler ↗</a></h2>'
            f'<table>{COLUMNS}<tbody>{"".join(rows)}</tbody></table></section>'
        )

    synced = (catalog.get("synced_at") or "")[:16].replace("T", " ")
    config = json.dumps({"token": token}).replace("</", "<\\/")
    return PAGE.format(
        summary=f"{notes_total} of {recorded_total} recorded classes have notes · {upcoming} upcoming · list refreshed {synced} UTC",
        nav="".join(nav), sections="".join(sections), config=config, css=CSS, js=JS,
    )


COLUMNS = '<colgroup><col style="width:7.5em"><col><col style="width:14em"><col style="width:15.5em"></colgroup>'

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>My Scaler classes</title><style>{css}</style></head>
<body>
<header>
  <h1>My Scaler classes</h1>
  <p class="muted">{summary}</p>
  <div class="toolbar live-only">
    <button class="btn" data-act="sync" title="Fetch the latest class list from Scaler">↻ Refresh class list</button>
    <button class="btn" data-act="update" title="Refresh the list, then download and make notes for the newest 3 classes waiting">⬇ Get new recordings</button>
    <button class="btn quiet" data-act="login" title="Opens Chrome so you can log in to Scaler again">Log in to Scaler</button>
  </div>
  <div id="panel" class="panel live-only" hidden></div>
  <p class="offline-only note">Opened as a file: notes links work, but the buttons need the app running.
    Start it with <code>python main.py</code> in the project folder (it opens this page with buttons).</p>
  <div class="find"><input id="q" type="search" placeholder="Search classes…" autocomplete="off"></div>
  <nav class="modules">{nav}</nav>
</header>
<main>{sections}</main>
<script id="config" type="application/json">{config}</script>
<script>{js}</script>
</body></html>
"""

CSS = """
:root{--bg:#f6f7f9;--card:#fff;--text:#1f2328;--muted:#667085;--line:#e4e7ec;--accent:#2563eb;--soft:#e8efff;--warn:#fff4d6;--warnt:#8a5a00;--ok:#e7f6ec;--okt:#1a7f37;--bad:#fdecec;--badt:#b42318}
@media (prefers-color-scheme:dark){:root{--bg:#0e1117;--card:#161b22;--text:#e6edf3;--muted:#8b949e;--line:#30363d;--accent:#7aa7ff;--soft:#1b2a45;--warn:#3a2d0c;--warnt:#f2c46d;--ok:#12321d;--okt:#6fd28a;--bad:#3b1414;--badt:#ff8f8f}}
*{box-sizing:border-box}
body{margin:0 auto;max-width:1180px;padding:20px 24px 60px;font:15px/1.5 system-ui,"Segoe UI",sans-serif;background:var(--bg);color:var(--text)}
a{color:var(--accent)} h1{margin:0} .muted{color:var(--muted)} code{font-family:ui-monospace,Consolas,monospace;font-size:.85em}
header{margin-bottom:8px}
.toolbar{display:flex;gap:8px;flex-wrap:wrap;margin:12px 0 8px}
.btn{font:inherit;font-size:.88rem;border:1px solid var(--line);background:var(--card);color:var(--text);border-radius:8px;padding:4px 12px;cursor:pointer;text-decoration:none;white-space:nowrap;display:inline-block}
.btn:hover{border-color:var(--accent)} .btn.primary{background:var(--accent);border-color:var(--accent);color:#fff}
.btn.quiet{color:var(--muted)} .btn:disabled{opacity:.5;cursor:default}
.panel{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:10px 14px;margin:8px 0;position:sticky;top:8px;z-index:5;box-shadow:0 2px 10px rgba(0,0,0,.06)}
.panel .now{font-weight:600} .panel .line{font-family:ui-monospace,Consolas,monospace;font-size:.85rem;color:var(--muted);white-space:pre-wrap;margin:2px 0}
.panel .result{border-radius:8px;padding:4px 10px;margin:6px 0 0}
.panel .result.ok{background:var(--ok);color:var(--okt)} .panel .result.bad{background:var(--bad);color:var(--badt)}
.panel details pre{max-height:240px;overflow:auto;font-size:.8rem;background:var(--bg);padding:8px;border-radius:6px;margin:6px 0 0}
.panel .row-top{display:flex;justify-content:space-between;gap:10px;align-items:center}
.note{background:var(--warn);color:var(--warnt);border-radius:8px;padding:6px 12px}
.find{margin:10px 0 4px} #q{width:100%;max-width:420px;font:inherit;padding:5px 10px;border:1px solid var(--line);border-radius:8px;background:var(--card);color:var(--text)}
nav.modules{display:flex;flex-wrap:wrap;gap:6px;margin:8px 0 0}
nav.modules a{font-size:.8rem;border:1px solid var(--line);border-radius:999px;padding:1px 10px;text-decoration:none;background:var(--card)}
section.module h2{font-size:1.08rem;margin:26px 0 8px} section.module h2 small{font-weight:400;font-size:.8rem}
a.ext{font-size:.78rem;font-weight:400;text-decoration:none;margin-left:6px}
table{width:100%;table-layout:fixed;border-collapse:collapse;background:var(--card);border:1px solid var(--line);border-radius:10px;overflow:hidden}
td{padding:6px 10px;border-bottom:1px solid var(--line);vertical-align:middle}
td.day{color:var(--muted);font-size:.85rem;line-height:1.25} td.day small{display:block;font-size:.8rem}
td.title .sub{font-size:.78rem;color:var(--muted)}
td.chips{white-space:nowrap} td.act{text-align:right;white-space:nowrap}
.chip{font-size:.72rem;border:1px solid var(--line);border-radius:999px;padding:0 7px;margin-right:3px;color:var(--muted)}
.chip.on{background:var(--soft);color:var(--accent);border-color:transparent}
.tag{font-size:.7rem;border-radius:4px;padding:0 6px;margin-left:6px;background:var(--warn);color:var(--warnt);vertical-align:1px}
.tag.test{background:var(--soft);color:var(--accent)}
tr.other td.title,tr.later td.title{color:var(--muted)}
tr.busy{background:var(--soft)} .state{font-size:.8rem;color:var(--accent);margin-right:6px}
body:not(.live) .live-only{display:none!important} body.live .offline-only{display:none!important}
tr.hidden,section.hidden{display:none}
"""

JS = r"""
(() => {
  const token = JSON.parse(document.getElementById('config').textContent).token;
  const live = !!token && location.protocol.startsWith('http');
  if (live) document.body.classList.add('live');
  const panel = document.getElementById('panel');
  const esc = s => String(s).replace(/[&<>"]/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]));

  // Search: keeps its text across the automatic reloads.
  const q = document.getElementById('q');
  q.value = sessionStorage.getItem('q') || '';
  const filter = () => {
    const words = q.value.toLowerCase().trim();
    sessionStorage.setItem('q', q.value);
    document.querySelectorAll('section.module').forEach(sec => {
      let shown = 0;
      sec.querySelectorAll('tr.row').forEach(tr => {
        const hit = !words || tr.dataset.title.includes(words);
        tr.classList.toggle('hidden', !hit);
        shown += hit;
      });
      sec.classList.toggle('hidden', !shown);
    });
  };
  q.addEventListener('input', filter);
  filter();
  if (!live) return;

  const post = async (action, body) => {
    try {
      const r = await fetch('/api/' + action, {method: 'POST', headers: {'Content-Type': 'application/json', 'X-Token': token}, body: JSON.stringify(body || {})});
      const j = await r.json();
      if (!r.ok) alert(j.error || 'That did not work.');
    } catch (e) { alert('The app is not running. Start it with: python main.py'); }
    poll();
  };
  document.addEventListener('click', e => {
    const b = e.target.closest('[data-act]');
    if (!b) return;
    e.preventDefault();
    post(b.dataset.act, b.dataset.id ? {class_id: +b.dataset.id} : {});
  });

  let version = null, offline = false;
  const render = s => {
    const cur = s.current, queued = s.queue || [];
    const busy = new Map(queued.map((j, i) => [j.class_id, 'queued ' + (i + 1)]));
    if (cur && cur.class_id) busy.set(cur.class_id, 'working…');
    document.querySelectorAll('tr.row').forEach(tr => {
      const st = busy.get(+tr.dataset.id);
      tr.classList.toggle('busy', !!st);
      tr.querySelector('.state').textContent = st || '';
      const btn = tr.querySelector('button[data-act]');
      if (btn) btn.disabled = !!st;
    });
    let h = '';
    if (cur) {
      h += `<div class="row-top"><div class="now">⏳ ${esc(cur.title)}</div><button class="btn" data-act="stop">■ Stop</button></div>`;
      h += `<div class="line">${esc(s.status || 'starting…')}</div>`;
      if (queued.length) h += `<div class="muted">Next: ${queued.map(j => esc(j.title)).join(' · ')}</div>`;
    }
    if (s.last && !cur) {
      const ok = s.last.result === 'done';
      h += `<div class="result ${ok ? 'ok' : 'bad'}">${ok ? '✓' : '⚠'} ${esc(s.last.message)}`;
      if (s.last.result === 'login') h += ` <button class="btn" data-act="login">Log in to Scaler</button>`;
      h += '</div>';
    }
    if (s.log && s.log.length) h += `<details><summary class="muted">Activity log</summary><pre>${esc(s.log.join('\n'))}</pre></details>`;
    const open = panel.querySelector('details')?.open;
    panel.innerHTML = h;
    if (open) panel.querySelector('details').open = true;
    panel.hidden = !h;
  };
  const poll = async () => {
    try {
      const s = await (await fetch('/api/state', {cache: 'no-store'})).json();
      if (offline) { offline = false; }
      if (version !== null && s.version !== version) { location.reload(); return; }
      version = s.version;
      render(s);
    } catch (e) {
      offline = true;
      panel.hidden = false;
      panel.innerHTML = '<div class="result bad">⚠ The app stopped. Start it again with: python main.py</div>';
    }
  };
  poll();
  setInterval(poll, 2000);
})();
"""
