"""Real, browser-driven behavior that no pure-HTML assertion can prove:
the notifications popover's click-outside-to-close JS, and that a click
on its own icon still toggles it normally. Uses the real, manually-
staged Chrome-for-Testing binary this project's own visual-QA passes
already rely on (Playwright's own downloader fails to reach its CDN in
this environment, confirmed earlier this session) -- skips cleanly,
not a failure, on any machine where that binary isn't staged at the
same real path.
"""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from config import Settings
from monitoring.live_status_server import build_live_status_server
from storage.database import Database

pytest.importorskip("playwright")
from playwright.sync_api import sync_playwright

_CHROME_PATH = Path(
    "C:/Users/prasanth/AppData/Local/ms-playwright/chromium-1243/chrome-win64/chrome.exe"
)

pytestmark = pytest.mark.skipif(
    not _CHROME_PATH.exists(),
    reason=f"real Chrome-for-Testing binary not staged at {_CHROME_PATH} on this machine",
)


@pytest.fixture
def dashboard_server(tmp_path):
    settings = Settings(database_path=tmp_path / "paper.db")
    database = Database(settings.database_path)
    database.initialize()
    server = build_live_status_server(database, settings, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.fixture
def page(dashboard_server):
    port = dashboard_server.server_address[1]
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=str(_CHROME_PATH))
        pg = browser.new_page(viewport={"width": 1440, "height": 900})
        pg.goto(f"http://127.0.0.1:{port}/dashboard", wait_until="networkidle", timeout=20000)
        yield pg
        browser.close()


def _popover_open_and_visible(page) -> bool:
    return page.eval_on_selector(
        "#notifications",
        "el => el.hasAttribute('open') && "
        "getComputedStyle(el.querySelector('.popover-content')).display !== 'none'",
    )


def test_clicking_outside_the_open_popover_closes_it(page):
    """The real, explicit requirement under test: a click anywhere else
    on the real page must close the real, already-open popover -- not
    just that a click listener exists somewhere."""
    page.click("#notifications summary")
    assert _popover_open_and_visible(page) is True

    # A real click well outside the <details> element -- the page body,
    # near "MARKET CLOSED".
    page.click("text=MARKET CLOSED", timeout=5000)

    assert _popover_open_and_visible(page) is False
    assert page.eval_on_selector("#notifications", "el => el.hasAttribute('open')") is False


def test_clicking_the_icon_itself_still_toggles_normally(page):
    """The click-outside listener must never interfere with the native
    open/close toggle on the icon itself."""
    assert _popover_open_and_visible(page) is False

    page.click("#notifications summary")
    assert _popover_open_and_visible(page) is True

    page.click("#notifications summary")
    assert _popover_open_and_visible(page) is False


def test_clicking_real_chrome_text_no_longer_produces_a_text_selection_caret(page):
    """The real bug this fixes, reproduced exactly as it was found: a
    plain click on real UI chrome text ("MARKET CLOSED") used to leave
    window.getSelection() in a real "Caret" state (the browser's own
    native text-insertion-point indicator) because that text was
    genuinely selectable (user-select: auto, the default) -- not a
    contenteditable/input bug, confirmed separately. user-select: none
    on chrome classes prevents the browser from placing that caret at
    all."""
    market_status = page.locator("text=MARKET CLOSED").last
    box = market_status.bounding_box()
    page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)

    selection_type = page.evaluate("window.getSelection().type")
    assert selection_type != "Caret"


def test_real_values_stay_selectable_after_the_caret_fix(page):
    """The fix must be scoped, not blanket -- a real numeric value
    (the hero NIFTY price / no-data label) must still be genuinely
    selectable, since a person may want to copy it."""
    computed_user_select = page.eval_on_selector(".hero-ltp", "el => getComputedStyle(el).userSelect")
    assert computed_user_select == "text"
