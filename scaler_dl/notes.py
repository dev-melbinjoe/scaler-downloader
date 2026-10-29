"""Build the study document for one class folder: notes.html (interactive) and notes.md.

Everything is assembled offline from what Scaler provides (instructor bookmarks, revision notes,
flashcards, quizzes, shared links, lecture-notes PDF), the Whisper transcript and the slide
screenshots. Click any timestamp in notes.html and the embedded video jumps there.
"""

from __future__ import annotations

import html
import json
import re
from datetime import datetime
from pathlib import Path

from markdown_it import MarkdownIt

from . import textproc
from .frames import extract_slides
from .util import hms, human_duration, read_json

MD = MarkdownIt("commonmark", {"html": False}).enable("table").enable("strikethrough")
CITATION = re.compile(r"\s*【[^】]*】")  # leftovers from the AI that wrote Scaler's notes
URL = re.compile(r"https?://[^\s<>\"')\]]+")
MIN_CHAPTER = 45.0


# -- assembling ----------------------------------------------------------------------------------

def build_notes(d: Path, number: int | None = None) -> Path:
    info = read_json(d / "class.json")
    if number is not None:
        info["no"] = number  # numbers can be reassigned in the catalog after the class was downloaded
    tr = read_json(d / "transcript.json")
    segments = tr["segments"] if tr else []
    duration = float(info["recording"].get("duration") or (tr or {}).get("duration") or 0)
    has_video = (d / "video.mp4").exists()

    frames = read_json(d / "frames.json")
    if frames is None and has_video:
        print("  taking screenshots of each slide / board...", flush=True)
        frames = extract_slides(d / "video.mp4", d)
    frames = frames or []

    revision = ""
    if (d / "revision_notes.md").exists():
        revision = CITATION.sub("", (d / "revision_notes.md").read_text(encoding="utf-8")).strip()
    cards = read_json(d / "flashcards.json", [])
    vocabulary = course_vocabulary(info, revision, cards)

    sents = textproc.sentences(segments)
    chapters = make_chapters(info, duration, sents, frames, vocabulary)
    quizzes = number_quizzes(info)
    groups = [[s for s in sents if ch["start"] <= s["start"] < ch["end"]] for ch in chapters]
    for ch, group, top in zip(chapters, groups, textproc.highlights(groups) if sents else [[]] * len(chapters)):
        inside = lambda t: t is not None and ch["start"] <= t < ch["end"]  # noqa: E731
        ch["paragraphs"] = textproc.paragraphs(group)
        ch["highlights"] = top
        ch["frames"] = [f for f in frames if inside(f["start"])]
        ch["quizzes"] = [q for q in quizzes if inside(q["at"])]
        ch["doubts"] = [b for b in info["bookmarks"] if b.get("type") == "question" and inside(b["at"])]

    for c in cards:
        c["times"] = textproc.mentions(sents, c.get("card_title", ""))
    card_words = {w for c in cards for w in textproc.tokens(c.get("card_title", "").replace(".", " "))}
    terms = [t for t in textproc.key_terms(sents, vocabulary=vocabulary) if not set(t["term"].split()) <= card_words]

    doc = {
        "info": info,
        "duration": duration,
        "has_video": has_video,
        "segments": segments,
        "transcribed": bool(segments),
        "model": (tr or {}).get("model"),
        "chapters": chapters,
        "quizzes": quizzes,
        "revision": revision,
        "cards": cards,
        "terms": terms[:15],
        "questions": textproc.instructor_questions(sents),
        "doubts": [b for b in info["bookmarks"] if b.get("type") == "question"],
        "links": collect_links(info),
        "frames": frames,
    }
    (d / "notes.html").write_text(render_html(doc), encoding="utf-8")
    (d / "notes.md").write_text(render_md(doc), encoding="utf-8")
    print(f"  notes written: {d / 'notes.html'}", flush=True)
    return d / "notes.html"


