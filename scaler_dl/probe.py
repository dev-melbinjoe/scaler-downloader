"""Record what the Scaler dashboard does on the network while you click around.

Used to discover (and later re-discover, when Scaler changes its site) the JSON APIs behind the
class list and the recording player. Cookies, auth headers and login form fields are redacted at
capture time; everything stays on this machine under .debug/.
"""

from __future__ import annotations

import json
import re
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import BrowserContext, Error as PlaywrightError, Page, Request, Response

from .browser import open_browser, wait_until_closed
from .config import DASHBOARD_URL, DEBUG_DIR

SENSITIVE_HEADERS = {"cookie", "set-cookie", "authorization", "proxy-authorization"}
NOISE_HOSTS = (
    "google-analytics", "googletagmanager", "doubleclick", "facebook", "hotjar", "clarity.ms",
    "mixpanel", "segment.", "sentry", "intercom", "amplitude", "clevertap", "moengage", "webengage",
    "branch.io", "linkedin", "bing.com", "twitter", "fonts.googleapis", "fonts.gstatic", "newrelic",
    "nr-data", "datadog", "fullstory", "logrocket", "lr-ingest", "youtube.com/api/stats",
)
TEXT_TYPES = ("json", "mpegurl", "vtt", "srt", "xml", "dash", "text/plain", "text/html")
TEXT_EXTS = (".m3u8", ".vtt", ".srt", ".mpd", ".json", ".txt")
# Class pages live under .../session, so only match genuine login/OTP endpoints here.
AUTH_URL = re.compile(r"login|sign_?in|sign_?up|/auth\b|/auth/|oauth|otp|password|/users/session", re.I)
SECRET_FIELD = re.compile(
    r'("?(?:password|passwd|otp|pin|token|secret|code)[^"=&:]*"?\s*[:=]\s*)("[^"]*"|[^&,}\s]*)', re.I
)
MAX_BODY = 5_000_000


def _redact_headers(headers: dict) -> dict:
    return {
        k: (f"<redacted {len(v)} chars>" if k.lower() in SENSITIVE_HEADERS or "token" in k.lower() else v)
        for k, v in headers.items()
    }


def _scrub(text: str) -> str:
    return SECRET_FIELD.sub(lambda m: m.group(1) + '"<redacted>"', text)


def _slug(url: str, limit: int = 90) -> str:
    parts = urlsplit(url)
    return re.sub(r"[^A-Za-z0-9]+", "_", f"{parts.hostname}{parts.path}").strip("_")[-limit:]


def _pattern(url: str) -> str:
    """Collapse ids/hashes in a URL path so similar calls group together."""
    parts = urlsplit(url)
    path = re.sub(r"/[0-9a-f]{8}-[0-9a-f-]{27,}", "/{uuid}", parts.path, flags=re.I)
    path = re.sub(r"/[0-9a-f]{24,}", "/{hash}", path, flags=re.I)
    path = re.sub(r"/\d+", "/{n}", path)
    return f"{parts.hostname}{path}"


