from __future__ import annotations

import pytest
from fastapi import HTTPException

from wow_api import app as api_module


def test_video_detail_returns_transcript_and_long_summary(monkeypatch) -> None:
    detail = {
        "video_id": "vid1",
        "summary": "Short summary.",
        "long_summary": "Detailed summary.",
        "transcript": "Complete transcript.",
    }
    monkeypatch.setattr(api_module, "get_video", lambda video_id: detail if video_id == "vid1" else None)

    assert api_module.api_video("vid1") == detail


def test_video_detail_returns_404_for_unknown_video(monkeypatch) -> None:
    monkeypatch.setattr(api_module, "get_video", lambda _video_id: None)

    with pytest.raises(HTTPException) as exc:
        api_module.api_video("missing")
    assert exc.value.status_code == 404


def test_delete_video_removes_index_before_database(monkeypatch) -> None:
    operations: list[str] = []
    monkeypatch.setattr(api_module, "get_video", lambda _video_id: {"video_id": "vid1"})
    monkeypatch.setattr(
        api_module,
        "delete_summary",
        lambda video_id: operations.append(f"index:{video_id}"),
    )
    monkeypatch.setattr(
        api_module,
        "delete_video",
        lambda video_id: operations.append(f"database:{video_id}") or True,
    )

    response = api_module.api_delete_video("vid1")

    assert response.status_code == 204
    assert operations == ["index:vid1", "database:vid1"]


def test_delete_video_returns_404_for_unknown_video(monkeypatch) -> None:
    monkeypatch.setattr(api_module, "get_video", lambda _video_id: None)

    with pytest.raises(HTTPException) as exc:
        api_module.api_delete_video("missing")

    assert exc.value.status_code == 404


def test_delete_video_keeps_database_when_index_removal_fails(monkeypatch) -> None:
    database_deleted = False
    monkeypatch.setattr(api_module, "get_video", lambda _video_id: {"video_id": "vid1"})

    def fail_index(_video_id: str) -> None:
        raise RuntimeError("index unavailable")

    def record_database_delete(_video_id: str) -> bool:
        nonlocal database_deleted
        database_deleted = True
        return True

    monkeypatch.setattr(api_module, "delete_summary", fail_index)
    monkeypatch.setattr(api_module, "delete_video", record_database_delete)

    with pytest.raises(HTTPException) as exc:
        api_module.api_delete_video("vid1")

    assert exc.value.status_code == 500
    assert database_deleted is False
