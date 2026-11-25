"""Browser tests for behaviour that only exists in web/app.js.

The API tests cannot see these: the latest request wins when filters change
quickly, any filter/sort change returns to page 1, and the CSV export
downloads through the page (with a toast when it fails).

Optional to install, NOT optional to run before delivery:

    uv pip install -e '.[dev,e2e]'
    uv run --no-sync python -m playwright install chromium
    uv run --no-sync python -m pytest tests/e2e -v

Set KEEPSTACK_E2E_BROWSER_CHANNEL=msedge (or chrome) to drive an installed
browser instead of Playwright's Chromium. Without Playwright the module is
skipped, so the default `pytest` run and CI stay unchanged.
"""
from __future__ import annotations

import csv
import io
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

import pytest

sync_api = pytest.importorskip("playwright.sync_api", reason="e2e extra not installed")
expect = sync_api.expect

ROOT = Path(__file__).resolve().parents[2]
SCENARIO_COUNT = 14  # quiz_data.build_scenario: 1 + TIED_COUNT(12) + 1


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def server():
    """A real uvicorn process on a throwaway data dir, filled by `seed-quiz`."""
    data_dir = tempfile.mkdtemp(prefix="keepstack-e2e-")
    port = _free_port()
    env = {**os.environ, "KEEPSTACK_DATA_DIR": data_dir, "KEEPSTACK_AI_ENABLED": "false",
           "KEEPSTACK_OCR_ENABLED": "false", "KEEPSTACK_HOST": "127.0.0.1", "KEEPSTACK_PORT": str(port)}
    proc = subprocess.Popen([sys.executable, "-m", "keepstack"], cwd=ROOT, env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = f"http://127.0.0.1:{port}"
    try:
        for _ in range(100):
            try:
                urllib.request.urlopen(base + "/healthz", timeout=1)
                break
            except OSError:
                time.sleep(0.1)
        else:
            pytest.fail("keepstack did not start")
        # Startup has created the schema and the admin user; now add the scenario.
        subprocess.run([sys.executable, "-m", "keepstack", "seed-quiz"], cwd=ROOT, env=env,
                       check=True, stdout=subprocess.DEVNULL)
        yield base
    finally:
        proc.terminate()
        proc.wait(timeout=10)


@pytest.fixture(scope="module")
def browser():
    channel = os.environ.get("KEEPSTACK_E2E_BROWSER_CHANNEL") or None
    with sync_api.sync_playwright() as p:
        try:
            b = p.chromium.launch(channel=channel)
        except Exception as exc:  # browser binaries missing
            pytest.skip(f"no browser for Playwright ({exc.__class__.__name__}); "
                        "run `python -m playwright install chromium`")
        yield b
        b.close()


@pytest.fixture
def page(server, browser):
    ctx = browser.new_context(accept_downloads=True)
    pg = ctx.new_page()
    pg.goto(server)
    # Local test instance with the documented bootstrap account (config defaults).
    pg.fill("#login-user", "admin")
    pg.fill("#login-pass", "admin")
    pg.click("#login-form button[type=submit]")
    expect(pg.locator("#result-title")).to_have_text("Library (18)")  # 17 active in Harbor Campaign + 1 in Harbor Archive
    yield pg
    ctx.close()


def _add_tag(page, selector, name):
    page.fill(selector, name)
    page.press(selector, "Enter")


def apply_scenario(page):
    """The README business scenario, entered through the page controls."""
    page.select_option("#f-collection", label="Harbor Campaign")
    page.fill("#search", "harbor")
    page.press("#search", "Enter")
    _add_tag(page, "#f-tags", "approved")
    _add_tag(page, "#f-tags", "Campaign")  # normalised to lower case like stored tags
    _add_tag(page, "#f-exclude", "internal")
    expect(page.locator("#result-title")).to_have_text(f"Results ({SCENARIO_COUNT} matching)")


def titles(page):
    return page.locator("#grid .card .title").all_inner_texts()


# 1) quick changes: only the latest condition is rendered -----------------------

def test_latest_request_wins_when_an_older_response_arrives_late(page):
    held = []

    def hold_lighthouse(route):
        # Park the "lighthouse" search; let every other request through.
        if "q=lighthouse" in route.request.url:
            held.append(route)
        else:
            route.continue_()

    page.route("**/api/assets?*", hold_lighthouse)
    page.fill("#search", "lighthouse")
    page.press("#search", "Enter")
    page.wait_for_timeout(300)
    assert held, "the first search should be in flight"

    page.fill("#search", "ferry")
    page.press("#search", "Enter")
    expect(page.locator("#result-title")).to_have_text("Results (1 matching)")
    assert titles(page) == ["Harbor ferry 港口渡轮"]

    # Now deliver the stale response. The page aborted that request, so it may
    # be refused; either way it must not replace the newer result.
    for route in held:
        try:
            route.fulfill(response=route.fetch())
        except Exception:
            pass
    page.wait_for_timeout(800)
    expect(page.locator("#result-title")).to_have_text("Results (1 matching)")
    assert titles(page) == ["Harbor ferry 港口渡轮"]
    expect(page.locator(".chip", has_text='Keyword: "ferry"')).to_be_visible()


def test_latest_request_wins_even_if_the_stale_one_is_not_aborted(page):
    """Strip the AbortController signal so the late response really is delivered
    to the page; only the request-sequence check can keep it off screen."""
    page.add_init_script("""
      const realFetch = window.fetch;
      window.fetch = (u, o = {}) => { const { signal, ...rest } = o; return realFetch(u, rest); };
    """)
    page.reload()  # token is in localStorage, so the app signs straight in
    expect(page.locator("#result-title")).to_have_text("Library (18)")

    held = []
    page.route("**/api/assets?*",
               lambda route: held.append(route) if "q=lighthouse" in route.request.url else route.continue_())
    page.fill("#search", "lighthouse")
    page.press("#search", "Enter")
    page.wait_for_timeout(300)
    assert held
    page.fill("#search", "ferry")
    page.press("#search", "Enter")
    expect(page.locator("#result-title")).to_have_text("Results (1 matching)")

    held[0].fulfill(response=held[0].fetch())  # must succeed: nothing aborted it
    page.wait_for_timeout(800)
    expect(page.locator("#result-title")).to_have_text("Results (1 matching)")
    assert titles(page) == ["Harbor ferry 港口渡轮"]


# 2) any filter or sort change goes back to page 1 ------------------------------

def test_filter_and_sort_changes_return_to_first_page(page):
    page.select_option("#pg-size", "12")
    apply_scenario(page)
    page.select_option("#sort", "oldest")
    expect(page.locator("#pg-info")).to_have_text(f"Page 1 of 2 · 1–12 of {SCENARIO_COUNT}")
    assert titles(page)[0] == "Harbor sunrise hero"

    page.click("#pg-next")
    expect(page.locator("#pg-info")).to_have_text(f"Page 2 of 2 · 13–14 of {SCENARIO_COUNT}")
    assert titles(page) == ["Harbor batch shot 12", "Harbor ferry 港口渡轮"]
    expect(page.locator("#pg-next")).to_be_disabled()

    # Sort change -> page 1 of the newest-first order.
    page.select_option("#sort", "newest")
    expect(page.locator("#pg-info")).to_have_text(f"Page 1 of 2 · 1–12 of {SCENARIO_COUNT}")
    assert titles(page)[0] == "Harbor ferry 港口渡轮"
    expect(page.locator("#pg-prev")).to_be_disabled()

    # Filter change from page 2 -> page 1 with the new total.
    page.click("#pg-next")
    expect(page.locator("#pg-info")).to_contain_text("Page 2 of 2")
    _add_tag(page, "#f-exclude", "ferry")
    expect(page.locator("#pg-info")).to_have_text(f"Page 1 of 2 · 1–12 of {SCENARIO_COUNT - 1}")

    # Removing a filter chip from page 2 also resets.
    page.click("#pg-next")
    expect(page.locator("#pg-info")).to_contain_text("Page 2 of 2")
    page.locator(".chip", has_text="Not: ferry").click()
    expect(page.locator("#pg-info")).to_have_text(f"Page 1 of 2 · 1–12 of {SCENARIO_COUNT}")

    # Clear all -> unfiltered library, page 1.
    page.click("#pg-next")
    page.click("#clear-filters")
    expect(page.locator("#result-title")).to_have_text("Library (18)")
    expect(page.locator("#pg-info")).to_have_text("Page 1 of 2 · 1–12 of 18")


# 3) export downloads all matches; failures are reported ------------------------

def test_export_downloads_all_matches_and_reports_failure(page, tmp_path):
    page.select_option("#pg-size", "12")
    apply_scenario(page)
    page.select_option("#sort", "oldest")
    expect(page.locator("#pg-info")).to_contain_text("Page 1 of 2")
    on_screen = titles(page)
    page.click("#pg-next")
    expect(page.locator("#pg-info")).to_contain_text("Page 2 of 2")
    on_screen += titles(page)

    with page.expect_download() as dl:
        page.click("#btn-export")
    download = dl.value
    assert download.suggested_filename.startswith("keepstack-assets-")
    assert download.suggested_filename.endswith(".csv")
    path = tmp_path / download.suggested_filename
    download.save_as(path)
    raw = path.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")
    rows = list(csv.DictReader(io.StringIO(raw.decode("utf-8-sig"), newline="")))
    # Every match, not just the visible page, in the on-screen order.
    assert [r["title"] for r in rows] == on_screen
    assert len(rows) == SCENARIO_COUNT
    expect(page.locator("#toast")).to_have_text("CSV downloaded")
    expect(page.locator("#btn-export")).to_be_enabled()

    # Server error -> toast with the reason, button usable again.
    page.route("**/api/assets/export.csv*", lambda route: route.fulfill(
        status=500, content_type="application/json", body='{"detail": "export exploded"}'))
    page.click("#btn-export")
    expect(page.locator("#toast")).to_have_text("Export failed: export exploded")
    expect(page.locator("#toast")).to_have_class("toast err")
    expect(page.locator("#btn-export")).to_be_enabled()

    # Network failure -> also reported.
    page.unroute("**/api/assets/export.csv*")
    page.route("**/api/assets/export.csv*", lambda route: route.abort())
    page.click("#btn-export")
    expect(page.locator("#toast")).to_contain_text("Export failed:")
