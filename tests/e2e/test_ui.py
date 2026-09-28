from __future__ import annotations

import pytest
from playwright.sync_api import Page, expect

pytestmark = pytest.mark.e2e

ORBITALS_TITLE = "Orbitals Explained"
ROME_TITLE = "The Fall of Rome"


def _open_browse(page: Page, live_server: str) -> None:
    page.goto(live_server)
    expect(page.get_by_role("heading", name="YouTube Digest")).to_be_visible()
    expect(page.get_by_role("link", name=ORBITALS_TITLE)).to_be_visible()
    expect(page.get_by_role("link", name=ROME_TITLE)).to_be_visible()


def test_browse_lists_seeded_videos(page: Page, live_server: str) -> None:
    _open_browse(page, live_server)
    expect(page.locator("#video-rows tr")).to_have_count(2)
    expect(page.locator("#browse-status")).to_contain_text("2 videos")


def test_keyword_filter_narrows_browse_table(page: Page, live_server: str) -> None:
    _open_browse(page, live_server)
    page.locator("#keyword").fill("orbital")
    page.get_by_role("button", name="Apply").click()
    expect(page.get_by_role("link", name=ORBITALS_TITLE)).to_be_visible()
    expect(page.get_by_role("link", name=ROME_TITLE)).to_have_count(0)
    expect(page.locator("#browse-status")).to_contain_text("1 videos")


def test_channel_filter_shows_only_that_channel(page: Page, live_server: str) -> None:
    _open_browse(page, live_server)
    page.locator("#channel").select_option(label="History Hour")
    page.get_by_role("button", name="Apply").click()
    expect(page.get_by_role("link", name=ROME_TITLE)).to_be_visible()
    expect(page.get_by_role("link", name=ORBITALS_TITLE)).to_have_count(0)


def test_video_detail_shows_summary_takeaways_and_transcript(page: Page, live_server: str) -> None:
    _open_browse(page, live_server)
    page.get_by_role("button", name="A walkthrough of orbital mechanics.").click()
    dialog = page.locator("#video-detail")
    expect(dialog).to_be_visible()
    expect(page.locator("#detail-title")).to_have_text(ORBITALS_TITLE)
    expect(page.locator("#detail-long-summary")).to_contain_text("mission planning")
    expect(page.locator("#detail-takeaways")).to_contain_text("Inclination matters")
    page.get_by_text("Full transcript").click()
    expect(page.locator("#detail-transcript")).to_contain_text("Full orbital mechanics transcript.")


def test_watched_toggle_can_filter_browse_results(page: Page, live_server: str) -> None:
    _open_browse(page, live_server)
    row = page.locator("#video-rows tr", has_text=ORBITALS_TITLE)
    with page.expect_response(lambda response: response.request.method == "PATCH" and response.ok):
        row.locator(".watch-toggle").check()
    page.locator("#watched").select_option("true")
    page.get_by_role("button", name="Apply").click()
    expect(page.get_by_role("link", name=ORBITALS_TITLE)).to_be_visible()
    expect(page.get_by_role("link", name=ROME_TITLE)).to_have_count(0)


def test_delete_video_removes_row_after_confirm(page: Page, live_server: str) -> None:
    _open_browse(page, live_server)
    page.get_by_role("button", name="A walkthrough of orbital mechanics.").click()
    expect(page.locator("#detail-delete")).to_be_enabled()
    page.once("dialog", lambda dialog: dialog.accept())
    page.locator("#detail-delete").click()
    expect(page.get_by_role("link", name=ORBITALS_TITLE)).to_have_count(0)
    expect(page.get_by_role("link", name=ROME_TITLE)).to_be_visible()


def test_theme_toggle_switches_color_scheme(page: Page, live_server: str) -> None:
    _open_browse(page, live_server)
    html = page.locator("html")
    before = html.get_attribute("data-theme")
    page.locator("#theme-toggle").click()
    expect(html).not_to_have_attribute("data-theme", before or "")
    after = html.get_attribute("data-theme")
    assert after in {"light", "dark"}


def test_semantic_search_renders_stubbed_hit(page: Page, live_server: str) -> None:
    _open_browse(page, live_server)
    page.locator("nav").get_by_role("button", name="Semantic search").click()
    page.locator("#semantic-q").fill("orbital mechanics")
    page.locator("#semantic-go").click()
    expect(page.locator("#semantic-status")).to_contain_text("results")
    expect(page.locator("#semantic-results")).to_contain_text(ORBITALS_TITLE)


def test_ask_renders_stubbed_answer_and_sources(page: Page, live_server: str) -> None:
    _open_browse(page, live_server)
    page.locator("nav").get_by_role("button", name="Ask").click()
    page.locator("#ask-q").fill("What matters for missions?")
    page.locator("#ask-go").click()
    expect(page.locator("#ask-answer")).to_have_text("Orbitals matter for mission planning.")
    expect(page.locator("#ask-sources")).to_contain_text(ORBITALS_TITLE)


def test_trends_view_loads_topic_series(page: Page, live_server: str) -> None:
    _open_browse(page, live_server)
    with page.expect_response(lambda response: "/api/trends" in response.url and response.ok):
        page.locator("nav").get_by_role("button", name="Trends").click()
    expect(page.locator("#view-trends")).to_be_visible()
    expect(page.locator("#view-browse")).to_be_hidden()
    expect(page.locator("#trend-chart")).to_be_visible()


def test_fetch_latest_shows_stubbed_scan_status(page: Page, live_server: str) -> None:
    _open_browse(page, live_server)
    page.get_by_role("button", name="Fetch latest from YouTube").click()
    expect(page.locator("#fetch-status")).to_contain_text("Scanned 2 of 2 inbox")


def test_fetch_url_requires_a_youtube_url(page: Page, live_server: str) -> None:
    _open_browse(page, live_server)
    page.get_by_role("button", name="Fetch Now").click()
    expect(page.locator("#fetch-status")).to_have_text("Enter a YouTube URL")


def test_fetch_url_shows_stubbed_video_outcome(page: Page, live_server: str) -> None:
    _open_browse(page, live_server)
    page.locator("#fetch-url-input").fill("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
    page.get_by_role("button", name="Fetch Now").click()
    expect(page.locator("#fetch-status")).to_contain_text("Video vid-orbitals: processed")
