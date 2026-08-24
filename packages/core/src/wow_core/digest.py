from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from wow_core import settings


def render_digest(videos: list[dict[str, Any]], *, run_date: str | None = None) -> str:
    day = run_date or datetime.now(timezone.utc).date().isoformat()
    lines = [
        f"# YouTube digest — {day}",
        "",
        f"{len(videos)} video{'s' if len(videos) != 1 else ''} processed.",
        "",
    ]
    if not videos:
        lines.append("_No new videos this run._")
        lines.append("")
        return "\n".join(lines)

    for video in videos:
        topics = ", ".join(video.get("topics") or [])
        takeaways = video.get("key_takeaways") or []
        lines.extend(
            [
                f"## {video['title']}",
                "",
                f"- Channel: {video.get('channel_name', '')}",
                f"- Published: {video.get('published_date', '')}",
                f"- Relevance: {video.get('relevance', '')}",
                f"- Topics: {topics}",
                f"- URL: {video['url']}",
                "",
                video.get("summary", ""),
                "",
            ]
        )
        if takeaways:
            lines.append("Key takeaways:")
            lines.extend(f"- {item}" for item in takeaways)
            lines.append("")
    return "\n".join(lines)


def write_digest(videos: list[dict[str, Any]], *, dest_dir: Path | None = None) -> Path:
    settings.ensure_data_dirs()
    day = datetime.now(timezone.utc).date().isoformat()
    directory = dest_dir or settings.DIGESTS_DIR
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{day}.md"
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    rendered = render_digest(videos, run_date=day)
    if existing.strip() and videos:
        path.write_text(existing.rstrip() + "\n\n" + _without_header(rendered), encoding="utf-8")
    else:
        path.write_text(rendered, encoding="utf-8")
    return path


def _without_header(markdown: str) -> str:
    parts = markdown.split("\n", 3)
    return parts[-1] if len(parts) >= 4 else markdown
