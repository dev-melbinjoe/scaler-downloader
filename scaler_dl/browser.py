"""Chrome automation with a persistent profile, so the Scaler login survives between runs.

You always log in yourself in the window this opens; the tool never sees or stores your password.

Never use headless mode with the real profile: Scaler sees a different browser ("HeadlessChrome"),
treats it as another device and ends the saved login (seen 2026-10-30).
"""

from __future__ import annotations

import time
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterator

from playwright.sync_api import BrowserContext, Error as PlaywrightError, sync_playwright

from .config import PROFILE_DIR


@contextmanager
def open_browser(headless: bool = False, profile_dir: Path = PROFILE_DIR) -> Iterator[BrowserContext]:
    profile_dir.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        options = dict(
            user_data_dir=str(profile_dir),
            headless=headless,
            no_viewport=True,
            args=["--start-maximized", "--disable-blink-features=AutomationControlled"],
            ignore_default_args=["--enable-automation"],
            accept_downloads=True,
        )
        try:
            ctx = p.chromium.launch_persistent_context(channel="chrome", **options)
        except PlaywrightError:
            # No system Chrome: fall back to Playwright's Chromium (`python -m playwright install chromium`).
            ctx = p.chromium.launch_persistent_context(**options)
        try:
            yield ctx
        finally:
            try:
                ctx.close()
            except PlaywrightError:
                pass


def wait_until_closed(ctx: BrowserContext, on_tick: Callable[[], None] | None = None, interval_ms: int = 1500) -> None:
    """Block until the user closes every tab, dispatching Playwright events (and `on_tick`) meanwhile."""
    closed = False

    def _closed(*_):
        nonlocal closed
        closed = True

    ctx.on("close", _closed)
    while not closed:
        pages = [pg for pg in ctx.pages if not pg.is_closed()]
        if not pages:
            break
        try:
            pages[0].wait_for_timeout(interval_ms)
        except PlaywrightError:
            # The tab we were waiting on closed; loop again to see if any are left.
            time.sleep(0.2)
            continue
        if on_tick:
            try:
                on_tick()
            except PlaywrightError:
                pass
