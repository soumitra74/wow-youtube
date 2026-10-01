from __future__ import annotations

import re
from pathlib import Path


def load_template(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def render_template(template: str, **values: str) -> str:
    """Replace {name} placeholders without interpreting JSON braces.

    Inserted values are not scanned again, so a fact containing ``{title}``
    stays literal.
    """
    if not values:
        return template
    keys = sorted(values, key=len, reverse=True)
    pattern = re.compile("|".join(re.escape("{" + key + "}") for key in keys))
    return pattern.sub(lambda match: values[match.group(0)[1:-1]], template)