class Recorder:
    def __init__(self, out: Path):
        self.out = out
        (out / "bodies").mkdir(parents=True, exist_ok=True)
        (out / "dom").mkdir(exist_ok=True)
        self.log = open(out / "requests.jsonl", "a", encoding="utf-8")
        self.n = 0
        self.dom_n = 0
        self.patterns: Counter[tuple[str, str, str]] = Counter()
        self.pages: dict[Page, dict] = {}
        self.cookie_names: list[dict] = []
        self.cookies_at = 0.0

    def _write(self, entry: dict) -> None:
        self.log.write(json.dumps(entry, ensure_ascii=False) + "\n")
        self.log.flush()

    def on_response(self, resp: Response) -> None:
        try:
            self._record(resp)
        except Exception as e:  # capture problems must never break your browsing session
            self._write({"error": repr(e)[:300], "url": resp.url})

    def on_failed(self, req: Request) -> None:
        self._write({"failed": req.failure, "method": req.method, "url": req.url, "type": req.resource_type})

    def _record(self, resp: Response) -> None:
        req = resp.request
        rtype = req.resource_type
        url = resp.url
        if rtype in ("image", "font", "stylesheet") or any(n in url.lower() for n in NOISE_HOSTS):
            return
        self.n += 1
        ct = (resp.headers.get("content-type") or "").lower()
        path = urlsplit(url).path.lower()
        is_auth = bool(AUTH_URL.search(path))
        entry = {
            "n": self.n,
            "t": round(time.time(), 3),
            "method": req.method,
            "url": url,
            "status": resp.status,
            "type": rtype,
            "ct": ct,
            "frame": req.frame.url if req.frame else None,
            "req_headers": _redact_headers(req.headers),
            "resp_headers": _redact_headers(resp.headers),
        }
        if req.post_data:
            entry["post"] = f"<redacted {len(req.post_data)} chars>" if is_auth else _scrub(req.post_data[:20000])
        self.patterns[(req.method, _pattern(url), ct.split(";")[0])] += 1

        wants_body = (any(t in ct for t in TEXT_TYPES) or path.endswith(TEXT_EXTS)) and not (300 <= resp.status < 400)
        if wants_body and not is_auth:
            try:
                body = resp.body()
            except PlaywrightError as e:
                entry["body_error"] = str(e)[:200]
            else:
                entry["size"] = len(body)
                if len(body) <= MAX_BODY:
                    if "json" in ct:
                        ext = ".json"
                    elif "mpegurl" in ct or path.endswith(".m3u8"):
                        ext = ".m3u8"
                    elif "html" in ct:
                        ext = ".html"
                    else:
                        ext = Path(path).suffix or ".txt"
                    name = f"{self.n:05d}_{_slug(url)}{ext}"
                    (self.out / "bodies" / name).write_bytes(body)
                    entry["body"] = name
        self._write(entry)

    def tick(self, ctx: BrowserContext) -> None:
        now = time.time()
        if now - self.cookies_at > 30:
            self.cookies_at = now
            # Names only (no values): enough to see how the session is kept.
            self.cookie_names = [
                {"name": c["name"], "domain": c["domain"], "path": c["path"], "expires": c.get("expires")}
                for c in ctx.cookies()
            ]
        self.snapshot_dom(ctx, now)

    def snapshot_dom(self, ctx: BrowserContext, now: float) -> None:
        """Save rendered HTML when a tab settles on a new URL, and again if the page changes a lot."""
        for pg in ctx.pages:
            if pg.is_closed():
                continue
            st = self.pages.setdefault(pg, {"url": None, "due": None, "len": 0, "last": 0.0})
            if pg.url != st["url"]:
                st.update(url=pg.url, due=now + 4, len=0)
            elif st["due"] is None and now - st["last"] > 20:
                st["due"] = now
            if st["due"] is None or now < st["due"]:
                continue
            st["due"], st["last"] = None, now
            html = pg.content()
            if st["len"] and abs(len(html) - st["len"]) < 0.02 * st["len"]:
                continue
            st["len"] = len(html)
            self.dom_n += 1
            name = f"{self.dom_n:03d}_{_slug(pg.url)}.html"
            (self.out / "dom" / name).write_text(f"<!-- {pg.url} -->\n{html}", encoding="utf-8")
            self._write({"dom": name, "page": pg.url, "t": round(now, 3)})

    def finish(self) -> None:
        (self.out / "cookie_names.json").write_text(json.dumps(self.cookie_names, indent=1), encoding="utf-8")
        lines = [
            f"{count:5d}  {method:6s} {ct:28s} {pattern}"
            for (method, pattern, ct), count in self.patterns.most_common()
        ]
        (self.out / "summary.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
        self.log.close()


def run_probe(start_url: str = DASHBOARD_URL) -> Path:
    out = DEBUG_DIR / f"probe-{datetime.now():%Y%m%d-%H%M%S}"
    rec = Recorder(out)
    print(f"Capturing network activity into {out}")
    print("Log in if asked, open a past class, play its recording for ~30s, then close the browser window.")
    try:
        with open_browser() as ctx:
            ctx.on("response", rec.on_response)
            ctx.on("requestfailed", rec.on_failed)
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.goto(start_url, wait_until="domcontentloaded")
            wait_until_closed(ctx, on_tick=lambda: rec.tick(ctx))
    finally:
        rec.finish()
    print(f"Captured {rec.n} responses and {rec.dom_n} page snapshots -> {out}")
    return out
