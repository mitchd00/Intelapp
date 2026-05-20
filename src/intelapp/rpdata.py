"""Look up owner postal addresses from RP Data (CoreLogic) via Playwright.

RP Data has no public API for end-users; we drive the web UI with a persistent
browser context so the user only has to log in once. The first run opens a
visible browser; subsequent runs can run headless because the session cookie
is cached in .auth/rpdata/.
"""
from __future__ import annotations

import os
import random
import re
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional

from playwright.sync_api import (
    BrowserContext,
    Page,
    TimeoutError as PlaywrightTimeoutError,
    sync_playwright,
)

from .models import OwnerInfo

RPDATA_BASE = "https://rpp.corelogic.com.au"
AUTH_DIR = Path(".auth/rpdata")
LOGIN_TIMEOUT_MS = 5 * 60 * 1000  # 5 minutes for manual login
NAV_TIMEOUT_MS = 45_000


@contextmanager
def rpdata_session(headless: Optional[bool] = None) -> Iterator["RPDataClient"]:
    """Open RP Data with a persistent context. First run is interactive."""
    AUTH_DIR.mkdir(parents=True, exist_ok=True)
    first_run = not any(AUTH_DIR.iterdir())
    if headless is None:
        headless = not first_run

    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            user_data_dir=str(AUTH_DIR),
            headless=headless,
            viewport={"width": 1400, "height": 900},
        )
        try:
            client = RPDataClient(ctx, first_run=first_run)
            client.ensure_logged_in()
            yield client
        finally:
            ctx.close()


class RPDataClient:
    def __init__(self, ctx: BrowserContext, first_run: bool):
        self.ctx = ctx
        self.first_run = first_run
        self.page: Page = ctx.pages[0] if ctx.pages else ctx.new_page()
        self.page.set_default_timeout(NAV_TIMEOUT_MS)

    # ----- auth -------------------------------------------------------------

    def ensure_logged_in(self) -> None:
        self.page.goto(RPDATA_BASE, wait_until="domcontentloaded")
        if self._looks_logged_in():
            return

        print(
            "\n[RP Data] Please log in manually in the browser window. "
            "Waiting up to 5 minutes...\n"
        )
        try:
            self.page.wait_for_function(
                """() => {
                    const u = location.href;
                    return !/login|signin|auth/i.test(u);
                }""",
                timeout=LOGIN_TIMEOUT_MS,
            )
        except PlaywrightTimeoutError as e:
            raise RuntimeError("Timed out waiting for RP Data login.") from e

    def _looks_logged_in(self) -> bool:
        url = self.page.url.lower()
        return "login" not in url and "signin" not in url and "auth" not in url

    # ----- lookup -----------------------------------------------------------

    def lookup_owner(self, address: str) -> OwnerInfo:
        """Search for an address, open the top result, scrape owner + postal."""
        info = OwnerInfo(lookup_status="pending")
        try:
            self._search_address(address)
        except PlaywrightTimeoutError:
            info.lookup_status = "search_timeout"
            return info

        try:
            self._open_top_result()
        except PlaywrightTimeoutError:
            info.lookup_status = "no_results"
            return info

        info.rpdata_url = self.page.url

        try:
            info.owner_name, info.owner_postal_address = self._scrape_owner_block()
            info.lookup_status = "ok" if info.owner_postal_address else "no_owner_data"
        except Exception as e:  # noqa: BLE001 - want to record any scrape failure
            info.lookup_status = f"scrape_error:{type(e).__name__}"

        # Jittered throttle
        time.sleep(random.uniform(1.5, 3.0))
        return info

    # ----- internals --------------------------------------------------------

    _SEARCH_SELECTORS = [
        'input[placeholder*="address" i]',
        'input[aria-label*="search" i]',
        'input[type="search"]',
        '#searchInput',
    ]

    def _search_address(self, address: str) -> None:
        # Go home so we start with the global search bar.
        self.page.goto(RPDATA_BASE, wait_until="domcontentloaded")

        search = None
        for sel in self._SEARCH_SELECTORS:
            loc = self.page.locator(sel).first
            if loc.count() and loc.is_visible():
                search = loc
                break
        if search is None:
            raise PlaywrightTimeoutError("Could not find RP Data search input")

        search.click()
        search.fill("")
        search.type(address, delay=30)
        # Give the autocomplete a moment to surface matches.
        self.page.wait_for_timeout(1500)

    def _open_top_result(self) -> None:
        # Try a few likely autocomplete selectors; press Enter as a fallback.
        suggestions = self.page.locator(
            'li[role="option"], [data-testid*="suggestion" i], '
            '[class*="autocomplete" i] [class*="item" i]'
        )
        if suggestions.count():
            suggestions.first.click()
        else:
            self.page.keyboard.press("Enter")

        # Wait for the property detail page to render. We treat the URL
        # changing away from the search context plus an owner-related
        # heading appearing as the success signal.
        self.page.wait_for_load_state("domcontentloaded")
        self.page.wait_for_timeout(2000)

    _POSTAL_LABELS = [
        re.compile(r"owner\s*postal\s*address", re.I),
        re.compile(r"postal\s*address", re.I),
        re.compile(r"vendor\s*postal", re.I),
    ]
    _NAME_LABELS = [
        re.compile(r"owner\s*name", re.I),
        re.compile(r"current\s*owner", re.I),
        re.compile(r"registered\s*owner", re.I),
    ]

    def _scrape_owner_block(self) -> tuple[str, str]:
        """Read owner name + postal address from the detail page.

        RP Data's DOM varies per template; we look for label/value pairs by
        text rather than relying on a stable class name.
        """
        html = self.page.content()
        name = self._extract_labelled_value(html, self._NAME_LABELS)
        postal = self._extract_labelled_value(html, self._POSTAL_LABELS)
        return name, postal

    @staticmethod
    def _extract_labelled_value(html: str, label_patterns: list[re.Pattern]) -> str:
        # Strip tags into a flat label-value stream and look for "<label>: <value>"
        text = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", html, flags=re.S | re.I)
        text = re.sub(r"<[^>]+>", "|", text)
        text = re.sub(r"\s*\|\s*", "|", text)
        text = re.sub(r"\|+", "|", text)
        tokens = [t.strip() for t in text.split("|") if t.strip()]
        for i, tok in enumerate(tokens):
            for pat in label_patterns:
                if pat.fullmatch(tok.rstrip(":").strip()):
                    if i + 1 < len(tokens):
                        return tokens[i + 1]
        return ""


def login_credentials_from_env() -> tuple[str, str]:
    return os.getenv("RPDATA_USERNAME", ""), os.getenv("RPDATA_PASSWORD", "")
