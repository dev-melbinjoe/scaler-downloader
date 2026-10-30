"""The library as a small local web app: `python main.py` opens http://127.0.0.1:8765.

The page's buttons call this server, which runs the same steps as the command line, one job at a
time in a background thread (downloads and transcription compete for the CPU, disk and the
Scaler browser session). It only listens on 127.0.0.1, checks the Host header, and every action
must carry a secret token embedded in the page, so other websites can't trigger anything.
"""

from __future__ import annotations

import http.server
import json
import mimetypes
import secrets
import sys
import threading
import time
import traceback
import webbrowser
from collections import deque
from urllib.parse import unquote, urlsplit

from . import control, library
from .config import DASHBOARD_URL
from .index import build_index, render_index

TOKEN = secrets.token_urlsafe(24)
QUALITY, MODEL, LANGUAGE = 720, "small", "en"
UPDATE_LIMIT = 3  # "Get new recordings": the newest classes waiting, a few at a time

for ext, ctype in ((".vtt", "text/vtt"), (".srt", "text/plain"), (".md", "text/markdown"), (".mp4", "video/mp4")):
    mimetypes.add_type(ctype, ext)


class Jobs:
    """A queue of jobs run one after another by a single worker thread."""

    def __init__(self):
        self.cond = threading.Condition()
        self.queue: deque[dict] = deque()
        self.current: dict | None = None
        self.log: deque[str] = deque(maxlen=300)
        self.status = ""  # latest progress line of the running job
        self.version = 0  # bumped when a job finishes: the page reloads its list
        self.last: dict | None = None
        threading.Thread(target=self._worker, daemon=True, name="jobs").start()

    def add(self, kind: str, entry: dict | None = None) -> bool:
        class_id = entry["class_id"] if entry else None
        with self.cond:
            if any(j and (j["kind"], j["class_id"]) == (kind, class_id) for j in (self.current, *self.queue)):
                return False
            titles = {"sync": "Refreshing the class list", "update": "Checking for new recordings", "login": "Log in to Scaler"}
            title = titles.get(kind) or f"Download & notes: {entry['title']}"
            self.queue.append({"kind": kind, "class_id": class_id, "title": title})
            self.cond.notify()
        return True

    def stop(self) -> None:
        with self.cond:
            self.queue.clear()
            if self.current and self.current["kind"] != "login":  # login just waits for you to close Chrome
                control.STOP.set()

    def say(self, text: str) -> None:
        """Everything printed by the running job (progress lines included) lands here."""
        with self.cond:
            if text.startswith("\r"):
                self.status = text.strip()
                return
            for line in text.splitlines():
                if line.strip():
                    self.log.append(line.rstrip())
                    self.status = line.strip()

    def state(self) -> dict:
        with self.cond:
            return {
                "current": self.current, "queue": list(self.queue), "status": self.status,
                "log": list(self.log)[-60:], "version": self.version, "last": self.last,
            }

    def _worker(self) -> None:
        while True:
            with self.cond:
                while not self.queue:
                    self.cond.wait()
                job = self.queue.popleft()
                self.current, self.status = job, ""
            print(f"\n>>> {job['title']}", flush=True)
            result, message = "done", ""
            try:
                message = run_job(self, job)
            except control.Stopped as e:
                result, message = "stopped", f"Stopped: {job['title']} ({e})"
            except Exception as e:
                from .scaler import NotLoggedIn

                if isinstance(e, NotLoggedIn):
                    result, message = "login", "Scaler needs you to log in again."
                else:
                    traceback.print_exc()
                    result, message = "failed", f"{job['title']}: {e or type(e).__name__}"
            print(f"<<< {message}", flush=True)
            with self.cond:
                # Cleared only once the job is over, so a Stop pressed as it starts isn't lost.
                control.STOP.clear()
                self.current = None
                self.version += 1
                self.last = {"result": result, "message": message, "at": time.time()}


def _find(class_id: int) -> dict:
    for c in library.load_catalog()["classes"]:
        if c["class_id"] == class_id:
            return c
    raise LookupError(f"class {class_id} is not in the list; refresh it first")


def run_job(jobs: Jobs, job: dict) -> str:
    from .browser import open_browser, wait_until_closed
    from .notes import build_notes
    from .scaler import Scaler
    from .transcribe import transcribe, vocabulary_hint

    kind = job["kind"]
    if kind == "login":
        with open_browser() as ctx:
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.goto(DASHBOARD_URL, wait_until="domcontentloaded")
            print("Log in to Scaler in the Chrome window, then close that window.", flush=True)
            wait_until_closed(ctx)
        return "Login saved. You can use the other buttons again."

    if kind in ("sync", "update"):
        refreshed = []
        with open_browser() as ctx:
            scaler = Scaler(ctx)
            scaler.connect()
            catalog = library.sync(scaler)
            if kind == "update":
                refreshed = library.add_scaler_notes(scaler, catalog)
        for entry in refreshed:
            build_notes(library.class_dir(entry), number=entry["no"])
            print(f"  added Scaler's revision notes to: {entry['title']}", flush=True)
        build_index()
        if kind == "sync":
            return "Class list refreshed."
        control.check()  # Stop pressed while the list was refreshing: don't queue anything
        todo = library.pending(catalog)[:UPDATE_LIMIT]
        for entry in todo:
            jobs.add("process", entry)
        return f"Class list refreshed; {len(todo)} class(es) queued." if todo else "Up to date: every available recording has notes."

    entry = _find(job["class_id"])
    d = library.class_dir(entry)
    if not ((d / "class.json").exists() and (d / "video.mp4").exists()):
        with open_browser() as ctx:
            scaler = Scaler(ctx)
            scaler.connect()
            print(f"Downloading [{entry['no']}] {entry['title']}", flush=True)
            info = library.fetch_materials(scaler, entry)
            library.download_video(scaler, entry, info, max_height=QUALITY)
    if not (d / "transcript.json").exists():
        print("Transcribing (about 30 min for a 2.5 h class)...", flush=True)
        transcribe(d / "video.mp4", d, model_size=MODEL, language=LANGUAGE, hotwords=vocabulary_hint(entry["title"]))
    build_notes(d, number=entry["no"])
    build_index()
    return f"Notes ready: {entry['title']}"


