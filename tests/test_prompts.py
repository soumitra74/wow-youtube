from __future__ import annotations

from pathlib import Path

from wow_core.claude_client import _parse_json, _validate_summary
from wow_core.prompts import load_template, render_template


def test_render_template_ignores_json_braces() -> None:
    template = 'schema: { "summary": string }\nTitle: {title}'
    assert render_template(template, title="Hello") == 'schema: { "summary": string }\nTitle: Hello'


def test_load_repo_prompt_templates() -> None:
    root = Path(__file__).resolve().parents[1]
    summarize = load_template(root / "config" / "prompts" / "summarize.txt")
    ask = load_template(root / "config" / "prompts" / "ask.txt")
    assert "{transcript}" in summarize
    assert "{question}" in ask
    assert "{context}" in ask


def test_parse_and_validate_summary_json() -> None:
    raw = """```json
    {
      "summary": "A tight explanation of RAG failure modes.",
      "key_takeaways": ["Chunk size matters", "Eval early"],
      "topics": ["llms", "coding"],
      "estimated_relevance": "HIGH"
    }
    ```"""
    parsed = _validate_summary(_parse_json(raw))
    assert parsed["estimated_relevance"] == "high"
    assert parsed["topics"] == ["llms", "coding"]
