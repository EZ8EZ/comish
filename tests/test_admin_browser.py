"""Playwright end-to-end tests of the review UI, in a real browser against a real server."""

import os
from pathlib import Path

import pytest

from comish.admin.app import create_admin_app
from tests.admin_seed import LEAGUE, PASSWORD, seed
from tests.servers import serve

playwright = pytest.importorskip("playwright.sync_api")

# Some sandboxes ship a Chromium that doesn't match the installed Playwright revision.
FALLBACK_CHROMIUM = Path("/opt/pw-browsers/chromium")


@pytest.fixture(scope="module")
def browser():
    with playwright.sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except playwright.Error:
            exe = os.environ.get("COMISH_CHROMIUM") or str(FALLBACK_CHROMIUM)
            if not Path(exe).exists():
                pytest.skip("no Chromium available for Playwright")
            b = p.chromium.launch(executable_path=exe)
        yield b
        b.close()


@pytest.fixture
def site(tmp_path, browser):
    store = seed(tmp_path)
    app = create_admin_app({LEAGUE.slug: LEAGUE}, lambda slug: store, PASSWORD)
    with serve(app) as url:
        context = browser.new_context(http_credentials={"username": "admin", "password": PASSWORD})
        page = context.new_page()
        yield page, url, store
        context.close()
    store.close()


def open_source(page, url, name):
    page.goto(f"{url}/l/football")
    page.get_by_role("link", name=name).click()


def test_dashboard_shows_review_counts(site):
    page, url, _ = site
    page.goto(url)
    page.get_by_role("link", name="Test Dynasty").wait_for()
    assert "0" in page.locator("table").inner_text()  # nothing citable yet


def test_doc_needs_a_date_decision_then_approves(site):
    page, url, store = site
    open_source(page, url, "Constitution")
    page.get_by_role("button", name="Approve source").click()
    assert "set a date or mark it undated" in page.get_by_test_id("error").inner_text()
    assert store.citable_records() == []

    page.get_by_label("Effective date").fill("2025-02-01")
    page.get_by_role("button", name="Set date").click()
    assert "2025-02-01" in page.get_by_test_id("date").inner_text()
    page.get_by_role("button", name="Approve source").click()
    page.get_by_test_id("notice").wait_for()
    assert {r.text for r in store.citable_records()} == {
        "Trades close at the Sleeper trade deadline.",
        "Two taxi slots per team.",
    }


def test_mark_undated_then_reject_one_record(site):
    page, url, store = site
    open_source(page, url, "Constitution")
    page.get_by_role("button", name="Mark undated").click()
    assert "Marked undated" in page.get_by_test_id("date").inner_text()
    page.get_by_role("button", name="Approve source").click()
    taxi = next(r for r in store.citable_records() if r.text.startswith("Two taxi"))
    page.get_by_role("button", name=f"Reject {taxi.label}").click()
    page.get_by_test_id("notice").wait_for()
    assert [r.text for r in store.citable_records()] == [
        "Trades close at the Sleeper trade deadline."
    ]


def test_screenshot_review_shows_disagreement_and_accepts_correction(site):
    page, url, store = site
    open_source(page, url, "IMG_1.png")
    assert "votes_for" in page.get_by_test_id("disagreements").inner_text()
    assert page.locator("img.shot").evaluate("img => img.naturalWidth") == 1

    image = store.source_by_external_id("img1")
    (record,) = store.records_for_source(image.id)
    page.get_by_label(f"Record text for {record.label}").fill(
        "Screenshot IMG_1.png.\nDecision: Expand taxi to 3.\nOutcome: passed\nVotes: 8 for"
    )
    page.get_by_role("button", name="Save correction").click()
    page.get_by_label("Effective date").fill("2024-08-10")
    page.get_by_role("button", name="Set date").click()
    page.get_by_role("button", name="Approve source").click()
    page.get_by_test_id("notice").wait_for()
    (citable,) = [r for r in store.citable_records() if r.source_id == image.id]
    assert citable.text.endswith("Votes: 8 for")


def test_verify_sleeper_field(site):
    page, url, store = site
    page.goto(f"{url}/l/football/fields")
    row = page.get_by_test_id("field-settings.waiver_type")
    assert "rolling waivers" in row.inner_text() and "FAAB bidding" in row.inner_text()
    row.get_by_role("button", name="Verify").click()
    page.get_by_test_id("notice").wait_for()
    seasons = sorted(r.season for r in store.citable_records() if "Waiver type" in r.text)
    assert seasons == ["2024", "2025", "2026"]
