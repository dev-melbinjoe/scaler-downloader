"""Your local class library: catalog of classes, one folder per class, and what's been done to each."""

from __future__ import annotations

import json
import mimetypes
import os
import re
import urllib.request
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlsplit

from .config import BASE_URL, ROOT
from .downloader import USER_AGENT, download_recording
from .scaler import Scaler
from .util import read_json, safe_filename, write_json

LIBRARY_DIR = Path(os.environ.get("SCALER_LIBRARY", ROOT / "library"))
CATALOG = LIBRARY_DIR / "catalog.json"
# v2 also lists bonus sessions; the first v2 sync renumbers once so numbers follow class dates.
CATALOG_VERSION = 2


class NoRecording(RuntimeError):
    pass


# -- catalog ----------------------------------------------------------------------------------

def load_catalog() -> dict:
    return read_json(CATALOG, {"classes": [], "modules": []})


def in_display_order(catalog: dict, classes: list[dict] | None = None) -> list[dict]:
    """As on the dashboard: modules in course order, each module's items by date."""
    order = {m["id"]: (m.get("order") or 0, i) for i, m in enumerate(catalog.get("modules", []))}
    return sorted(
        catalog["classes"] if classes is None else classes,
        key=lambda c: (order.get(c.get("module_id"), (999, 999)), c.get("date") or "9999", c.get("no") or 0),
    )


def is_class(entry: dict) -> bool:
    """A live class (could have a recording), as opposed to a test, break or holiday."""
    return entry.get("kind", "class") == "class" and not entry.get("no_session")


def tag(entry: dict) -> str:
    """Short label for anything that isn't a regular class."""
    if entry.get("kind") == "test":
        return "test"
    if entry.get("no_session"):
        title = entry.get("title", "").lower()
        return "holiday" if "holiday" in title else "practice" if "practi" in title else "break"
    if entry.get("status") == "bonus":
        return "bonus"
    return (entry.get("bucket") or "").lower()


def pending(catalog: dict) -> list[dict]:
    """Classes whose recording is out but that have no notes yet, newest first."""
    todo = [c for c in catalog["classes"] if c.get("has_recording") and c.get("folder") and not (class_dir(c) / "notes.html").exists()]
    return sorted(todo, key=lambda c: c.get("date") or "", reverse=True)


def awaiting_scaler_notes(catalog: dict, days: int = 21) -> list[dict]:
    """Recent classes documented before Scaler Companion wrote its revision notes (it takes a few days)."""
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    return [
        c for c in catalog["classes"]
        if is_class(c) and c.get("folder") and (c.get("date") or "") >= since
        and (class_dir(c) / "notes.html").exists() and not (class_dir(c) / "revision_notes.md").exists()
    ]


def add_scaler_notes(scaler: Scaler, catalog: dict) -> list[dict]:
    """Fetch Scaler's revision notes for those classes again; returns the ones that now have them."""
    updated = []
    for entry in awaiting_scaler_notes(catalog):
        try:
            fetch_materials(scaler, entry)
        except Exception as e:
            print(f"  could not refresh Scaler's notes for {entry['title']}: {e}")
            continue
        if (class_dir(entry) / "revision_notes.md").exists():
            updated.append(entry)
    return updated


