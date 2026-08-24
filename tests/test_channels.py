from __future__ import annotations

from pathlib import Path

from wow_core.channels import load_channels


def test_load_channels_empty_file(tmp_path: Path) -> None:
    path = tmp_path / "channels.json"
    path.write_text("[]", encoding="utf-8")
    assert load_channels(path) == []


def test_load_channels_requires_both_fields(tmp_path: Path) -> None:
    path = tmp_path / "channels.json"
    path.write_text('[{"channel_id": "UCabc", "channel_name": "Demo"}]', encoding="utf-8")
    assert load_channels(path) == [{"channel_id": "UCabc", "channel_name": "Demo"}]