GENERIC_TOPICS = set(
    """java python code coding program programming scaler lecture session module today notes revision class
    agenda quiz quizzes solution problem question example examples point points basic basics solve solving
    important key observation observations potential issues description continued attention ask learner
    learners live assignment assignments dashboard walkthrough resolution doubt doubts intermediate refresher
    introduction intro statements handling another find finding calculate calculation compare concept
    concepts conclusion summary setup snippet snippets practice practices practical illustration illustrations
    application applications identify identification discussion discussions communication pre tip tips
    overview approach explanation understanding use using working works need steps step case cases part non""".split()
)


def course_vocabulary(info: dict, revision: str, cards: list[dict]) -> set[str] | None:
    """The topics Scaler names for this class: headings, bold terms and code in its notes,
    flashcard titles, bookmarks. Keeps the topic index on topic instead of on verbal habits."""
    texts = re.findall(r"^#+\s*(.+)$|\*\*(.+?)\*\*|`([^`\n]+)`", revision, flags=re.M)
    texts = [part for match in texts for part in match if part]
    texts += [info.get("title", "")] + [c.get("card_title", "") for c in cards]
    texts += [b.get("title", "") for b in info.get("bookmarks", []) if b.get("type") != "question"]
    words = {w for t in texts for w in textproc.tokens(t)}
    words |= {part for w in list(words) for part in w.split(".") if len(part) > 2}  # "System.out.println" -> "println"
    words -= GENERIC_TOPICS
    return words if len(words) >= 10 else None  # too little to go on: fall back to phrases


def make_chapters(info: dict, duration: float, sents: list[dict], frames: list[dict], vocabulary: set[str] | None = None) -> list[dict]:
    marks = [
        (b["at"], " ".join(b["title"].split()))
        for b in info.get("bookmarks", [])
        if b.get("type") != "question" and b.get("title") and 0 <= b["at"] < duration - 30
    ]
    if marks:
        if marks[0][0] > 60:
            marks.insert(0, (0.0, "Before the lesson starts"))
        else:
            marks[0] = (0.0, marks[0][1])
    else:
        marks = auto_chapters(duration, sents, frames, vocabulary)

    # A bookmark followed seconds later by another ("Agenda" -> first topic) becomes one chapter.
    starts, pending = [], None
    for i, (t, title) in enumerate(marks):
        end = marks[i + 1][0] if i + 1 < len(marks) else duration
        if pending:
            t, title, pending = pending[0], f"{pending[1]} · {title}", None
        if end - t < MIN_CHAPTER and i + 1 < len(marks):
            pending = (t, title)
            continue
        starts.append((t, title))
    return [
        {"no": i + 1, "start": t, "end": starts[i + 1][0] if i + 1 < len(starts) else max(duration, t + 1), "title": title}
        for i, (t, title) in enumerate(starts)
    ]


def auto_chapters(duration: float, sents: list[dict], frames: list[dict], vocabulary: set[str] | None = None) -> list[tuple[float, str]]:
    """No instructor bookmarks: ~10 minute parts, cut at a slide change or pause, named by key terms."""
    n = max(1, round(duration / 600))
    cuts = sorted({f["start"] for f in frames} | {b["start"] for a, b in zip(sents, sents[1:]) if b["start"] - a["end"] > 4})
    starts = [0.0]
    for k in range(1, n):
        target = k * duration / n
        near = [c for c in cuts if abs(c - target) < 120 and c > starts[-1] + 120]
        starts.append(min(near, key=lambda c: abs(c - target)) if near else target)
    ends = starts[1:] + [duration]
    tops = [
        textproc.key_terms([s for s in sents if t <= s["start"] < end], limit=8, vocabulary=vocabulary)
        for t, end in zip(starts, ends)
    ]
    # Name each part by what is specific to it: a term that comes up in every part ("long" in a
    # data-types class) says little about any one of them.
    spread: dict[str, int] = {}
    for top in tops:
        for k in top:
            spread[k["term"]] = spread.get(k["term"], 0) + 1
    out = []
    for i, (t, top) in enumerate(zip(starts, tops)):
        best = sorted(top, key=lambda k: -k["count"] / spread[k["term"]])[:3]
        out.append((t, " · ".join(k["term"].title() for k in best) or f"Part {i + 1}"))
    return out


