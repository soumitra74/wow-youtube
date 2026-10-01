from __future__ import annotations

import json
import logging
import re
import threading
from datetime import datetime, timezone
from typing import Any, Literal
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from wow_core import settings

logger = logging.getLogger(__name__)

Provider = Literal["tavily", "firecrawl"]

_rr_lock = threading.Lock()
_rr_counter = 0

_TIME_SENSITIVE = re.compile(
    r"\b(latest|current|today|recent|recently|now|update|updated|since|news|"
    r"this week|this month|breaking|202[4-9]|203\d)\b",
    re.IGNORECASE,
)

_AUTHORITY_BOOST: tuple[tuple[str, int], ...] = (
    (".gov", 40),
    (".edu", 35),
    ("reuters.com", 30),
    ("apnews.com", 30),
    ("bbc.com", 25),
    ("bbc.co.uk", 25),
    ("nature.com", 28),
    ("science.org", 28),
    ("arxiv.org", 28),
    ("nih.gov", 32),
    ("who.int", 32),
    ("docs.", 25),
    ("developer.", 22),
    ("wikipedia.org", 15),
)

_AUTHORITY_PENALTY: tuple[tuple[str, int], ...] = (
    ("reddit.com", -35),
    ("quora.com", -35),
    ("pinterest.com", -30),
    ("medium.com", -10),
    ("facebook.com", -25),
    ("twitter.com", -15),
    ("x.com", -15),
    ("tiktok.com", -25),
)

_MIN_AUTHORITY_SCORE = -5


def search_web(*, question: str, video: dict[str, Any] | None = None) -> tuple[list[dict[str, Any]], str | None]:
    """Return ranked web hits and the provider name used (if any)."""
    query = _build_search_query(question.strip(), video)
    if not query:
        return [], None

    providers = _configured_providers()
    if not providers:
        return [], None

    primary = _pick_provider(providers)
    hits, provider = _search_with_provider(primary, query, question)
    if hits:
        return _finalize_hits(hits), provider

    fallback = _other_provider(primary, providers)
    if fallback:
        logger.warning("Web search provider %s failed or returned no hits; trying %s", primary, fallback)
        hits, provider = _search_with_provider(fallback, query, question)
        if hits:
            return _finalize_hits(hits), provider

    return [], primary


def _configured_providers() -> list[Provider]:
    out: list[Provider] = []
    if settings.TAVILY_API_KEY:
        out.append("tavily")
    if settings.FIRECRAWL_API_KEY:
        out.append("firecrawl")
    return out


def _pick_provider(providers: list[Provider]) -> Provider:
    global _rr_counter
    if len(providers) == 1:
        return providers[0]
    with _rr_lock:
        provider = providers[_rr_counter % len(providers)]
        _rr_counter += 1
    return provider


def _other_provider(current: Provider, providers: list[Provider]) -> Provider | None:
    for provider in providers:
        if provider != current:
            return provider
    return None


def _build_search_query(question: str, video: dict[str, Any] | None) -> str:
    parts = [question]
    if video:
        title = str(video.get("title") or "").strip()
        if title:
            parts.append(title)
        topics = video.get("topics") or []
        if topics:
            parts.append(", ".join(str(t) for t in topics[:4]))
    query = " — ".join(p for p in parts if p)

    year = str(datetime.now(timezone.utc).year)
    if _TIME_SENSITIVE.search(question):
        query = f"{query} latest {year}"
    elif _looks_factual(question):
        query = f"{query} {year}"

    return query.strip()


def _looks_factual(question: str) -> bool:
    lowered = question.lower()
    if "?" not in question:
        return False
    starters = ("what", "who", "when", "where", "how", "why", "is ", "are ", "did ", "does ")
    return lowered.startswith(starters)


def _search_with_provider(
    provider: Provider,
    query: str,
    question: str,
) -> tuple[list[dict[str, Any]], Provider]:
    try:
        if provider == "tavily":
            return _search_tavily(query, question), provider
        return _search_firecrawl(query), provider
    except (HTTPError, URLError, TimeoutError, OSError, ValueError, KeyError, TypeError) as exc:
        logger.warning("Web search via %s failed: %s", provider, exc)
        return [], provider


