from __future__ import annotations

from pathlib import Path

from wow_core.digest import render_digest, write_digest


def test_render_digest_empty() -> None:
    text = render_digest([], run_date="2026-08-22")
    assert "0 videos processed" in text
    assert "No new videos" in text


def test_render_digest_includes_takeaways() -> None:
    text = render_digest(
        [
            {
                "title": "Agents",
                "channel_name": "ML Weekly",
                "published_date": "2026-08-21",
                "relevance": "high",
                "topics": ["ai-agents", "llms"],
                "url": "https://www.youtube.com/watch?v=abc",
                "summary": "A practical agent walkthrough.",
                "key_takeaways": ["Use tools", "Keep state small"],
            }
        ],
        run_date="2026-08-22",
    )
    assert "## Agents" in text
    assert "Use tools" in text
    assert "ai-agents" in text


def test_write_digest_creates_file(tmp_path: Path) -> None:
    path = write_digest([], dest_dir=tmp_path)
    assert path.exists()
    assert path.read_text(encoding="utf-8").startswith("# YouTube digest")
