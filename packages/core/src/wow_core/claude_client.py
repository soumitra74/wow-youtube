from __future__ import annotations

import json
import logging
import re
from typing import Any, Literal

from wow_core import settings
from wow_core.prompts import load_template, render_template

logger = logging.getLogger(__name__)

Relevance = Literal["high", "medium", "low"]


class SummarizeError(Exception):
    pass


def summarize_video(
    *,
    title: str,
    channel_name: str,
    published_date: str,
    url: str,
    transcript: str,
) -> dict[str, Any]:
    if not settings.ANTHROPIC_API_KEY:
        raise SummarizeError("ANTHROPIC_API_KEY is not set")

    template = load_template(settings.SUMMARIZE_PROMPT_PATH)
    prompt = render_template(
        template,
        title=title,
        channel_name=channel_name,
        published_date=published_date,
        url=url,
        transcript=transcript,
    )
    raw = _complete(settings.CLAUDE_SUMMARIZE_MODEL, prompt, max_tokens=4096)
    parsed = _parse_json(raw)
    return _validate_summary(parsed)


def ask_across_videos(*, question: str, videos: list[dict[str, Any]]) -> str:
    if not settings.ANTHROPIC_API_KEY:
        raise SummarizeError("ANTHROPIC_API_KEY is not set")
    if not videos:
        return "The archive does not contain any matching videos yet."

    context_blocks = []
    for video in videos:
        topics = ", ".join(video.get("topics") or [])
        context_blocks.append(
            "\n".join(
                [
                    f"Title: {video['title']}",
                    f"Channel: {video.get('channel_name', '')}",
                    f"Published: {video.get('published_date', '')}",
                    f"URL: {video['url']}",
                    f"Topics: {topics}",
                    f"Summary: {video['summary']}",
                ]
            )
        )
    template = load_template(settings.ASK_PROMPT_PATH)
    prompt = render_template(
        template,
        question=question.strip(),
        context="\n\n---\n\n".join(context_blocks),
    )
    return _complete(settings.CLAUDE_ASK_MODEL, prompt, max_tokens=2048)


_NO_CHECKABLE_FACTS = "This summary has no specific claims that can be checked against the web."
_SNIPPET_CHARS = 500
_MIN_FACT_CHARS = 12
_MAX_FACT_CHARS = 400


def extract_verifiable_facts(*, video: dict[str, Any]) -> list[str]:
    """Split a video summary into atomic claims that can each be web-searched."""
    if not settings.ANTHROPIC_API_KEY:
        raise SummarizeError("ANTHROPIC_API_KEY is not set")

    template = load_template(settings.EXTRACT_FACTS_PROMPT_PATH)
    prompt = render_template(
        template,
        title=str(video.get("title") or ""),
        channel_name=str(video.get("channel_name") or ""),
        published_date=str(video.get("published_date") or ""),
        long_summary=str(video.get("long_summary") or video.get("summary") or ""),
        max_facts=str(settings.VERIFY_MAX_FACTS),
    )
    raw = _complete(settings.CLAUDE_SUMMARIZE_MODEL, prompt, max_tokens=1024)
    facts = _validate_facts(_parse_json(raw))
    logger.info("Extracted %d verifiable claims", len(facts))
    return facts


def verify_video_summary(
    *,
    video: dict[str, Any],
    fact_bundles: list[dict[str, Any]],
) -> str:
    """Judge each claim using only the web snippets retrieved for that claim."""
    if not fact_bundles:
        return _NO_CHECKABLE_FACTS
    if not settings.ANTHROPIC_API_KEY:
        raise SummarizeError("ANTHROPIC_API_KEY is not set")

    template = load_template(settings.VERIFY_SUMMARY_PROMPT_PATH)
    prompt = render_template(
        template,
        title=str(video.get("title") or ""),
        channel_name=str(video.get("channel_name") or ""),
        published_date=str(video.get("published_date") or ""),
        url=str(video.get("url") or ""),
        fact_context=_format_fact_bundles(fact_bundles),
    )
    return _complete(settings.CLAUDE_ASK_MODEL, prompt, max_tokens=3072)


def _validate_facts(data: dict[str, Any]) -> list[str]:
    raw = data.get("facts")
    if raw is None:
        raw = data.get("claims") or []
    if not isinstance(raw, list):
        raise SummarizeError("facts must be an array")

    facts: list[str] = []
    seen: set[str] = set()
    limit = max(settings.VERIFY_MAX_FACTS, 1)
    for item in raw:
        fact = " ".join(str(item).split())
        if len(fact) < _MIN_FACT_CHARS or len(fact) > _MAX_FACT_CHARS:
            continue
        key = fact.casefold()
        if key in seen:
            continue
        seen.add(key)
        facts.append(fact)
        if len(facts) >= limit:
            break
    return facts


