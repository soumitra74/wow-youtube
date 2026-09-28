from __future__ import annotations

from wow_core import chroma_store


def test_delete_summary_removes_video_id(monkeypatch) -> None:
    class FakeCollection:
        def __init__(self) -> None:
            self.deleted_ids: list[str] = []

        def delete(self, *, ids: list[str]) -> None:
            self.deleted_ids.extend(ids)

    collection = FakeCollection()
    monkeypatch.setattr(chroma_store, "_client", lambda: collection)

    chroma_store.delete_summary("vid-delete")

    assert collection.deleted_ids == ["vid-delete"]