def number_quizzes(info: dict) -> list[dict]:
    """Attach each in-class quiz to the bookmark where its solution is explained."""
    explained = r"(?:solution|explanation|explained|answer|discussion)"
    solutions, numbered = {}, set()
    for b in info.get("bookmarks", []):
        m = re.search(rf"quiz\s*(\d+).*{explained}|{explained}.*quiz\s*(\d+)", b.get("title", ""), re.I)
        if m:
            solutions[int(m.group(1) or m.group(2))] = b["at"]
            numbered.add(b["at"])
    quizzes = []
    for i, q in enumerate(sorted(info.get("quizzes", []), key=lambda q: q["at"] or 0), 1):
        solution = solutions.get(i)
        if solution is None and q["at"] is not None:
            # An unnumbered "Solution" bookmark shortly after the quiz (never another quiz's one).
            later = [
                b["at"] for b in info.get("bookmarks", [])
                if b["at"] not in numbered and re.search(explained, b.get("title", ""), re.I) and 0 < b["at"] - q["at"] < 600
            ]
            solution = min(later) if later else None
        quizzes.append({**q, "no": i, "solution_at": solution})
    return quizzes


def collect_links(info: dict) -> list[dict]:
    links, seen = [], set()

    def add(url: str, title: str, source: str, at: float | None = None) -> None:
        url = url.rstrip(".,;")
        if url and url not in seen:
            seen.add(url)
            links.append({"url": url, "title": title.strip() or url, "source": source, "at": at})

    for n in info.get("lecture_notes", []):
        if n.get("file"):
            add(n["file"], f"Lecture notes: {n.get('filename') or 'PDF'}", "lecture notes")
    for i, extra in enumerate(info.get("other_recordings") or [], 2):
        add(f"video_part{i}.mp4", f"Recording part {i} ({human_duration(extra.get('duration') or 0)})", "lecture notes")
    for key, label in (("post_lecture", "after class"), ("reading", "reading"), ("pre_lecture", "before class")):
        for block in info.get(key, []):
            for link in block.get("links", []):
                # A bare URL as link text: describe it with the surrounding text instead ("WhatsApp group link").
                title = link["title"] if link["title"] != link["url"] else URL.sub("", block.get("text", "")).strip(" :-")[:120]
                add(link["url"], title, label)
    for n in sorted(info.get("notices", []), key=lambda n: n.get("at") or 0):
        text = " ".join(filter(None, [n.get("title"), n.get("description"), n.get("cta_link")]))
        for url in URL.findall(text):
            add(url, URL.sub("", text).strip(" :-") or url, "shared in class", n.get("at"))
    for m in info.get("instructor_messages", []):
        for url in URL.findall(m["text"]):
            add(url, URL.sub("", m["text"]).strip(" :-") or url, "instructor in chat", m.get("at"))
    return links


# -- HTML -----------------------------------------------------------------------------------------

def _e(text) -> str:
    return html.escape(str(text), quote=True)


def _ts(t: float | None, label: str | None = None) -> str:
    if t is None:
        return ""
    return f'<a class="ts" href="#" data-t="{t:.1f}" title="Play from here">{label or hms(t)}</a>'


def _linkify(text: str) -> str:
    return URL.sub(lambda m: f'<a href="{m.group()}" target="_blank" rel="noopener">{m.group()}</a>', _e(text))


def _demote(markdown: str, by: int = 2) -> str:
    """Scaler's notes start at '#'; nest them under our own section heading."""
    return re.sub(r"^(#{1,6})(?=\s)", lambda m: "#" * min(len(m.group(1)) + by, 6), markdown, flags=re.M)


def _href(url: str) -> str:
    return _e(url)


