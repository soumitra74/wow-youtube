from __future__ import annotations

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from wow_api import app as api_module
from wow_core.youtube import NotificationAuthError


class _FakeProgress:
    def __init__(
        self,
        *,
        running: bool = False,
        begin_error: RuntimeError | None = None,
    ) -> None:
        self.running = running
        self._begin_error = begin_error
        self.began = False
        self.completed: dict[str, object] | None = None
        self.failed: str | None = None

    def is_running(self) -> bool:
        return self.running

    def begin(self) -> None:
        if self._begin_error is not None:
            raise self._begin_error
        self.began = True
        self.running = True

    def snapshot(self) -> dict[str, object]:
        return {"running": self.running, "message": "idle"}

    def complete(self, result: dict[str, object]) -> None:
        self.completed = result
        self.running = False

    def fail(self, error: str) -> None:
        self.failed = error
        self.running = False


class _NoThread:
    def __init__(self, target=None, args=(), kwargs=None, name=None, daemon=None) -> None:
        self._target = target
        self._args = args

    def start(self) -> None:
        return None


def test_video_detail_returns_transcript_and_long_summary(monkeypatch) -> None:
    detail = {
        "video_id": "vid1",
        "summary": "Short summary.",
        "long_summary": "Detailed summary.",
        "transcript": "Complete transcript.",
    }
    monkeypatch.setattr(api_module, "get_video", lambda video_id: detail if video_id == "vid1" else None)

    assert api_module.api_video_detail("vid1") == detail


def test_video_detail_returns_404_for_unknown_video(monkeypatch) -> None:
    monkeypatch.setattr(api_module, "get_video", lambda _video_id: None)

    with pytest.raises(HTTPException) as exc:
        api_module.api_video_detail("missing")
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

    response = api_module.api_video_delete("vid1")

    assert response.status_code == 204
    assert operations == ["index:vid1", "database:vid1"]


def test_delete_video_returns_404_for_unknown_video(monkeypatch) -> None:
    monkeypatch.setattr(api_module, "get_video", lambda _video_id: None)

    with pytest.raises(HTTPException) as exc:
        api_module.api_video_delete("missing")

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
        api_module.api_video_delete("vid1")

    assert exc.value.status_code == 500
    assert database_deleted is False


def test_video_patch_watched(monkeypatch) -> None:
    monkeypatch.setattr(api_module, "set_watched", lambda video_id, watched: video_id == "vid1")

    result = api_module.api_video_patch("vid1", api_module.VideoPatchBody(watched=True))

    assert result == {"video_id": "vid1", "watched": True}


def test_sync_status_returns_snapshot(monkeypatch) -> None:
    fake = _FakeProgress()
    monkeypatch.setattr(api_module, "get_fetch_progress", lambda: fake)

    assert api_module.api_videos_sync_status() == {"running": False, "message": "idle"}


def test_video_list_rejects_invalid_date_field() -> None:
    with pytest.raises(HTTPException) as exc:
        api_module.api_video_list(date_field="created")
    assert exc.value.status_code == 400