def _format_fact_bundles(bundles: list[dict[str, Any]]) -> str:
    blocks: list[str] = []
    for index, bundle in enumerate(bundles, start=1):
        fact = str(bundle.get("fact") or "").strip()
        lines = [f"Claim {index}: {fact}"]
        hits = bundle.get("hits") or []
        if not hits:
            lines.append("Web snippets: (none)")
        else:
            lines.append("Web snippets:")
            for hit in hits:
                title = str(hit.get("title") or hit.get("url") or "Source").strip()
                url = str(hit.get("url") or "").strip()
                snippet = " ".join(str(hit.get("snippet") or "").split())
                if len(snippet) > _SNIPPET_CHARS:
                    snippet = snippet[:_SNIPPET_CHARS].rstrip() + "…"
                published = str(hit.get("published_date") or "").strip()
                date_line = f"\n  Published: {published}" if published else ""
                lines.append(
                    f"- Title: {title}\n  URL: {url}{date_line}\n  Snippet: {snippet or '(empty)'}"
                )
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def ask_about_video(
    *,
    video: dict[str, Any],
    messages: list[dict[str, str]],
    web_hits: list[dict[str, Any]] | None = None,
) -> str:
    if not settings.ANTHROPIC_API_KEY:
        raise SummarizeError("ANTHROPIC_API_KEY is not set")
    if not messages:
        raise SummarizeError("messages are required")

    takeaways = video.get("key_takeaways") or []
    takeaway_lines = "\n".join(f"- {item}" for item in takeaways) if takeaways else "(none)"
    topics = ", ".join(video.get("topics") or [])

    web_blocks = []
    for hit in web_hits or []:
        title = str(hit.get("title") or hit.get("url") or "Source").strip()
        url = str(hit.get("url") or "").strip()
        snippet = str(hit.get("snippet") or "").strip()
        published = str(hit.get("published_date") or "").strip()
        date_line = f"Published: {published}\n" if published else ""
        web_blocks.append(f"Title: {title}\nURL: {url}\n{date_line}Snippet: {snippet}")
    web_context = "\n\n---\n\n".join(web_blocks) if web_blocks else "(none)"

    conversation_lines = []
    for msg in messages:
        role = str(msg.get("role") or "").strip().lower()
        content = str(msg.get("content") or "").strip()
        if role not in {"user", "assistant"} or not content:
            continue
        label = "User" if role == "user" else "Assistant"
        conversation_lines.append(f"{label}: {content}")
    conversation = "\n\n".join(conversation_lines) if conversation_lines else "(none)"

    template = load_template(settings.VIDEO_CHAT_PROMPT_PATH)
    prompt = render_template(
        template,
        title=video.get("title", ""),
        channel_name=video.get("channel_name", ""),
        published_date=video.get("published_date", ""),
        url=video.get("url", ""),
        topics=topics,
        summary=video.get("summary", ""),
        long_summary=video.get("long_summary") or video.get("summary") or "",
        key_takeaways=takeaway_lines,
        web_context=web_context,
        conversation=conversation,
    )
    return _complete(settings.CLAUDE_ASK_MODEL, prompt, max_tokens=2048)


def _complete(model: str, prompt: str, *, max_tokens: int) -> str:
    import anthropic

    client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
    message = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    parts = [block.text for block in message.content if getattr(block, "type", "") == "text"]
    text = "\n".join(parts).strip()
    if not text:
        raise SummarizeError("Claude returned an empty response")
    return text


def _parse_json(raw: str) -> dict[str, Any]:
    cleaned = raw.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", cleaned, re.DOTALL)
    if fenced:
        cleaned = fenced.group(1)
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start == -1 or end == -1:
            raise SummarizeError("Claude response was not valid JSON")
        data = json.loads(cleaned[start : end + 1])
    if not isinstance(data, dict):
        raise SummarizeError("Claude JSON was not an object")
    return data


def _validate_summary(data: dict[str, Any]) -> dict[str, Any]:
    summary = str(data.get("summary") or "").strip()
    if not summary:
        raise SummarizeError("summary is missing")

    long_summary = str(data.get("long_summary") or "").strip()
    if not long_summary:
        raise SummarizeError("long_summary is missing")

    takeaways = data.get("key_takeaways") or []
    if not isinstance(takeaways, list):
        raise SummarizeError("key_takeaways must be an array")
    takeaways = [str(item).strip() for item in takeaways if str(item).strip()]

    topics = data.get("topics") or []
    if not isinstance(topics, list):
        raise SummarizeError("topics must be an array")
    topics = [str(item).strip().lower() for item in topics if str(item).strip()]
    topics = topics[:4]
    if len(topics) < 2:
        raise SummarizeError("topics must contain 2-4 tags")

    relevance = str(data.get("estimated_relevance") or "").strip().lower()
    if relevance not in {"high", "medium", "low"}:
        raise SummarizeError("estimated_relevance must be high, medium, or low")

    return {
        "summary": summary,
        "long_summary": long_summary,
        "key_takeaways": takeaways,
        "topics": topics,
        "estimated_relevance": relevance,
    }