def render_html(doc: dict) -> str:
    info, chapters = doc["info"], doc["chapters"]
    date = (info.get("date") or "")[:10]
    pdfs = [link for link in doc["links"] if link["source"] == "lecture notes"]
    meta = " · ".join(filter(None, [_e(info.get("module", "")), date, human_duration(doc["duration"]), f"Class #{info.get('no')}"]))
    actions = [f'<a href="{_href(p["url"])}" target="_blank">📄 {_e(p["title"])}</a>' for p in pdfs]
    if doc["transcribed"]:
        actions.append('<a href="video.srt" download>Subtitles (.srt)</a>')
    if doc["has_video"]:
        actions.append('<a href="video.mp4" target="_blank">Open video file</a>')

    toc = "\n".join(
        f'<li data-start="{c["start"]:.1f}">{_ts(c["start"])} <a href="#ch{c["no"]}">{_e(c["title"])}</a></li>' for c in chapters
    )
    player = (
        '<video id="video" src="video.mp4" controls preload="metadata"></video>'
        '<div class="player-bar"><button id="cc" type="button" hidden>CC captions</button>'
        '<span class="hint">Click any timestamp to jump there</span></div>'
        if doc["has_video"]
        else '<p class="muted">video.mp4 is not in this folder (download it to watch alongside the notes).</p>'
    )

    sections: list[str] = []
    nav: list[tuple[str, str]] = []

    def section(anchor: str, title: str, body: str, note: str = "") -> None:
        nav.append((anchor, title))
        sub = f' <small class="muted">{note}</small>' if note else ""
        sections.append(f'<section id="{anchor}"><h2>{_e(title)}{sub}</h2>\n{body}\n</section>')

    if doc["revision"]:
        section("revision", "Revision notes", f'<div class="md">{MD.render(_demote(doc["revision"]))}</div>', "written by Scaler Companion")

    if doc["cards"]:
        cards = "\n".join(
            f'<div class="card"><div class="term">{_e(c.get("card_title", ""))}</div>'
            f'<div>{_e(c.get("card_content", ""))}</div>'
            + (f'<div class="where">discussed at {" ".join(_ts(t) for t in c["times"])}</div>' if c.get("times") else "")
            + "</div>"
            for c in doc["cards"]
        )
        section("flashcards", "Flashcards", f'<div class="cards">{cards}</div>', "from Scaler")

    walk = []
    for c in chapters:
        parts = [f'<h3>{_ts(c["start"])} {c["no"]}. {_e(c["title"])} <small class="muted">{human_duration(c["end"] - c["start"])}</small></h3>']
        if c["frames"]:
            shots = "".join(
                f'<figure><a href="{_e(f["file"])}" target="_blank"><img loading="lazy" src="{_e(f["file"])}" alt="screen at {hms(f["start"])}"></a>'
                f'<figcaption>{_ts(f["start"])}</figcaption></figure>'
                for f in c["frames"]
            )
            parts.append(f'<div class="shots">{shots}</div>')
        if c["highlights"]:
            items = "".join(f"<li>{_ts(s['start'])} {_e(s['text'])}</li>" for s in c["highlights"])
            parts.append(f'<div class="label">Key moments</div><ul class="highlights">{items}</ul>')
        for q in c["quizzes"]:
            parts.append(_quiz_html(q))
        for b in c["doubts"]:
            parts.append(f'<p class="doubt">❓ {_ts(b["at"])} {_e(b["title"])}</p>')
        if c["paragraphs"]:
            paras = "".join(f"<p>{_ts(p['start'])} {_e(p['text'])}</p>" for p in c["paragraphs"])
            words = sum(len(p["text"].split()) for p in c["paragraphs"])
            parts.append(f'<details class="transcript"><summary>Transcript ({words:,} words)</summary>{paras}</details>')
        walk.append(f'<article class="chapter" id="ch{c["no"]}">{"".join(parts)}</article>')
    note = "chapters are the instructor's bookmarks" if any(b.get("type") != "question" for b in info["bookmarks"]) else "automatic chapters"
    if not doc["transcribed"]:
        note += " · transcript not made yet (run `transcribe`)"
    section("walkthrough", "Class walkthrough", "\n".join(walk), note)

    if doc["quizzes"]:
        section("quizzes", "Practice: in-class quizzes", "\n".join(_quiz_html(q) for q in doc["quizzes"]), "answer first, then watch the solution")

    asks = []
    if doc["doubts"]:
        asks.append('<div class="label">Doubts students asked</div><ul>' + "".join(f"<li>{_ts(b['at'])} {_e(b['title'])}</li>" for b in doc["doubts"]) + "</ul>")
    if doc["questions"]:
        asks.append('<div class="label">Questions the instructor asked (try answering them)</div><ul>' + "".join(f"<li>{_ts(s['start'])} {_e(s['text'])}</li>" for s in doc["questions"]) + "</ul>")
    if asks:
        section("questions", "Questions", "\n".join(asks))

    if doc["terms"]:
        rows = "".join(f'<tr><td>{_e(t["term"])}</td><td>{" ".join(_ts(x) for x in t["times"])}</td></tr>' for t in doc["terms"])
        section("terms", "Topic index", f'<table class="terms"><tr><th>Topic</th><th>Where it comes up</th></tr>{rows}</table>', "click to jump to each discussion")

    if doc["links"]:
        items = "".join(
            f'<li><a href="{_href(link["url"])}" target="_blank" rel="noopener">{_e(link["title"])}</a>'
            f' <small class="muted">{_e(link["source"])}</small> {_ts(link["at"]) if link.get("at") is not None else ""}</li>'
            for link in doc["links"]
        )
        section("links", "Links & resources", f"<ul class=links>{items}</ul>")

    msgs = [m for m in info.get("instructor_messages", []) if not URL.search(m["text"]) and len(m["text"]) > 3]
    if msgs:
        section("chat", "Instructor's chat messages", "<ul>" + "".join(f"<li>{_ts(m['at'])} {_linkify(m['text'])}</li>" for m in msgs) + "</ul>")

    jump = " ".join(f'<a href="#{a}">{_e(t)}</a>' for a, t in nav)
    cues = json.dumps([[s["start"], s["end"], s["text"]] for s in doc["segments"]], ensure_ascii=False).replace("</", "<\\/")
    made = f'Made {datetime.now():%d %b %Y} by scaler_dl.' + (
        f' Transcript: Whisper "{_e(doc["model"])}" on this PC, so expect some misheard words.' if doc["model"] else ""
    )
    return TEMPLATE.format(
        title=_e(info["title"]), meta=meta, actions=" ".join(actions), player=player, toc=toc, jump=jump,
        sections="\n".join(sections), cues=cues, made=made, css=CSS, js=JS,
    )


