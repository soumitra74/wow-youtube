from __future__ import annotations

from pathlib import Path

from wow_core.db import (
    create_oauth_session,
    create_or_update_oauth_user,
    get_oauth_user_by_session,
    get_seen,
    get_video,
    init_db,
    insert_video,
    list_channels,
    list_watchlater_items,
    query_videos,
    set_watched,
    sync_channels,
    topic_trends,
    upsert_seen,
    upsert_watchlater_item,
)


def _db(tmp_path: Path) -> Path:
    path = tmp_path / "wow.db"
    init_db(path)
    sync_channels(
        [{"channel_id": "chan1", "channel_name": "Physics Weekly"}],
        db_path=path,
    )
    return path


def test_sync_channels_is_idempotent(tmp_path: Path) -> None:
    path = _db(tmp_path)
    added = sync_channels(
        [{"channel_id": "chan1", "channel_name": "Physics Weekly Renamed"}],
        db_path=path,
    )
    assert added == 0
    channels = list_channels(path)
    assert len(channels) == 1
    assert channels[0]["channel_name"] == "Physics Weekly Renamed"


def test_seen_and_video_roundtrip(tmp_path: Path) -> None:
    path = _db(tmp_path)
    upsert_seen(
        video_id="vid1",
        channel_id="chan1",
        status="processed",
        title="Orbitals",
        published_date="2026-08-01T10:00:00+00:00",
        url="https://www.youtube.com/watch?v=vid1",
        db_path=path,
    )
    insert_video(
        video_id="vid1",
        channel_id="chan1",
        title="Orbitals",
        published_date="2026-08-01T10:00:00+00:00",
        url="https://www.youtube.com/watch?v=vid1",
        transcript_length=1200,
        summary="A walkthrough of orbital mechanics.",
        key_takeaways=["Inclination matters", "Delta-v is scarce"],
        topics=["space", "physics"],
        relevance="high",
        db_path=path,
    )
    video = get_video("vid1", db_path=path)
    assert video is not None
    assert video["topics"] == ["space", "physics"]
    assert video["watched"] is False
    assert set_watched("vid1", True, db_path=path) is True
    assert get_video("vid1", db_path=path)["watched"] is True


def test_query_filters_and_keyword(tmp_path: Path) -> None:
    path = _db(tmp_path)
    upsert_seen(
        video_id="vid1",
        channel_id="chan1",
        status="processed",
        db_path=path,
    )
    insert_video(
        video_id="vid1",
        channel_id="chan1",
        title="LangGraph agents",
        published_date="2026-08-20T00:00:00+00:00",
        url="https://www.youtube.com/watch?v=vid1",
        transcript_length=10,
        summary="How to wire tool-using agents.",
        key_takeaways=["graphs compose"],
        topics=["ai-agents", "llms"],
        relevance="high",
        db_path=path,
    )
    rows = query_videos(topic="agents", keyword="LangGraph", db_path=path)
    assert len(rows) == 1
    assert query_videos(relevance="low", db_path=path) == []
    assert query_videos(date_from="2099-01-01T00:00:00+00:00", db_path=path) == []
    assert len(query_videos(date_from="2020-01-01T00:00:00+00:00", date_field="processed", db_path=path)) == 1


def test_error_seen_can_be_updated(tmp_path: Path) -> None:
    path = _db(tmp_path)
    upsert_seen(video_id="vid2", channel_id="chan1", status="error", error_message="boom", db_path=path)
    upsert_seen(video_id="vid2", channel_id="chan1", status="processed", db_path=path)
    assert get_seen("vid2", db_path=path)["status"] == "processed"


def test_watchlater_oauth_and_date_filter(tmp_path: Path) -> None:
    path = tmp_path / "wow.db"
    init_db(path)
    user = create_or_update_oauth_user(
        google_id="sub123",
        email="me@example.com",
        refresh_token="rt",
        db_path=path,
    )
    create_oauth_session(user_id=user["id"], token="sess1", db_path=path)
    assert get_oauth_user_by_session("sess1", db_path=path)["google_id"] == "sub123"

    upsert_watchlater_item(
        user_id=user["id"],
        video_id="abc",
        title="Old",
        added_at="2026-01-01T00:00:00+00:00",
        db_path=path,
    )
    upsert_watchlater_item(
        user_id=user["id"],
        video_id="def",
        title="Recent",
        added_at="2026-08-15T00:00:00+00:00",
        db_path=path,
    )
    all_items = list_watchlater_items(user_id=user["id"], db_path=path)
    assert len(all_items) == 2
    filtered = list_watchlater_items(
        user_id=user["id"],
        date_from="2026-08-01T00:00:00+00:00",
        db_path=path,
    )
    assert len(filtered) == 1
    assert filtered[0]["video_id"] == "def"


def test_topic_trends_buckets(tmp_path: Path) -> None:
    path = _db(tmp_path)
    upsert_seen(video_id="vid3", channel_id="chan1", status="processed", db_path=path)
    insert_video(
        video_id="vid3",
        channel_id="chan1",
        title="JWST",
        published_date="2026-08-10T00:00:00+00:00",
        url="https://www.youtube.com/watch?v=vid3",
        transcript_length=10,
        summary="Telescope news.",
        key_takeaways=["infrared"],
        topics=["space"],
        relevance="medium",
        db_path=path,
    )
    rows = topic_trends(months=3, bucket="month", db_path=path)
    assert rows
    assert rows[0]["topic"] == "space"
    assert rows[0]["count"] == 1