def _search_tavily(query: str, question: str) -> list[dict[str, Any]]:
    topic = "news" if _TIME_SENSITIVE.search(question) else "general"
    body: dict[str, Any] = {
        "api_key": settings.TAVILY_API_KEY,
        "query": query,
        "max_results": max(settings.WEB_SEARCH_MAX_RESULTS * 2, 8),
        "search_depth": "advanced",
        "days": settings.WEB_SEARCH_RECENCY_DAYS,
        "topic": topic,
    }
    if settings.WEB_SEARCH_INCLUDE_DOMAINS:
        body["include_domains"] = settings.WEB_SEARCH_INCLUDE_DOMAINS
    if settings.WEB_SEARCH_EXCLUDE_DOMAINS:
        body["exclude_domains"] = settings.WEB_SEARCH_EXCLUDE_DOMAINS

    payload = _post_json("https://api.tavily.com/search", body)
    results = payload.get("results") or []
    hits: list[dict[str, Any]] = []
    for item in results:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "").strip()
        if not url:
            continue
        hits.append(
            {
                "title": str(item.get("title") or url).strip(),
                "url": url,
                "snippet": str(item.get("content") or "").strip(),
                "published_date": str(item.get("published_date") or "").strip() or None,
            }
        )
    return hits


def _search_firecrawl(query: str) -> list[dict[str, Any]]:
    body: dict[str, Any] = {
        "query": query,
        "limit": max(settings.WEB_SEARCH_MAX_RESULTS * 2, 8),
    }
    if settings.WEB_SEARCH_INCLUDE_DOMAINS:
        body["includeDomains"] = settings.WEB_SEARCH_INCLUDE_DOMAINS
    if settings.WEB_SEARCH_EXCLUDE_DOMAINS:
        body["excludeDomains"] = settings.WEB_SEARCH_EXCLUDE_DOMAINS

    payload = _post_json(
        "https://api.firecrawl.dev/v1/search",
        body,
        headers={"Authorization": f"Bearer {settings.FIRECRAWL_API_KEY}"},
    )
    data = payload.get("data") or payload.get("results") or []
    hits: list[dict[str, Any]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or item.get("link") or "").strip()
        if not url:
            continue
        hits.append(
            {
                "title": str(item.get("title") or url).strip(),
                "url": url,
                "snippet": str(item.get("description") or item.get("snippet") or "").strip(),
                "published_date": str(item.get("publishedDate") or item.get("published_date") or "").strip()
                or None,
            }
        )
    return hits


def _post_json(
    url: str,
    body: dict[str, Any],
    *,
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    data = json.dumps(body).encode("utf-8")
    req_headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if headers:
        req_headers.update(headers)
    request = Request(url, data=data, headers=req_headers, method="POST")
    with urlopen(request, timeout=30) as response:
        raw = response.read().decode("utf-8")
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise ValueError("web search response was not an object")
    return parsed


def _finalize_hits(hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ranked = sorted(
        hits,
        key=lambda hit: (-_authority_score(str(hit.get("url") or "")), str(hit.get("url") or "")),
    )
    strong = [hit for hit in ranked if _authority_score(str(hit.get("url") or "")) >= _MIN_AUTHORITY_SCORE]
    chosen = strong if strong else ranked
    return chosen[: settings.WEB_SEARCH_MAX_RESULTS]


def _authority_score(url: str) -> int:
    host = (urlparse(url).hostname or "").lower()
    if not host:
        return 0
    score = 10
    for needle, delta in _AUTHORITY_BOOST:
        if needle in host:
            score += delta
    for needle, delta in _AUTHORITY_PENALTY:
        if host == needle or host.endswith("." + needle):
            score += delta
    return score


def build_search_query_for_tests(question: str, video: dict[str, Any] | None = None) -> str:
    """Expose query shaping for unit tests."""
    return _build_search_query(question, video)
