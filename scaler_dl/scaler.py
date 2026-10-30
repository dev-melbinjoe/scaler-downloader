"""Scaler Academy's web API, used through the logged-in Chrome profile.

Endpoints were mapped with `python -m scaler_dl probe` (Oct 2026):

  /mentee-modules/?filter[category]=core           your modules
  /mentee-academy-topics/classes?...                classes of one module
  /api/v2/classroom/<class id>/session              class -> meeting slug, lecture notes, resources
  /meetings/<slug>/recordings                       HLS playlist url, instructor bookmarks, notice board
  /meetings/<slug>/recordings/quizzes               in-class quiz questions
  api.companion.scaler.com/api/flashcard[/notes]    Scaler's own revision notes + flashcards
"""

from __future__ import annotations

import re
import time
from urllib.parse import urlencode

from playwright.sync_api import BrowserContext, Error as PlaywrightError

from .config import BASE_URL, DASHBOARD_URL

COMPANION_API = "https://api.companion.scaler.com"
# The statuses the dashboard's class list asks for. "bonus" covers extra sessions (revision,
# problem-solving, practice) and break placeholders; "repeat" classes from a module taken again.
CLASS_STATUSES = ("active", "locked", "repeat", "bonus")


class NotLoggedIn(RuntimeError):
    pass


class ScalerError(RuntimeError):
    pass