def sync(scaler: Scaler) -> dict:
    """Refresh the class list from Scaler. Numbers stay stable across syncs, new classes get new numbers."""
    previous = load_catalog()
    renumber = previous.get("version") != CATALOG_VERSION
    old = {c["class_id"]: c for c in previous["classes"]}
    modules = scaler.modules()
    found: list[dict] = []
    for m in modules:
        if m["status"] == "deprecated":
            continue
        classes = scaler.module_classes(m)
        print(f"  {m['name']}: {len(classes)} classes", flush=True)
        found += classes

    now = datetime.now(timezone.utc).isoformat()
    merged = []
    for c in found:
        prev = dict(old.get(c["class_id"], {}))
        if renumber:
            prev.pop("no", None)
        merged.append({**prev, **{k: v for k, v in c.items() if v is not None or k not in prev}})

    # The class list has no dates; each class's session has the date and the recording status.
    # Every class still without a recording is looked at again (upcoming classes get rescheduled,
    # recordings appear a day or two after class). Breaks/holidays have no session at all.
    todo = [c for c in merged if is_class(c) and not c.get("has_recording")]
    for c in merged:
        if c.get("kind") == "test":
            c["has_recording"] = False
    new_recordings = 0
    for i, c in enumerate(todo, 1):
        print(f"\r  checking classes {i}/{len(todo)}", end="", flush=True)
        try:
            s = scaler.class_session(c["class_id"])
        except Exception as e:  # one odd class must not abort the sync
            if "not found" in str(e):  # a "Break" / holiday placeholder, not a class
                c["no_session"], c["has_recording"] = True, False
                c.pop("error", None)
            else:
                c["error"] = str(e)[:200]
            continue
        lesson = s.get("batch_lesson") or {}
        c["date"] = lesson.get("start_time") or c.get("date")
        c["slug"] = Scaler.meeting_slug(s)
        c["meeting_id"] = lesson.get("session_id")
        c["has_recording"] = bool(c["slug"]) and lesson.get("status") == "completed" and not lesson.get("recording_pending")
        new_recordings += c["has_recording"]
        c.pop("error", None)
    if todo:
        print(f"\n  {new_recordings} newly available recording(s)")

    # Numbers follow class dates and never change once given, so `run 12` always means the same class.
    next_no = max((c.get("no") or 0 for c in merged), default=0) + 1
    for c in sorted(merged, key=lambda c: (c.get("date") or "9999", c["class_id"])):
        if c.get("kind") == "test":  # nothing to download: no number
            continue
        if "no" not in c:
            c["no"], next_no = next_no, next_no + 1
        if c.get("date"):
            c.setdefault("folder", safe_filename(f"{c['date'][:10]} {c['title']}"))

    merged.sort(key=lambda c: (c.get("no") is None, c.get("no") or 0, c.get("date") or ""))
    catalog = {"version": CATALOG_VERSION, "synced_at": now, "modules": modules, "classes": merged}
    write_json(CATALOG, catalog)
    return catalog


def select(catalog: dict, spec: list[str], module: str | None = None, search: str | None = None) -> list[dict]:
    classes = [c for c in catalog["classes"] if is_class(c)]  # tests/breaks have nothing to download
    if spec and spec != ["all"]:
        wanted: set[int] = set()
        for token in ",".join(spec).replace(" ", ",").split(","):
            if not token:
                continue
            if "-" in token:
                a, b = token.split("-", 1)
                wanted.update(range(int(a), int(b) + 1))
            else:
                wanted.add(int(token))
        classes = [c for c in classes if c.get("no") in wanted]
    elif not (spec or module or search):
        return []
    if module:
        classes = [c for c in classes if module.lower() in c["module"].lower()]
    if search:
        classes = [c for c in classes if search.lower() in c["title"].lower()]
    return classes


def class_dir(entry: dict) -> Path:
    return LIBRARY_DIR / entry["folder"]


def status(entry: dict) -> str:
    d = class_dir(entry)
    return "".join(
        flag if (d / name).exists() else "."
        for flag, name in (("V", "video.mp4"), ("T", "transcript.json"), ("N", "notes.html"))
    )


# -- fetching one class -------------------------------------------------------------------------

class _Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links: list[dict] = []
        self.text: list[str] = []
        self._href: str | None = None

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self._href = dict(attrs).get("href")

    def handle_endtag(self, tag):
        if tag == "a":
            self._href = None

    def handle_data(self, data):
        data = data.strip()
        if data:
            self.text.append(data)
        if self._href and data:
            self.links.append({"title": data, "url": self._href})


def _resources(html_items: list[dict]) -> list[dict]:
    """Scaler stores pre/post-lecture material as HTML snippets; keep plain text + links only."""
    out = []
    for item in html_items or []:
        p = _Links()
        p.feed(item.get("content") or "")
        urls = {link["url"] for link in p.links}
        bare = [{"title": u, "url": u} for u in re.findall(r"https?://[^\s<>\"']+", " ".join(p.text)) if u not in urls]
        out.append({"text": " ".join(p.text), "links": p.links + bare})
    return out


def _offset(iso: str | None, start: datetime) -> float | None:
    if not iso:
        return None
    t = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    return round((t - start).total_seconds(), 1)