BR = re.compile(r"<br\s*/?>", re.I)


CODE_HINT = re.compile(r"[;{}=]|System\.|\bprint|\bint\s|\breturn\b|^\s{2,}", re.M)


def _quiz_question_html(question: str) -> str:
    # Scaler writes quiz questions in Markdown ("```java ... ``` What's the output?"), as bare
    # code ("System.out.print(10);"), or as a plain sentence.
    if "```" in question:
        return f'<div class="md">{MD.render(question)}</div>'
    text = BR.sub(chr(10), question)
    if CODE_HINT.search(text):
        return f"<pre>{_e(text)}</pre>"
    return f'<p class="question">{_e(text)}</p>'


def _quiz_html(q: dict) -> str:
    choices = "".join(f'<li><code class="choice">{_e(BR.sub(chr(10), c))}</code></li>' for c in q["choices"] if c is not None)
    watch = f'{_ts(q["solution_at"], "▶ solution")}' if q.get("solution_at") is not None else ""
    return (
        f'<div class="quiz"><div class="label">Quiz {q["no"]} {_ts(q["at"])} {watch}</div>'
        f'{_quiz_question_html(q["question"])}<ol type="A">{choices}</ol></div>'
    )


# -- Markdown -------------------------------------------------------------------------------------

def render_md(doc: dict) -> str:
    info, out = doc["info"], []
    out.append(f"# {info['title']}\n")
    out.append(f"**{info.get('module', '')}** · {(info.get('date') or '')[:10]} · {human_duration(doc['duration'])} · Class #{info.get('no')}\n")
    files = ["[▶ video.mp4](video.mp4)"] if doc["has_video"] else []
    files += [f"[{link['title']}]({link['url'].replace(' ', '%20')})" for link in doc["links"] if link["source"] == "lecture notes"]
    if files:
        out.append(" · ".join(files) + "\n")
    out.append("## Contents\n")
    out += [f"- `{hms(c['start'])}` {c['title']}" for c in doc["chapters"]]
    out.append("")
    if doc["revision"]:
        out.append("## Revision notes *(by Scaler Companion)*\n")
        out.append(_demote(doc["revision"]) + "\n")
    if doc["cards"]:
        out.append("## Flashcards\n")
        out.append("| Term | Meaning | Discussed at |\n|---|---|---|")
        for c in doc["cards"]:
            cell = lambda s: str(s).replace("|", "\\|").replace("\n", " ")  # noqa: E731
            out.append(f"| **{cell(c.get('card_title', ''))}** | {cell(c.get('card_content', ''))} | {' '.join(f'`{hms(t)}`' for t in c.get('times', []))} |")
        out.append("")
    out.append("## Class walkthrough\n")
    for c in doc["chapters"]:
        out.append(f"### {c['no']}. {c['title']}  `{hms(c['start'])} – {hms(c['end'])}`\n")
        for f in c["frames"]:
            out.append(f"![screen at {hms(f['start'])}]({f['file']})")
        if c["frames"]:
            out.append("")
        if c["highlights"]:
            out.append("**Key moments**\n")
            out += [f"- `{hms(s['start'])}` {s['text']}" for s in c["highlights"]]
            out.append("")
        for q in c["quizzes"]:
            out.append(_quiz_md(q))
        for b in c["doubts"]:
            out.append(f"> ❓ `{hms(b['at'])}` {b['title']}\n")
        if c["paragraphs"]:
            out.append("<details><summary>Transcript</summary>\n")
            out += [f"`{hms(p['start'])}` {p['text']}\n" for p in c["paragraphs"]]
            out.append("</details>\n")
    if doc["quizzes"]:
        out.append("## Practice: in-class quizzes\n")
        out += [_quiz_md(q) for q in doc["quizzes"]]
    if doc["doubts"] or doc["questions"]:
        out.append("## Questions\n")
        if doc["doubts"]:
            out.append("**Doubts students asked**\n")
            out += [f"- `{hms(b['at'])}` {b['title']}" for b in doc["doubts"]]
            out.append("")
        if doc["questions"]:
            out.append("**Questions the instructor asked (try answering them)**\n")
            out += [f"- `{hms(s['start'])}` {s['text']}" for s in doc["questions"]]
            out.append("")
    if doc["terms"]:
        out.append("## Topic index\n")
        out += [f"- **{t['term']}**: " + " ".join(f"`{hms(x)}`" for x in t["times"]) for t in doc["terms"]]
        out.append("")
    if doc["links"]:
        out.append("## Links & resources\n")
        out += [
            f"- [{link['title']}]({link['url'].replace(' ', '%20')}) *({link['source']})*" + (f" `{hms(link['at'])}`" if link.get("at") is not None else "")
            for link in doc["links"]
        ]
        out.append("")
    return "\n".join(out) + "\n"