class _Capture:
    """Mirror stdout to the console and into the job log shown on the page."""

    def __init__(self, console, jobs: Jobs):
        self.console, self.jobs = console, jobs

    def write(self, text: str) -> int:
        self.console.write(text)
        self.jobs.say(text)
        return len(text)

    def flush(self) -> None:
        self.console.flush()

    def __getattr__(self, name):
        return getattr(self.console, name)


def make_handler(jobs: Jobs, port: int):
    hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
    origins = {f"http://{h}" for h in hosts}

    class Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args) -> None:  # keep the console for job output
            pass

        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _json(self, code: int, data: dict) -> None:
            self._send(code, json.dumps(data).encode(), "application/json")

        def _host_ok(self) -> bool:
            # Rejecting other Host names blocks DNS-rebinding tricks from websites.
            if self.headers.get("Host") in hosts:
                return True
            self._send(403, b"Forbidden", "text/plain")
            return False

        def do_HEAD(self) -> None:
            self.do_GET()

        def do_GET(self) -> None:
            if not self._host_ok():
                return
            path = unquote(urlsplit(self.path).path)
            if path in ("/", "/index.html"):
                self._send(200, render_index(TOKEN).encode("utf-8"), "text/html; charset=utf-8")
            elif path == "/api/state":
                self._json(200, jobs.state())
            else:
                self._file(path)

        def do_POST(self) -> None:
            if not self._host_ok():
                return
            origin = self.headers.get("Origin")
            if self.headers.get("X-Token") != TOKEN or (origin and origin not in origins):
                return self._json(403, {"error": "not allowed"})
            length = int(self.headers.get("Content-Length") or 0)
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
            except json.JSONDecodeError:
                body = {}
            action = urlsplit(self.path).path.removeprefix("/api/")
            if action == "stop":
                jobs.stop()
                return self._json(200, {"ok": True})
            if action in ("sync", "update", "login"):
                jobs.add(action)
                return self._json(200, {"ok": True})
            if action == "process":
                try:
                    entry = _find(int(body.get("class_id")))
                except (LookupError, TypeError, ValueError) as e:
                    return self._json(400, {"error": str(e)})
                if not (library.is_class(entry) and entry.get("has_recording")):
                    return self._json(400, {"error": "This class has no recording yet."})
                jobs.add("process", entry)
                return self._json(200, {"ok": True})
            self._json(404, {"error": f"unknown action {action}"})

        def _file(self, path: str) -> None:
            root = library.LIBRARY_DIR.resolve()
            target = (root / path.lstrip("/")).resolve()
            if root not in target.parents or not target.is_file():
                return self._send(404, b"Not found", "text/plain")
            size = target.stat().st_size
            ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
            if ctype.startswith("text/") or ctype == "application/json":
                ctype += "; charset=utf-8"
            start, end, code = 0, size - 1, 200
            rng = self.headers.get("Range", "")
            if rng.startswith("bytes="):  # video seeking asks for byte ranges
                first, _, last = rng[6:].split(",")[0].partition("-")
                if first:
                    start, end = int(first), min(int(last) if last else size - 1, size - 1)
                else:
                    start = max(size - int(last or 0), 0)
                if start > end or start >= size:
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{size}")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                code = 206
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(end - start + 1))
            if code == 206:
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.end_headers()
            if self.command == "HEAD":
                return
            try:
                with open(target, "rb") as f:
                    f.seek(start)
                    left = end - start + 1
                    while left > 0:
                        chunk = f.read(min(1 << 20, left))
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        left -= len(chunk)
            except (ConnectionError, OSError):  # the browser cancels requests when you seek
                pass

    return Handler


class _Server(http.server.ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address) -> None:
        # The browser drops connections all the time (leaving a page mid-request, seeking in a
        # video); on Windows that surfaces as WinError 10053/10054. Nothing is wrong: stay quiet.
        if isinstance(sys.exc_info()[1], (ConnectionError, TimeoutError)):
            return
        super().handle_error(request, client_address)


def _already_running(port: int) -> bool:
    """Another `main.py` serving this library? Then just open its page instead of a second copy
    (two copies would run jobs on the same files at the same time)."""
    import urllib.request

    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/state", timeout=2) as resp:
            return "version" in json.loads(resp.read())
    except (OSError, ValueError):
        return False


def serve(port: int = 8765, open_page: bool = True) -> int:
    if _already_running(port):
        url = f"http://127.0.0.1:{port}/"
        print(f"The library is already open at {url} (in another window); showing it.")
        if open_page:
            webbrowser.open(url)
        return 0
    jobs = Jobs()
    sys.stdout = _Capture(sys.stdout, jobs)
    build_index()
    server = None
    for p in range(port, port + 10):
        try:
            server = _Server(("127.0.0.1", p), make_handler(jobs, p))
            port = p
            break
        except OSError:
            continue
    if server is None:
        print(f"Couldn't open a local port between {port} and {port + 9}.")
        return 1
    url = f"http://127.0.0.1:{port}/"
    print(f"Your Scaler library is open at {url}")
    print("Keep this window open while you use the buttons. Press Ctrl+C to quit.", flush=True)
    if open_page:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nClosing. Anything unfinished continues where it left off next time.")
    finally:
        control.STOP.set()
        server.server_close()
    return 0
