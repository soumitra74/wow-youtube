from __future__ import annotations

from pathlib import Path

from wow_core import settings
from wow_core.claude_client import (
    _format_fact_bundles,
    _parse_json,
    _validate_facts,
    _validate_summary,
    extract_verifiable_facts,
    summarize_video,
    verify_video_summary,
)
from wow_core.prompts import load_template, render_template


def test_render_template_ignores_json_braces() -> None:
    template = 'schema: { "summary": string }\nTitle: {title}'
    assert render_template(template, title="Hello") == 'schema: { "summary": string }\nTitle: Hello'


def test_render_template_does_not_rescan_inserted_values() -> None:
    rendered = render_template("Fact: {fact_context}", fact_context="See {title} for details")
    assert rendered == "Fact: See {title} for details"


def test_load_repo_prompt_templates() -> None:
    root = Path(__file__).resolve().parents[1]
    summarize = load_template(root / "config" / "prompts" / "summarize.txt")
    ask = load_template(root / "config" / "prompts" / "ask.txt")
    video_chat = load_template(root / "config" / "prompts" / "video_chat.txt")
    extract_facts = load_template(root / "config" / "prompts" / "extract_facts.txt")
    verify_summary = load_template(root / "config" / "prompts" / "verify_summary.txt")
    assert "{transcript}" in summarize
    assert "{question}" in ask
    assert "{context}" in ask
    assert "{long_summary}" in video_chat
    assert "{web_context}" in video_chat
    assert "{conversation}" in video_chat
    assert "{long_summary}" in extract_facts
    assert "{max_facts}" in extract_facts
    assert "{fact_context}" in verify_summary
    assert "Supported" in verify_summary


def test_parse_and_validate_summary_json() -> None:
    raw = """```json
    {
      "summary": "A tight explanation of RAG failure modes.",
      "long_summary": "A detailed explanation of retrieval, chunking, evaluation, and failure modes.",
      "key_takeaways": ["Chunk size matters", "Eval early"],
      "topics": ["llms", "coding"],
      "estimated_relevance": "HIGH"
    }
    ```"""
    parsed = _validate_summary(_parse_json(raw))
    assert parsed["long_summary"].startswith("A detailed explanation")
    assert parsed["estimated_relevance"] == "high"
    assert parsed["topics"] == ["llms", "coding"]


def test_validate_summary_requires_long_summary() -> None:
    data = {
        "summary": "Short summary.",
        "key_takeaways": ["Concrete point"],
        "topics": ["llms", "coding"],
        "estimated_relevance": "medium",
    }

    try:
        _validate_summary(data)
    except Exception as exc:
        assert str(exc) == "long_summary is missing"
    else:
        raise AssertionError("missing long_summary should be rejected")


def test_summarize_video_allows_enough_output_for_long_summary(monkeypatch) -> None:
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "test-key")
    captured: dict[str, int] = {}

    def complete(_model: str, _prompt: str, *, max_tokens: int) -> str:
        captured["max_tokens"] = max_tokens
        return """{
          "summary": "Short summary.",
          "long_summary": "Detailed summary.",
          "key_takeaways": ["Concrete point"],
          "topics": ["education", "other"],
          "estimated_relevance": "medium"
        }"""

    monkeypatch.setattr("wow_core.claude_client._complete", complete)

    summarize_video(
        title="Title",
        channel_name="Channel",
        published_date="2026-09-17",
        url="https://example.com",
        transcript="Transcript",
    )
    assert captured["max_tokens"] >= 4096


def test_validate_facts_dedupes_and_caps(monkeypatch) -> None:
    monkeypatch.setattr(settings, "VERIFY_MAX_FACTS", 2)
    facts = _validate_facts(
        {
            "facts": [
                "short",
                "Artemis II crew includes Reid Wiseman",
                "  Artemis   II crew includes Reid Wiseman ",
                "Starship reached orbit on flight 4",
                "NASA named the next lunar lander",
            ]
        }
    )
    assert facts == [
        "Artemis II crew includes Reid Wiseman",
        "Starship reached orbit on flight 4",
    ]


def test_extract_verifiable_facts_uses_summary_fields(monkeypatch) -> None:
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "test-key")
    captured: dict[str, str] = {}

    def complete(_model: str, prompt: str, *, max_tokens: int) -> str:
        captured["prompt"] = prompt
        return """{"facts": ["Artemis II crew includes Reid Wiseman"]}"""

    monkeypatch.setattr("wow_core.claude_client._complete", complete)
    facts = extract_verifiable_facts(
        video={
            "title": "Artemis update",
            "channel_name": "NASA",
            "published_date": "2026-01-02",
            "summary": "Crew names.",
            "long_summary": "Reid Wiseman is the commander.",
            "key_takeaways": ["Crew is assigned"],
            "topics": ["space"],
        }
    )
    assert facts == ["Artemis II crew includes Reid Wiseman"]
    assert "Reid Wiseman is the commander." in captured["prompt"]
    assert "Crew is assigned" not in captured["prompt"]
    assert "Topics:" not in captured["prompt"]
    assert "Short summary:" not in captured["prompt"]
    assert "{max_facts}" not in captured["prompt"]


def test_verify_video_summary_groups_snippets_by_claim(monkeypatch) -> None:
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "test-key")
    captured: dict[str, str] = {}

    def complete(_model: str, prompt: str, *, max_tokens: int) -> str:
        captured["prompt"] = prompt
        captured["max_tokens"] = str(max_tokens)
        return "**Supported** — Artemis II crew includes Reid Wiseman."

    monkeypatch.setattr("wow_core.claude_client._complete", complete)
    answer = verify_video_summary(
        video={"title": "Artemis update", "published_date": "2026-01-02", "url": "https://youtu.be/abc"},
        fact_bundles=[
            {
                "fact": "Artemis II crew includes Reid Wiseman",
                "hits": [
                    {
                        "title": "NASA",
                        "url": "https://www.nasa.gov/artemis",
                        "snippet": "Reid Wiseman commands Artemis II.",
                    }
                ],
            },
            {"fact": "The lander is already at the Moon", "hits": []},
        ],
    )
    assert answer.startswith("**Supported**")
    assert "Claim 1: Artemis II crew includes Reid Wiseman" in captured["prompt"]
    assert "https://www.nasa.gov/artemis" in captured["prompt"]
    assert "Claim 2: The lander is already at the Moon" in captured["prompt"]
    assert "Web snippets: (none)" in captured["prompt"]
    assert int(captured["max_tokens"]) >= 3072


def test_verify_video_summary_without_claims_skips_the_model() -> None:
    assert verify_video_summary(video={"title": "T"}, fact_bundles=[]) == (
        "This summary has no specific claims that can be checked against the web."
    )


def test_format_fact_bundles_truncates_long_snippets() -> None:
    text = _format_fact_bundles(
        [{"fact": "A claim long enough", "hits": [{"title": "S", "url": "https://a.test", "snippet": "x" * 800}]}]
    )
    assert "…" in text
    assert "x" * 501 not in text