def _quiz_md(q: dict) -> str:
    choices = "\n".join(f"   {chr(65 + i)}. `{BR.sub(' ⏎ ', c)}`" for i, c in enumerate(q["choices"]) if c is not None)
    solution = f" · solution at `{hms(q['solution_at'])}`" if q.get("solution_at") is not None else ""
    question = q["question"] if "```" in q["question"] else f"```\n{BR.sub(chr(10), q['question'])}\n```"
    return f"**Quiz {q['no']}** `{hms(q['at'] or 0)}`{solution}\n\n{question}\n\n{choices}\n"


# -- page template ---------------------------------------------------------------------------------

CSS = """
:root{--bg:#f6f7f9;--card:#fff;--text:#1f2328;--muted:#667085;--line:#e4e7ec;--accent:#2563eb;--soft:#e8efff;--code:#f2f4f7;--warn:#fff7e6}
@media (prefers-color-scheme:dark){:root{--bg:#0e1117;--card:#161b22;--text:#e6edf3;--muted:#8b949e;--line:#30363d;--accent:#7aa7ff;--soft:#1b2a45;--code:#1f2630;--warn:#2d2410}}
*{box-sizing:border-box}
body{margin:0;font:16px/1.65 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;background:var(--bg);color:var(--text)}
a{color:var(--accent)}
header.top{padding:22px 28px 8px}
header.top h1{margin:0 0 4px;font-size:1.65rem;line-height:1.3}
a.back{font-size:.88rem;text-decoration:none;display:inline-block;margin-bottom:6px}
.meta,.muted{color:var(--muted)}
.actions{margin:10px 0 4px;display:flex;flex-wrap:wrap;gap:8px}
.actions a,.jump a{border:1px solid var(--line);background:var(--card);border-radius:999px;padding:3px 12px;text-decoration:none;font-size:.9rem}
.jump{display:flex;flex-wrap:wrap;gap:6px;margin:8px 0 0}
.layout{display:grid;grid-template-columns:minmax(360px,40%) 1fr;gap:22px;padding:12px 28px 40px;align-items:start}
aside{position:sticky;top:10px;max-height:calc(100vh - 20px);display:flex;flex-direction:column;gap:10px}
video{width:100%;border-radius:10px;background:#000;display:block}
.player-bar{display:flex;gap:10px;align-items:center;font-size:.85rem}
.player-bar button{border:1px solid var(--line);background:var(--card);color:var(--text);border-radius:6px;padding:2px 10px;cursor:pointer}
.player-bar button.on{background:var(--accent);color:#fff;border-color:var(--accent)}
.hint{color:var(--muted)}
.toc{overflow:auto;background:var(--card);border:1px solid var(--line);border-radius:10px;padding:6px 0;margin:0}
.toc h2{font-size:.85rem;text-transform:uppercase;letter-spacing:.05em;color:var(--muted);margin:6px 14px}
.toc ol{list-style:none;margin:0;padding:0}
.toc li{padding:4px 14px;font-size:.92rem;line-height:1.4}
.toc li a:not(.ts){text-decoration:none;color:var(--text)}
.toc li.current{background:var(--soft)}
main{min-width:0}
section,article.chapter{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px 22px;margin-bottom:16px}
section>h2{margin:0 0 10px;font-size:1.3rem}
article.chapter{margin:14px 0 0;padding:12px 16px;border-radius:10px}
article.chapter h3{margin:0 0 8px;font-size:1.08rem}
a.ts{font:600 12px/1.6 ui-monospace,Consolas,monospace;color:var(--accent);background:var(--soft);padding:1px 7px;border-radius:6px;text-decoration:none;white-space:nowrap;vertical-align:1px}
a.ts:hover{background:var(--accent);color:#fff}
.shots{display:grid;grid-template-columns:repeat(auto-fill,minmax(210px,1fr));gap:10px;margin:6px 0 10px}
.shots figure{margin:0}
.shots img{width:100%;display:block;border-radius:8px;border:1px solid var(--line)}
.shots figcaption{margin-top:2px}
.label{font-weight:600;font-size:.88rem;color:var(--muted);margin:8px 0 4px}
.highlights li{margin:3px 0}
.quiz{border-left:3px solid var(--accent);background:var(--soft);border-radius:8px;padding:6px 12px;margin:8px 0}
.quiz pre{margin:4px 0}.quiz .md p{margin:4px 0}code.choice{white-space:pre-wrap}
.doubt{background:var(--warn);border-radius:8px;padding:6px 10px}
details.transcript summary{cursor:pointer;color:var(--muted);font-size:.92rem}
details.transcript p{margin:6px 0;font-size:.95rem}
.md h3{font-size:1.15rem;margin:1.1em 0 .4em}.md h4{font-size:1.02rem;margin:1em 0 .3em}.md h5{font-size:.95rem}
pre,code{font-family:ui-monospace,Consolas,monospace;font-size:.9em;background:var(--code);border-radius:6px}
pre{padding:9px 12px;overflow:auto;white-space:pre-wrap}code{padding:1px 4px}pre code{padding:0;background:none}
.cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(250px,1fr));gap:10px}
.card{border:1px solid var(--line);border-radius:10px;padding:10px 12px;font-size:.95rem}
.card .term{font-weight:650;margin-bottom:2px}.card .where{margin-top:6px;font-size:.85rem;color:var(--muted)}
table.terms{border-collapse:collapse;width:100%}table.terms td,table.terms th{border-bottom:1px solid var(--line);padding:5px 8px;text-align:left;vertical-align:top}
ul.links li{margin:4px 0}
footer{color:var(--muted);font-size:.85rem;padding:0 28px 30px}
@media (max-width:1000px){.layout{grid-template-columns:1fr}aside{position:static;max-height:none}}
@media print{aside,.actions,.jump,footer{display:none}.layout{display:block;padding:0}body{background:#fff;color:#000}section,article.chapter{break-inside:avoid-page;border-color:#ccc}}
"""

