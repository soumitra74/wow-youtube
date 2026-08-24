from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from wow_core import settings


def load_channels(path: Path | None = None) -> list[dict[str, str]]:
    channels_path = path or settings.CHANNELS_PATH
    if not channels_path.exists():
        return []
    raw = json.loads(channels_path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError("channels.json must be a JSON array")
    cleaned: list[dict[str, str]] = []
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError("each channel must be an object")
        channel_id = str(item.get("channel_id") or "").strip()
        name = str(item.get("channel_name") or "").strip()
        if not channel_id or not name:
            raise ValueError("each channel needs channel_id and channel_name")
        cleaned.append({"channel_id": channel_id, "channel_name": name})
    return cleaned
