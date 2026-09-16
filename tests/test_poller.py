from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from wow_core.db import init_db, list_channels, sync_channels, upsert_seen
from wow_core.youtube import FeedVideo, TranscriptUnavailable
from wow_poller.pipeline import _process_video, run_latest, run_poll, video_is_fresh


def _prepare(tmp_path: Path, monkeypatch) -> Path:
    db_path = tmp_path / "wow.db"
    monkeypatch.setattr("wow_core.settings.DB_PATH", db_path)
    init_db()
    sync_channels([{"channel_id": "chan1", "channel_name": "Demo"}])
    return db_path


def _prepare(tmp_path: Path, monkeypatch) -> Path:
    db_path = tmp_path / "wow.db"
    monkeypatch.setattr("wow_core.settings.DB_PATH", db_path)
    init_db()
    sync_channels([{"channel_id": "chan1", "channel_name": "Demo"}])
    return db_path


def test_process_skips_already_seen(tmp_path: Path, monkeypatch) -> None:
    _prepare(tmp_path, monkeypatch)
    upsert_seen(video_id="vid1", channel_id="chan1", status="processed")
    called = {"n": 0}

    def fail_if_called(_video_id: str):
        called["n"] += 1
        raise AssertionError("should not fetch transcript for processed videos")

    monkeypatch.setattr("wow_poller.pipeline.get_transcript", fail_if_called)
    video = SimpleNamespace(video_id="vid1", title="Old", published_date="", url="")
    assert _process_video(video, {"channel_id": "chan1", "channel_name": "Demo"}, retry_errors=False) == "skipped"
    assert called["n"] == 0


def test_process_retries_error_rows(tmp_path: Path, monkeypatch) -> None:
    _prepare(tmp_path, monkeypatch)
    upsert_seen(video_id="vid1", channel_id="chan1", status="error")
    called = {"n": 0}

    def no_captions(_video_id: str):
        called["n"] += 1
        raise TranscriptUnavailable("none")

    monkeypatch.setattr("wow_poller.pipeline.get_transcript", no_captions)
    video = SimpleNamespace(
        video_id="vid1",
        title="Old",
        published_date="2026-08-22T00:00:00+00:00",
        url="",
    )
    assert _process_video(video, {"channel_id": "chan1"}, retry_errors=False) == "skipped"
    assert called["n"] == 0
    assert _process_video(video, {"channel_id": "chan1"}, retry_errors=True) == "skipped"
    assert called["n"] == 1


def test_run_latest_upserts_channels_and_processes_notifications(tmp_path: Path, monkeypatch) -> None:
    _prepare(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "wow_poller.pipeline.fetch_notification_videos",
        lambda scan_limit=None: [
            FeedVideo(
                video_id="new1",
                title="From inbox",
                published_date="2026-08-22T00:00:00+00:00",
                url="https://www.youtube.com/watch?v=new1",
                channel_id="chan2",
                channel_name="Inbox Channel",
            ),
            FeedVideo(
                video_id="new2",
                title="Known channel",
                published_date="2026-08-22T01:00:00+00:00",
                url="https://www.youtube.com/watch?v=new2",
                channel_id="chan1",
                channel_name="Demo",
            ),
        ],
    )
    processed: list[tuple[str, str]] = []

    def fake_process(video, channel, *, retry_errors):
        processed.append((video.video_id, channel["channel_id"]))
        return "processed" if video.video_id == "new1" else "skipped"

    monkeypatch.setattr("wow_poller.pipeline._process_video", fake_process)
    result = run_latest()
    assert result["fetched"] == 2
    assert result["processed"] == 1
    assert result["skipped"] == 1
    assert result["failed"] == 0
    assert processed == [("new1", "chan2"), ("new2", "chan1")]
    ids = {c["channel_id"] for c in list_channels()}
    assert ids == {"chan1", "chan2"}


def test_video_is_fresh_respects_age_window() -> None:
    now = datetime(2026, 8, 24, 10, 0, tzinfo=timezone.utc)
    assert video_is_fresh("2026-08-23T12:00:00+00:00", max_age_days=3, now=now) is True
    assert video_is_fresh("2026-08-01T12:00:00+00:00", max_age_days=3, now=now) is False
    assert video_is_fresh("2026-08-01T12:00:00+00:00", max_age_days=0, now=now) is True
    assert video_is_fresh("", max_age_days=3, now=now) is True


