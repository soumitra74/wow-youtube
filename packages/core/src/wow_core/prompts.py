from __future__ import annotations

from pathlib import Path


def load_template(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def render_template(template: str, **values: str) -> str:
    """Replace {name} placeholders without interpreting JSON braces."""
    rendered = template
    for key, value in values.items():
        rendered = rendered.replace("{" + key + "}", value)
    return rendered