class Scaler:
    def __init__(self, ctx: BrowserContext):
        self.ctx = ctx
        self.page = ctx.pages[0] if ctx.pages else ctx.new_page()
        self.csrf = ""
        self.auth_token = ""
        self._companion_token: str | None = None

    def connect(self) -> None:
        """Open the dashboard once: confirms the session and picks up the tokens its API calls need."""
        self.page.goto(DASHBOARD_URL, wait_until="domcontentloaded")
        if "/users/" in self.page.url:  # bounced to sign-in or the device-limit page
            raise NotLoggedIn("Not logged in to Scaler.")  # the CLI and the page each say how to log in
        self.csrf = self.page.evaluate("() => document.querySelector('meta[name=csrf-token]')?.content || ''")
        self.auth_token = self.page.evaluate("() => window.__AUTH_TOKEN__ || ''")

    # -- plumbing -----------------------------------------------------------------------------

    def _headers(self) -> dict:
        return {
            "accept": "application/json",
            "content-type": "application/json",
            "x-requested-with": "XMLHttpRequest",
            "app-name": "desktop",
            "x-csrf-token": self.csrf,
            "referer": DASHBOARD_URL,
        }

    def _request(self, url: str, headers: dict, method: str = "GET"):
        for attempt in range(5):
            try:
                resp = self.ctx.request.fetch(url, method=method, headers=headers, timeout=60_000)
            except PlaywrightError as e:
                if attempt == 4:
                    raise ScalerError(f"network error for {url}: {e}") from e
            else:
                if resp.status == 429 or resp.status >= 500:
                    if attempt == 4:
                        raise ScalerError(f"HTTP {resp.status} for {url}")
                elif resp.status in (401, 403) and url.startswith(BASE_URL):
                    raise NotLoggedIn("Scaler rejected the saved login.")
                else:
                    return resp
            time.sleep(2 ** attempt)
        raise AssertionError("unreachable")

    def get_json(self, path: str, query: list[tuple[str, object]] | None = None):
        url = (path if path.startswith("http") else BASE_URL + path) + (f"?{urlencode(query)}" if query else "")
        resp = self._request(url, self._headers())
        if "json" not in (resp.headers.get("content-type") or ""):
            # Rails redirects expired sessions to the HTML sign-in page.
            raise NotLoggedIn("Got a web page instead of data; the Scaler session has probably expired.")
        if resp.status == 404:
            return None
        if not resp.ok:
            raise ScalerError(f"HTTP {resp.status} for {path}")
        return resp.json()

    def get_text(self, url: str) -> str:
        resp = self._request(url if url.startswith("http") else BASE_URL + url, {"referer": DASHBOARD_URL})
        if not resp.ok:
            raise ScalerError(f"HTTP {resp.status} for {url}")
        return resp.text()

    # -- catalog ------------------------------------------------------------------------------

    def modules(self) -> list[dict]:
        d = self.get_json("/mentee-modules/", [("filter[category]", "core")])
        info = {i["id"]: i["attributes"] for i in d.get("included", []) if i["type"] == "academy_module"}
        mods = []
        for m in d["data"]:
            mid = m["relationships"]["academy_module"]["data"]["id"]
            a = info.get(mid, {})
            mods.append({
                "id": int(mid),
                "name": a.get("name") or f"Module {mid}",
                "order": m["attributes"].get("order") or 0,
                "status": m["attributes"].get("status"),
                "category": a.get("category") or "core",
            })
        return sorted(mods, key=lambda m: (m["order"], m["id"]))

    def module_classes(self, module: dict) -> list[dict]:
        """Every item of a module, exactly as the dashboard's module page lists them: classes,
        bonus sessions, tests (contests), breaks and holidays, with their day number and date."""
        query = [
            ("filter[academy_module_id]", module["id"]),
            *[("filter[status][]", status) for status in CLASS_STATUSES],
            ("filter[category]", module["category"]),
            ("sort", "date_of_topic"),
            # Including the class objects themselves is what makes the API send their dates.
            ("include[]", "super_batch_academy_topic"),
            ("include[]", "super_batch_academy_topic.academy_topic"),
            ("include[]", "super_batch_academy_topic.lecture_bucket"),
        ]
        d = self.get_json("/mentee-academy-topics/classes", query) or {}
        inc = {(i["type"], i["id"]): i for i in d.get("included", [])}
        classes = []
        for item in d.get("data", []):
            kind = "test" if item["attributes"].get("activity_type") == "contest" else "class"
            # Item ids look like "<batch>::<class id>"; only some responses also carry the relationship.
            sbat_ref = (item.get("relationships", {}).get("super_batch_academy_topic") or {}).get("data") or {}
            sbat_id = str(sbat_ref.get("id") or item["id"].split("::")[-1])
            sbat = inc.get(("super_batch_academy_topic", sbat_id), {})
            topic_ref = sbat.get("relationships", {}).get("academy_topic", {}).get("data") or {}
            topic = inc.get(("academy_topic", topic_ref.get("id")), {})
            bucket_ref = (sbat.get("relationships", {}).get("lecture_bucket") or {}).get("data") or {}
            bucket = inc.get(("lecture_bucket", bucket_ref.get("id")), {})
            classes.append({
                "class_id": int(sbat_id),
                "kind": kind,
                "title": topic.get("attributes", {}).get("title") or f"Class {sbat_id}",
                "date": sbat.get("attributes", {}).get("date_of_topic"),
                "module_id": module["id"],
                "module": module["name"],
                "day_number": (item.get("meta") or {}).get("day_number"),
                "status": item["attributes"].get("status"),
                "bucket": bucket.get("attributes", {}).get("title"),
            })
        return classes

    # -- one class ----------------------------------------------------------------------------

    def class_session(self, class_id: int) -> dict:
        d = self.get_json(f"/api/v2/classroom/{class_id}/session")
        if not d:
            raise ScalerError(f"class {class_id} not found")
        return d["data"]["attributes"]

    @staticmethod
    def meeting_slug(session: dict) -> str | None:
        link = (session.get("batch_lesson") or {}).get("session_link") or ""
        m = re.search(r"/meetings/(?:i/)?([^/?#]+)", link)
        return m.group(1) if m else None

    def recordings(self, slug: str) -> dict:
        return self.get_json(f"/meetings/{slug}/recordings") or {}

    def quizzes(self, slug: str) -> dict:
        return self.get_json(f"/meetings/{slug}/recordings/quizzes") or {}

    # -- Scaler Companion (AI revision notes + flashcards) ------------------------------------

    def _companion_get(self, path: str, meeting_id: int, retry: bool = True):
        if self._companion_token is None:
            token = self.get_json("/scaler/generate-companion-token")["token"]
            resp = self._request(
                f"{COMPANION_API}/api/verify_token/v1",
                {"authorization": f"Bearer {token}", "referer": BASE_URL + "/"},
                method="POST",
            )
            if not resp.ok:
                raise ScalerError(f"companion login failed: HTTP {resp.status}")
            self._companion_token = resp.json()["access_token"]
        resp = self._request(
            f"{COMPANION_API}{path}?resource_type=class&resource_id={meeting_id}",
            {
                "authorization": f"Bearer {self._companion_token}",
                "x-user-token": self.auth_token,
                "content-type": "application/json",
                "referer": BASE_URL + "/",
            },
        )
        if resp.status in (401, 403) and retry:  # companion tokens are short-lived
            self._companion_token = None
            return self._companion_get(path, meeting_id, retry=False)
        if not resp.ok:
            return None
        return resp.json()

    def revision_notes(self, meeting_id: int) -> str | None:
        d = self._companion_get("/api/flashcard/notes", meeting_id)
        return (d or {}).get("notes") or None

    def flashcards(self, meeting_id: int) -> list[dict]:
        d = self._companion_get("/api/flashcard/", meeting_id)
        return (d or {}).get("flashcards") or []