def _feed_videos(*ids: str, published: str = "2026-08-23T00:00:00+00:00") -> list[FeedVideo]:
    return [
        FeedVideo(
            video_id=video_id,
            title=video_id,
            published_date=published,
            url=f"https://www.youtube.com/watch?v={video_id}",
            channel_id="chan1",
        )
        for video_id in ids
    ]


def test_run_poll_stops_after_limit(tmp_path: Path, monkeypatch) -> None:
    _prepare(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "wow_poller.pipeline.load_channels",
        lambda: [{"channel_id": "chan1", "channel_name": "Demo"}],
    )
    monkeypatch.setattr(
        "wow_poller.pipeline.fetch_channel_feed",
        lambda channel_id, rss_url: _feed_videos("aaaaaaaaaaa", "bbbbbbbbbbb", "ccccccccccc"),
    )
    attempted: list[str] = []

    def fake_process(video, channel, *, retry_errors):
        attempted.append(video.video_id)
        return "processed"

    monkeypatch.setattr("wow_poller.pipeline._process_video", fake_process)
    monkeypatch.setattr("wow_poller.pipeline.get_video", lambda video_id: {"video_id": video_id})
    monkeypatch.setattr("wow_poller.pipeline.write_digest", lambda rows: "digest.md")
    assert run_poll(limit=2, max_age_days=0) == 0
    assert attempted == ["aaaaaaaaaaa", "bbbbbbbbbbb"]


def test_run_poll_skips_old_videos_without_processing(tmp_path: Path, monkeypatch) -> None:
    _prepare(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "wow_poller.pipeline.load_channels",
        lambda: [{"channel_id": "chan1", "channel_name": "Demo"}],
    )
    monkeypatch.setattr(
        "wow_poller.pipeline.fetch_channel_feed",
        lambda channel_id, rss_url: [
            *_feed_videos("oldoldoldol", published="2026-01-01T00:00:00+00:00"),
            *_feed_videos("newnewnewne", published="2026-08-23T00:00:00+00:00"),
        ],
    )
    attempted: list[str] = []

    def fake_process(video, channel, *, retry_errors):
        attempted.append(video.video_id)
        return "processed"

    monkeypatch.setattr("wow_poller.pipeline._process_video", fake_process)
    monkeypatch.setattr("wow_poller.pipeline.get_video", lambda video_id: {"video_id": video_id})
    monkeypatch.setattr("wow_poller.pipeline.write_digest", lambda rows: "digest.md")
    now = datetime(2026, 8, 24, tzinfo=timezone.utc)
    monkeypatch.setattr("wow_poller.pipeline._poll_now", lambda: now)
    assert run_poll(limit=10, max_age_days=3) == 0
    assert attempted == ["newnewnewne"]


def test_run_poll_does_not_count_already_seen_toward_limit(tmp_path: Path, monkeypatch) -> None:
    _prepare(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "wow_poller.pipeline.load_channels",
        lambda: [{"channel_id": "chan1", "channel_name": "Demo"}],
    )
    upsert_seen(video_id="aaaaaaaaaaa", channel_id="chan1", status="processed")
    monkeypatch.setattr(
        "wow_poller.pipeline.fetch_channel_feed",
        lambda channel_id, rss_url: _feed_videos("aaaaaaaaaaa", "bbbbbbbbbbb", "ccccccccccc"),
    )
    attempted: list[str] = []

    def fake_process(video, channel, *, retry_errors):
        attempted.append(video.video_id)
        return "processed"

    monkeypatch.setattr("wow_poller.pipeline._process_video", fake_process)
    monkeypatch.setattr("wow_poller.pipeline.get_video", lambda video_id: {"video_id": video_id})
    monkeypatch.setattr("wow_poller.pipeline.write_digest", lambda rows: "digest.md")
    assert run_poll(limit=1, max_age_days=0) == 0
    assert attempted == ["bbbbbbbbbbb"]
