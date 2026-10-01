from __future__ import annotations

from wow_core import settings
from wow_core import web_search as ws


def test_build_search_query_adds_recency_for_time_sensitive_questions() -> None:
    query = ws.build_search_query_for_tests(
        "What is the latest update on Starship?",
        {"title": "Starship overview", "topics": ["space"]},
    )
    assert "latest" in query.lower()
    assert "Starship overview" in query
    assert any(ch.isdigit() for ch in query)


def test_authority_ranking_prefers_gov_over_reddit() -> None:
    hits = [
        {"title": "Forum", "url": "https://www.reddit.com/r/space/comments/abc", "snippet": "x"},
        {"title": "NASA", "url": "https://www.nasa.gov/news", "snippet": "y"},
    ]
    ranked = ws._finalize_hits(hits)
    assert ranked[0]["url"].startswith("https://www.nasa.gov")


def test_round_robin_alternates_providers(monkeypatch) -> None:
    monkeypatch.setattr(settings, "TAVILY_API_KEY", "tv")
    monkeypatch.setattr(settings, "FIRECRAWL_API_KEY", "fc")
    ws._rr_counter = 0

    calls: list[str] = []

    def fake_search(provider: str, query: str, question: str) -> tuple[list[dict], str]:
        calls.append(provider)
        return [{"title": "Hit", "url": f"https://{provider}.example.com/a", "snippet": "s"}], provider  # type: ignore[return-value]

    monkeypatch.setattr(ws, "_search_with_provider", fake_search)

    ws.search_web(question="What changed?", video=None)
    ws.search_web(question="What changed?", video=None)

    assert calls == ["tavily", "firecrawl"]


def test_single_provider_when_only_one_key(monkeypatch) -> None:
    monkeypatch.setattr(settings, "TAVILY_API_KEY", "tv")
    monkeypatch.setattr(settings, "FIRECRAWL_API_KEY", "")
    ws._rr_counter = 0

    def fake_search(provider: str, query: str, question: str) -> tuple[list[dict], str]:
        assert provider == "tavily"
        return [{"title": "Hit", "url": "https://nasa.gov/a", "snippet": "s"}], provider  # type: ignore[return-value]

    monkeypatch.setattr(ws, "_search_with_provider", fake_search)
    hits, provider = ws.search_web(question="Current orbit?", video=None)
    assert provider == "tavily"
    assert hits


def test_fallback_to_second_provider(monkeypatch) -> None:
    monkeypatch.setattr(settings, "TAVILY_API_KEY", "tv")
    monkeypatch.setattr(settings, "FIRECRAWL_API_KEY", "fc")
    ws._rr_counter = 0

    def fake_search(provider: str, query: str, question: str) -> tuple[list[dict], str]:
        if provider == "tavily":
            return [], provider  # type: ignore[return-value]
        return [{"title": "Hit", "url": "https://apnews.com/a", "snippet": "s"}], provider  # type: ignore[return-value]

    monkeypatch.setattr(ws, "_search_with_provider", fake_search)
    hits, provider = ws.search_web(question="Latest news?", video=None)
    assert provider == "firecrawl"
    assert hits[0]["url"].startswith("https://apnews.com")