def fetch_materials(scaler: Scaler, entry: dict) -> dict:
    """Everything Scaler has for one class, minus anything about other students."""
    session = scaler.class_session(entry["class_id"])
    lesson = session.get("batch_lesson") or {}
    slug = Scaler.meeting_slug(session)
    if not slug:
        raise NoRecording("no meeting is linked to this class")
    rec = scaler.recordings(slug)
    recordings = [r for r in rec.get("recordings") or [] if r.get("url")]
    if not recordings:
        raise NoRecording("recording not published yet" if lesson.get("recording_pending") else "no recording")
    main = max(recordings, key=lambda r: r.get("duration") or 0)
    rec_start = datetime.fromisoformat(main["start_time"].replace("Z", "+00:00"))

    staff = {p["user_id"] for p in rec.get("participants") or [] if p.get("role") not in (None, "audience")}
    notices = []
    for n in rec.get("notice_board") or []:
        try:
            body = json.loads(n.get("body") or "{}")
        except json.JSONDecodeError:
            body = {"title": n.get("body") or ""}
        notices.append({"at": _offset(n.get("created_at"), rec_start), **{k: body.get(k) for k in ("title", "description", "cta_link")}})

    quiz_data = scaler.quizzes(slug)
    quizzes = []
    for q in (quiz_data.get("quizzes") or {}).values():
        for pid in q.get("problems") or []:
            p = (quiz_data.get("quiz_problems") or {}).get(str(pid))
            if p:
                quizzes.append({
                    "at": _offset(q.get("start_time"), rec_start),
                    "question": p.get("description") or "",
                    "choices": [c.get("answer") for c in p.get("choices") or []],
                })
    quizzes.sort(key=lambda q: q["at"] or 0)

    info = {
        "class_id": entry["class_id"],
        "no": entry["no"],
        "title": lesson.get("title") or entry["title"],
        "module": entry["module"],
        "date": lesson.get("start_time") or entry["date"],
        "meeting": {"id": lesson.get("session_id"), "slug": slug},
        "recording": {k: main.get(k) for k in ("id", "duration", "start_time", "end_time", "url")},
        "other_recordings": [{k: r.get(k) for k in ("id", "duration", "url")} for r in recordings if r is not main],
        "bookmarks": sorted(
            (
                {"at": float(b["start_time"]), "title": b.get("title") or "", "type": b.get("bookmark_type")}
                for b in rec.get("bookmarks") or []
                if b.get("start_time") is not None and b.get("video_id") in (None, main["id"])
            ),
            key=lambda b: b["at"],
        ),
        "notices": notices,
        "instructor_messages": [
            {"at": _offset(m.get("created_at"), rec_start), "text": m.get("body") or ""}
            for m in rec.get("messages") or []
            if m.get("user_id") in staff and m.get("message_type") == "text"
        ],
        "quizzes": quizzes,
        "pre_lecture": _resources((lesson.get("pre_lecture_content") or {}).get("data")),
        "post_lecture": _resources((lesson.get("post_lecture_content") or {}).get("data")),
        "reading": _resources((lesson.get("literature_resources") or {}).get("data")),
        "lecture_notes": [
            {"filename": n.get("filename"), "url": n.get("link")}
            for n in (lesson.get("lecture_notes") or {}).get("data") or []
            if n.get("link")
        ],
    }

    d = class_dir(entry)
    d.mkdir(parents=True, exist_ok=True)
    meeting_id = lesson.get("session_id")
    if meeting_id:
        try:
            notes = scaler.revision_notes(meeting_id)
            if notes:
                (d / "revision_notes.md").write_text(notes, encoding="utf-8")
            cards = scaler.flashcards(meeting_id)
            if cards:
                write_json(d / "flashcards.json", cards)
        except Exception as e:  # companion is a bonus; never block the video on it
            print(f"  (Scaler revision notes unavailable: {e})", flush=True)

    for n in info["lecture_notes"]:
        try:
            n["file"] = _download_attachment(n["url"], d / "attachments", n["filename"])
        except Exception as e:
            print(f"  (could not download lecture notes {n['filename']}: {e})", flush=True)

    write_json(d / "class.json", info)
    return info


def _download_attachment(url: str, folder: Path, name: str | None) -> str:
    folder.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=120) as resp:
        ext = Path(urlsplit(url).path).suffix or mimetypes.guess_extension(resp.headers.get_content_type() or "") or ""
        base = safe_filename(name or Path(urlsplit(url).path).stem)
        dest = folder / (base if base.lower().endswith(ext.lower()) else base + ext)
        dest.write_bytes(resp.read())
    return str(dest.relative_to(folder.parent)).replace("\\", "/")


def download_video(scaler: Scaler, entry: dict, info: dict, max_height: int = 720) -> dict:
    d = class_dir(entry)
    # Rarely a class is recorded in several parts (e.g. a restart); keep the extra parts too.
    for i, extra in enumerate(info.get("other_recordings") or [], 2):
        part = d / f"video_part{i}.mp4"
        if extra.get("url") and not part.exists():
            print(f"  this class has an extra recording part ({i}), downloading it too", flush=True)
            download_recording(scaler.get_text, urljoin(BASE_URL, extra["url"]), part, max_height=max_height)
    out = d / "video.mp4"
    if out.exists():
        return {"skipped": True}
    return download_recording(scaler.get_text, urljoin(BASE_URL, info["recording"]["url"]), out, max_height=max_height)
