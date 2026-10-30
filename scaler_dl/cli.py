"""Download Scaler Academy class recordings and build timestamped study notes.

  {prog} login            log in once (a Chrome window opens)
  {prog} sync             fetch your class list from Scaler
  {prog} list             show classes with their numbers
  {prog} run 12 14-16     download + transcribe + notes for those classes
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

from .config import DASHBOARD_URL


def _selected(args: argparse.Namespace) -> list[dict]:
    from . import library

    catalog = library.load_catalog()
    if not catalog["classes"]:
        sys.exit("No class list yet. Run `python main.py sync` first (or press Refresh class list on the library page).")
    chosen = library.select(catalog, args.classes, args.module, args.search)
    if not chosen:
        sys.exit("No classes selected. Give class numbers (see `list`), e.g. `5`, `3-7`, `all`, or --module/--search.")
    return chosen


def _header(entry: dict) -> None:
    from .library import tag

    label = f"  ({tag(entry)})" if tag(entry) else ""
    print(f"\n[{entry.get('no', '-')}] {entry['date'][:10] if entry.get('date') else '????-??-??'}  {entry['title']}{label}", flush=True)


def cmd_login(args: argparse.Namespace) -> int:
    from .browser import open_browser, wait_until_closed

    with open_browser() as ctx:
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(DASHBOARD_URL, wait_until="domcontentloaded")
        print("Log in to Scaler in the Chrome window, then close the window once you see your dashboard.")
        wait_until_closed(ctx)
    print("Session saved; later commands reuse it.")
    return 0


def cmd_probe(args: argparse.Namespace) -> int:
    from .probe import run_probe

    run_probe(args.url)
    return 0


def cmd_sync(args: argparse.Namespace) -> int:
    from . import library
    from .browser import open_browser
    from .scaler import Scaler

    with open_browser() as ctx:
        scaler = Scaler(ctx)
        scaler.connect()
        print("Fetching your modules and classes...")
        catalog = library.sync(scaler)
    total = len(catalog["classes"])
    with_rec = sum(1 for c in catalog["classes"] if c.get("has_recording"))
    breaks = sum(1 for c in catalog["classes"] if c.get("no_session"))
    print(f"{total - breaks} classes ({with_rec} with recordings) saved to {library.CATALOG}"
          + (f"; {breaks} items without a live class skipped (breaks, holidays, practice sets)" if breaks else ""))
    print("Next: `list` to see their numbers, or `update` to process everything new.")
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    from . import library

    catalog = library.load_catalog()
    classes = [c for c in catalog["classes"] if library.is_class(c)]  # not tests / breaks / holidays
    if args.module:
        classes = [c for c in classes if args.module.lower() in c["module"].lower()]
    if args.search:
        classes = [c for c in classes if args.search.lower() in c["title"].lower()]
    if not args.all:
        now = datetime.now(timezone.utc).isoformat()
        classes = [c for c in classes if (c.get("date") or "9") < now]
    if not classes:
        print("Nothing to show. Run `sync` first (or loosen the filters).")
        return 0
    module = None
    for c in library.in_display_order(catalog, classes):
        if c["module"] != module:
            module = c["module"]
            print(f"\n== {module}")
        rec = "rec" if c.get("has_recording") else "   "
        print(f"{c['no']:>4}  {(c.get('date') or '')[:10]}  {rec}  {library.status(c)}  {library.tag(c):<6} {c['title']}")
    print("\nrec = recording available   V = video  T = transcript  N = notes   bonus = extra session")
    return 0


def _download(scaler, entries: list[dict], args: argparse.Namespace) -> int:
    """Materials + video for each class. 0 ok, 1 some failed, 2 stopped (disk full / logged out)."""
    from . import library
    from .downloader import NotEnoughSpace
    from .scaler import NotLoggedIn

    failures = 0
    for entry in entries:
        _header(entry)
        if not entry.get("folder"):
            print("  skipped: this class hasn't happened yet")
            continue
        try:
            info = library.fetch_materials(scaler, entry)
            print(f"  materials saved ({len(info['bookmarks'])} bookmarks, {len(info['quizzes'])} quiz questions)", flush=True)
            result = library.download_video(scaler, entry, info, max_height=args.quality)
            if result.get("skipped"):
                print("  video already downloaded", flush=True)
            else:
                print(f"  video saved: {result['bytes'] / 1e6:.0f} MB", flush=True)
        except NotEnoughSpace as e:
            print(f"  STOPPED: not enough disk space -- {e}")
            return 2
        except NotLoggedIn as e:
            print(f"  STOPPED: {e} Log in again with `python main.py login`, then rerun this.")
            return 2
        except library.NoRecording as e:
            print(f"  skipped: {e}")
            failures += 1
        except Exception as e:
            print(f"  FAILED: {e or type(e).__name__}  (run the same command again to retry/resume)")
            failures += 1
    return 1 if failures else 0


def cmd_download(args: argparse.Namespace) -> int:
    from .browser import open_browser
    from .index import build_index
    from .scaler import Scaler

    chosen = _selected(args)
    with open_browser() as ctx:
        scaler = Scaler(ctx)
        scaler.connect()
        code = _download(scaler, chosen, args)
    build_index()
    return code


class _Tee:
    """Print to the console and append to a log file (progress lines rewritten with \\r stay off the log)."""

    def __init__(self, console, log_path: Path):
        log_path.parent.mkdir(parents=True, exist_ok=True)
        self.console, self.log = console, open(log_path, "a", encoding="utf-8")

    def write(self, text: str) -> int:
        self.console.write(text)
        if not text.startswith("\r"):
            self.log.write(text)
        return len(text)

    def flush(self) -> None:
        self.console.flush()
        self.log.flush()

    def __getattr__(self, name):  # isatty(), encoding, ... come from the real console
        return getattr(self.console, name)


def cmd_update(args: argparse.Namespace) -> int:
    """Everything new since last time: refresh the class list, then download, transcribe and make notes
    for each class whose recording is out but has no notes yet (newest first)."""
    from . import library
    from .browser import open_browser
    from .scaler import Scaler

    sys.stdout = _Tee(sys.stdout, library.LIBRARY_DIR / "update.log")
    print(f"\n===== update {datetime.now():%Y-%m-%d %H:%M} =====", flush=True)
    code, todo = 0, []
    with open_browser() as ctx:
        scaler = Scaler(ctx)
        scaler.connect()
        print("Checking Scaler for new classes and recordings...", flush=True)
        catalog = library.sync(scaler)
        refreshed = library.add_scaler_notes(scaler, catalog)
        waiting = library.pending(catalog)
        todo = waiting[: args.limit or None]
        if not todo:
            print("Up to date: every available recording already has notes.")
        else:
            later = f"; {len(waiting) - len(todo)} older ones left for next runs" if len(waiting) > len(todo) else ""
            print(f"{len(todo)} class(es) to process now, newest first{later}.", flush=True)
            code = _download(scaler, todo, args)
    entries = [e for e in todo if (library.class_dir(e) / "video.mp4").exists()]
    _transcribe(entries, args)
    _notes(entries + [e for e in refreshed if e not in entries])  # also re-made: notes that gained Scaler's notes
    return code


def _downloaded(args: argparse.Namespace, need: str) -> list[dict]:
    """Selected classes that already have `need` on disk (so `all` doesn't list 100 missing ones)."""
    from . import library

    chosen = _selected(args)
    have = [e for e in chosen if e.get("folder") and (library.class_dir(e) / need).exists()]
    if len(chosen) == 1 and not have:
        _header(chosen[0])
        print("  nothing downloaded yet -- run `download` first")
    return have


def _transcribe(entries: list[dict], args: argparse.Namespace) -> None:
    from . import library
    from .transcribe import transcribe, vocabulary_hint

    for entry in entries:
        d = library.class_dir(entry)
        if (d / "transcript.json").exists() and not getattr(args, "force", False):
            continue
        _header(entry)
        print(f"  transcribing with Whisper '{args.model}' (the slow part: ~30 min per 2.5 h class on a 4-core CPU)", flush=True)
        transcribe(d / "video.mp4", d, model_size=args.model, language=args.language, hotwords=vocabulary_hint(entry["title"]))


def _notes(entries: list[dict]) -> None:
    from . import library
    from .index import build_index
    from .notes import build_notes

    for entry in entries:
        _header(entry)
        build_notes(library.class_dir(entry), number=entry["no"])
    print(f"\nLibrary index: {build_index()}")


def cmd_transcribe(args: argparse.Namespace) -> int:
    _transcribe(_downloaded(args, "video.mp4"), args)
    return 0


def cmd_notes(args: argparse.Namespace) -> int:
    from . import library

    # A video, or notes made before (you may have deleted the video to save space). Not a
    # half-finished download: notes there would mark the class done and stop it being resumed.
    entries = [
        e for e in _downloaded(args, "class.json")
        if (library.class_dir(e) / "video.mp4").exists() or (library.class_dir(e) / "notes.html").exists()
    ]
    _notes(entries)
    return 0


def cmd_app(args: argparse.Namespace) -> int:
    from .server import serve

    return serve(port=args.port, open_page=not args.no_browser)


def cmd_run(args: argparse.Namespace) -> int:
    # Downloads first (needs the browser), then the long offline part with the browser closed.
    code = cmd_download(args)
    entries = _downloaded(args, "video.mp4")
    _transcribe(entries, args)
    _notes(entries)
    return code


def main(argv: list[str] | None = None) -> int:
    # Class titles can contain emoji / non-Latin text; never crash on a legacy console code page.
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="replace")

    prog = "python main.py" if Path(sys.argv[0]).name == "main.py" else "python -m scaler_dl"
    parser = argparse.ArgumentParser(
        prog=prog, description=__doc__.format(prog=prog), formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("login", help="open Chrome so you can log in to Scaler (the session is remembered)")

    p = sub.add_parser("probe", help="record the dashboard's network calls while you click around (debugging)")
    p.add_argument("--url", default=DASHBOARD_URL, help="page to start on")

    sub.add_parser("sync", help="fetch your modules and classes from Scaler")

    p = sub.add_parser("list", help="show classes and their numbers")
    p.add_argument("--module", help="only modules whose name contains this text")
    p.add_argument("--search", help="only classes whose title contains this text")
    p.add_argument("--all", action="store_true", help="include upcoming classes")

    def selection(p: argparse.ArgumentParser) -> None:
        p.add_argument("classes", nargs="*", help="class numbers from `list`: 5  3-7  2,4,9  all")
        p.add_argument("--module", help="every class of modules whose name contains this text")
        p.add_argument("--search", help="every class whose title contains this text")

    def whisper(p: argparse.ArgumentParser) -> None:
        p.add_argument("--model", default="small", help="Whisper model: tiny, base, small (default), medium")
        p.add_argument("--language", default="en", help="spoken language (default en)")

    for name, help_text in (
        ("download", "download recordings + Scaler's notes/bookmarks/quizzes"),
        ("run", "download, transcribe and build notes in one go"),
        ("update", "check Scaler and process every class whose recording is new (newest first)"),
    ):
        p = sub.add_parser(name, help=help_text)
        if name == "update":
            # New classes come first; older ones without notes get worked through a few per run.
            p.add_argument("--limit", type=int, default=3, help="classes to process per run, newest first (default 3; 0 = all)")
        else:
            selection(p)
        p.add_argument("--quality", type=int, default=720, help="max video height: 720 (default) or 480")
        if name != "download":
            whisper(p)

    p = sub.add_parser("transcribe", help="make timestamped transcripts with Whisper (offline)")
    selection(p)
    whisper(p)
    p.add_argument("--force", action="store_true", help="redo existing transcripts")

    p = sub.add_parser("notes", help="(re)build the study notes from what's downloaded")
    selection(p)

    p = sub.add_parser("app", help="open the library page with buttons (what `main.py` alone does)")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--no-browser", action="store_true", help="don't open the page automatically")

    args = parser.parse_args(argv)
    handlers = {
        "login": cmd_login, "probe": cmd_probe, "sync": cmd_sync, "list": cmd_list, "download": cmd_download,
        "run": cmd_run, "update": cmd_update, "transcribe": cmd_transcribe, "notes": cmd_notes, "app": cmd_app,
    }
    from .scaler import NotLoggedIn

    try:
        return handlers[args.command](args)
    except NotLoggedIn as e:
        print(f"\n{e}\nRun `{prog} login`, log in to Scaler in the Chrome window, close it, then try again.")
        return 2
