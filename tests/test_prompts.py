from __future__ import annotations

from pathlib import Path

from wow_core import settings
from wow_core.claude_client import _parse_json, _validate_summary, summarize_video
from wow_core.prompts import load_template, render_template


def test_render_template_ignores_json_braces() -> None:
    template = 'schema: { "summary": string }\nTitle: {title}'
    assert render_template(template, title="Hello") == 'schema: { "summary": string }\nTitle: Hello'


def test_load_repo_prompt_templates() -> None:
    root = Path(__file__).resolve().parents[1]
    summarize = load_template(root / "config" / "prompts" / "summarize.txt")
    ask = load_template(root / "config" / "prompts" / "ask.txt")
    video_chat = load_template(root / "config" / "prompts" / "video_chat.txt")
    assert "{transcript}" in summarize
    assert "{question}" in ask
    assert "{context}" in ask
    assert "{long_summary}" in video_chat
    assert "{web_context}" in video_chat
    assert "{conversation}" in video_chat


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