JS = """
(() => {
  const v = document.getElementById('video');
  const cues = JSON.parse(document.getElementById('cues').textContent);
  const seek = t => {
    if (!v) return;
    v.currentTime = t;
    v.play().catch(() => {});
    if (innerWidth <= 1000) v.scrollIntoView({behavior: 'smooth', block: 'center'});
  };
  document.addEventListener('click', e => {
    const a = e.target.closest('a.ts');
    if (a) { e.preventDefault(); seek(+a.dataset.t); }
  });
  if (v && window.VTTCue && cues.length) {
    const track = v.addTextTrack('captions', 'English', 'en');
    for (const [s, e, t] of cues) track.addCue(new VTTCue(s, e, t));
    track.mode = 'hidden';
    const cc = document.getElementById('cc');
    cc.hidden = false;
    cc.onclick = () => {
      track.mode = track.mode === 'showing' ? 'hidden' : 'showing';
      cc.classList.toggle('on', track.mode === 'showing');
    };
  }
  const items = [...document.querySelectorAll('.toc li[data-start]')];
  if (v) {
    v.addEventListener('timeupdate', () => {
      let cur = null;
      for (const li of items) if (+li.dataset.start <= v.currentTime) cur = li;
      for (const li of items) li.classList.toggle('current', li === cur);
    });
    // Resume where you left off last time.
    const key = 'scaler-pos:' + location.pathname;
    const saved = +localStorage.getItem(key);
    if (saved > 5) v.addEventListener('loadedmetadata', () => { v.currentTime = saved; }, {once: true});
    setInterval(() => { if (!v.paused) localStorage.setItem(key, v.currentTime); }, 5000);
  }
  addEventListener('beforeprint', () => document.querySelectorAll('details').forEach(d => d.open = true));
})();
"""

TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>{css}</style>
</head>
<body>
<header class="top">
  <a class="back" href="../index.html">← All classes</a>
  <h1>{title}</h1>
  <div class="meta">{meta}</div>
  <div class="actions">{actions}</div>
  <nav class="jump">{jump}</nav>
</header>
<div class="layout">
  <aside>
    {player}
    <nav class="toc"><h2>Chapters</h2><ol>{toc}</ol></nav>
  </aside>
  <main>
{sections}
  </main>
</div>
<footer>{made}</footer>
<script type="application/json" id="cues">{cues}</script>
<script>{js}</script>
</body>
</html>
"""