def test_video_list_delegates_to_query_videos(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_query_videos(**kwargs: object) -> list[dict[str, str]]:
        captured.update(kwargs)
        return [{"video_id": "v1"}]

    monkeypatch.setattr(api_module, "query_videos", fake_query_videos)

    result = api_module.api_video_list(channel_id="c1", q="hello", date_field="processed")

    assert result == [{"video_id": "v1"}]
    assert captured == {
        "channel_id": "c1",
        "date_from": None,
        "date_to": None,
        "date_field": "processed",
        "topic": None,
        "relevance": None,
        "watched": None,
        "keyword": "hello",
    }


def test_video_patch_returns_404_when_missing(monkeypatch) -> None:
    monkeypatch.setattr(api_module, "set_watched", lambda _video_id, _watched: False)

    with pytest.raises(HTTPException) as exc:
        api_module.api_video_patch("missing", api_module.VideoPatchBody(watched=False))
    assert exc.value.status_code == 404


def test_query_requires_non_empty_q() -> None:
    with pytest.raises(HTTPException) as exc:
        api_module.api_query("   ")
    assert exc.value.status_code == 400


def test_query_merges_hits_with_videos_and_skips_orphans(monkeypatch) -> None:
    monkeypatch.setattr(
        api_module,
        "query_similar",
        lambda _q, top_k=None: [
            {"video_id": "a", "distance": 0.1},
            {"video_id": "missing", "distance": 0.2},
        ],
    )
    monkeypatch.setattr(
        api_module,
        "get_videos_by_ids",
        lambda ids: [{"video_id": "a", "title": "Alpha"}] if ids else [],
    )

    results = api_module.api_query("machine learning", k=5)

    assert len(results) == 1
    assert results[0]["video_id"] == "a"
    assert results[0]["title"] == "Alpha"
    assert results[0]["distance"] == 0.1


def test_video_chat_returns_answer_and_web_sources(monkeypatch) -> None:
    video = {
        "video_id": "v1",
        "title": "Demo",
        "url": "https://example.com/v1",
        "summary": "Short",
        "long_summary": "Long",
    }
    monkeypatch.setattr(api_module, "get_video", lambda video_id: video if video_id == "v1" else None)
    monkeypatch.setattr(
        api_module,
        "search_web",
        lambda question, video=None: (
            [{"title": "NASA", "url": "https://nasa.gov/x", "snippet": "update"}],
            "tavily",
        ),
    )
    monkeypatch.setattr(
        api_module,
        "ask_about_video",
        lambda video, messages, web_hits=None: f"Answer ({len(messages)} msgs, {len(web_hits or [])} web)",
    )

    payload = api_module.api_video_chat(
        "v1",
        api_module.VideoChatBody(messages=[api_module.ChatMessage(role="user", content="Latest?")]),
    )

    assert payload["answer"].startswith("Answer (1 msgs")
    assert payload["web_provider"] == "tavily"
    assert payload["web_sources"] == [{"title": "NASA", "url": "https://nasa.gov/x"}]


def test_video_chat_skips_web_when_disabled(monkeypatch) -> None:
    video = {"video_id": "v1", "title": "Demo", "summary": "Short", "long_summary": "Long", "url": ""}
    monkeypatch.setattr(api_module, "get_video", lambda _video_id: video)

    def fail_search(*_args, **_kwargs):
        raise AssertionError("search_web should not run")

    monkeypatch.setattr(api_module, "search_web", fail_search)
    monkeypatch.setattr(
        api_module,
        "ask_about_video",
        lambda video, messages, web_hits=None: "ok",
    )

    payload = api_module.api_video_chat(
        "v1",
        api_module.VideoChatBody(
            messages=[api_module.ChatMessage(role="user", content="Hi")],
            web_search=False,
        ),
    )
    assert payload["answer"] == "ok"
    assert payload["web_sources"] == []


def test_video_chat_returns_404_for_unknown_video(monkeypatch) -> None:
    monkeypatch.setattr(api_module, "get_video", lambda _video_id: None)

    with pytest.raises(HTTPException) as exc:
        api_module.api_video_chat(
            "missing",
            api_module.VideoChatBody(messages=[api_module.ChatMessage(role="user", content="Hi")]),
        )
    assert exc.value.status_code == 404


def test_answers_returns_answer_and_sources(monkeypatch) -> None:
    monkeypatch.setattr(
        api_module,
        "query_similar",
        lambda _q, top_k=None: [{"video_id": "v1"}],
    )
    monkeypatch.setattr(
        api_module,
        "get_videos_by_ids",
        lambda _ids: [{"video_id": "v1", "title": "Demo"}],
    )
    monkeypatch.setattr(
        api_module,
        "ask_across_videos",
        lambda question, videos: f"Answer to: {question} ({len(videos)} sources)",
    )

    payload = api_module.api_answers(api_module.AnswerBody(question="What is new?", k=3))

    assert payload["answer"] == "Answer to: What is new? (1 sources)"
    assert payload["sources"] == [{"video_id": "v1", "title": "Demo"}]


def test_trends_validates_period_and_months() -> None:
    with pytest.raises(HTTPException) as exc:
        api_module.api_trends(period="day")
    assert exc.value.status_code == 400

    with pytest.raises(HTTPException) as exc:
        api_module.api_trends(months=0)
    assert exc.value.status_code == 400


def test_trends_delegates_to_topic_trends(monkeypatch) -> None:
    monkeypatch.setattr(
        api_module,
        "topic_trends",
        lambda months, bucket: [{"period": "2026-W01", "topic": "ai", "count": 2}],
    )

    rows = api_module.api_trends(period="week", months=6)

    assert rows == [{"period": "2026-W01", "topic": "ai", "count": 2}]


def test_channels_delegates_to_list_channels(monkeypatch) -> None:
    monkeypatch.setattr(api_module, "list_channels", lambda: [{"channel_id": "UC1", "channel_name": "One"}])

    assert api_module.api_channels() == [{"channel_id": "UC1", "channel_name": "One"}]


def test_begin_sync_run_rejects_when_already_running(monkeypatch) -> None:
    monkeypatch.setattr(api_module, "get_fetch_progress", lambda: _FakeProgress(running=True))

    with pytest.raises(HTTPException) as exc:
        api_module._begin_sync_run()
    assert exc.value.status_code == 409


def test_begin_sync_run_maps_runtime_error_to_409(monkeypatch) -> None:
    monkeypatch.setattr(
        api_module,
        "get_fetch_progress",
        lambda: _FakeProgress(begin_error=RuntimeError("fetch already running")),
    )

    with pytest.raises(HTTPException) as exc:
        api_module._begin_sync_run()
    assert exc.value.status_code == 409
    assert exc.value.detail == "fetch already running"


def test_sync_start_returns_202_and_spawns_thread(monkeypatch) -> None:
    fake = _FakeProgress()
    monkeypatch.setattr(api_module, "get_fetch_progress", lambda: fake)
    monkeypatch.setattr(api_module.threading, "Thread", _NoThread)

    response = api_module.api_videos_sync_start()

    assert response.status_code == 202
    assert fake.began is True


def test_video_import_returns_202(monkeypatch) -> None:
    fake = _FakeProgress()
    monkeypatch.setattr(api_module, "get_fetch_progress", lambda: fake)
    monkeypatch.setattr(api_module.threading, "Thread", _NoThread)

    response = api_module.api_video_import(api_module.VideoImportBody(url="https://youtu.be/abc123xyz78"))

    assert response.status_code == 202
    assert fake.began is True


def test_video_import_rejects_blank_url_after_strip() -> None:
    with pytest.raises(HTTPException) as exc:
        api_module.api_video_import(api_module.VideoImportBody(url="   "))
    assert exc.value.status_code == 400


def test_run_fetch_job_completes_on_success(monkeypatch) -> None:
    fake = _FakeProgress()
    monkeypatch.setattr(api_module, "get_fetch_progress", lambda: fake)
    monkeypatch.setattr(api_module, "run_latest", lambda: {"processed": 1})

    api_module._run_fetch_job()

    assert fake.completed == {"processed": 1}


def test_run_fetch_job_fails_on_notification_auth(monkeypatch) -> None:
    fake = _FakeProgress()

    def boom() -> dict[str, int]:
        raise NotificationAuthError("cookies expired")

    monkeypatch.setattr(api_module, "get_fetch_progress", lambda: fake)
    monkeypatch.setattr(api_module, "run_latest", boom)

    api_module._run_fetch_job()

    assert fake.failed == "cookies expired"


def test_run_fetch_url_job_fails_on_invalid_url(monkeypatch) -> None:
    fake = _FakeProgress()
    monkeypatch.setattr(api_module, "get_fetch_progress", lambda: fake)

    def boom(_url: str) -> dict[str, int]:
        raise ValueError("Invalid YouTube URL")

    monkeypatch.setattr(api_module, "run_fetch_url", boom)

    api_module._run_fetch_url_job("not-a-url")

    assert fake.failed == "Invalid YouTube URL"


@pytest.fixture
def api_client(monkeypatch: pytest.MonkeyPatch, tmp_path) -> TestClient:
    monkeypatch.setattr("wow_core.settings.DB_PATH", tmp_path / "wow.db")
    return TestClient(api_module.app)


def test_http_get_video_list(api_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(api_module, "query_videos", lambda **kwargs: [])

    response = api_client.get("/api/video", params={"q": "test"})

    assert response.status_code == 200
    assert response.json() == []


def test_http_get_query_requires_q(api_client: TestClient) -> None:
    response = api_client.get("/api/query", params={"q": "  "})

    assert response.status_code == 400


def test_http_post_answers(api_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(api_module, "query_similar", lambda _q, top_k=None: [])
    monkeypatch.setattr(api_module, "get_videos_by_ids", lambda _ids: [])
    monkeypatch.setattr(api_module, "ask_across_videos", lambda question, videos: "none")

    response = api_client.post("/api/answers", json={"question": "hello?"})

    assert response.status_code == 200
    assert response.json()["answer"] == "none"


def test_http_post_videos_sync_conflict(api_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(api_module, "get_fetch_progress", lambda: _FakeProgress(running=True))

    response = api_client.post("/api/videos")

    assert response.status_code == 409
