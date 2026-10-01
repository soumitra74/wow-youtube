from __future__ import annotations

import sqlite3
from pathlib import Path

from wow_core.db import (
    create_oauth_session,
    create_or_update_oauth_user,
    delete_video,
    get_oauth_user_by_session,
    get_seen,
    get_video,
    get_videos_by_ids,
    init_db,
    insert_video,
    list_channels,
    list_watchlater_items,
    query_videos,
    set_video_chat_transcript,
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
        transcript="Full orbital mechanics transcript.",
        summary="A walkthrough of orbital mechanics.",
        long_summary="A detailed walkthrough of orbital mechanics and mission planning.",
        key_takeaways=["Inclination matters", "Delta-v is scarce"],
        topics=["space", "physics"],
        relevance="high",
        description="Chapters, links, and shownotes.",
        db_path=path,
    )
    video = get_video("vid1", db_path=path)
    assert video is not None
    assert video["transcript"] == "Full orbital mechanics transcript."
    assert video["description"] == "Chapters, links, and shownotes."
    assert video["long_summary"] == "A detailed walkthrough of orbital mechanics and mission planning."
    assert video["topics"] == ["space", "physics"]
    assert video["watched"] is False
    compact_video = get_videos_by_ids(["vid1"], db_path=path)[0]
    assert "transcript" not in compact_video
    assert "description" not in compact_video
    assert "long_summary" not in compact_video
    assert set_watched("vid1", True, db_path=path) is True
    assert get_video("vid1", db_path=path)["watched"] is True


def test_delete_video_keeps_seen_marker(tmp_path: Path) -> None:
    path = _db(tmp_path)
    upsert_seen(
        video_id="vid-delete",
        channel_id="chan1",
        status="processed",
        db_path=path,
    )
    insert_video(
        video_id="vid-delete",
        channel_id="chan1",
        title="Delete me",
        published_date="2026-08-20T00:00:00+00:00",
        url="https://www.youtube.com/watch?v=vid-delete",
        transcript_length=10,
        transcript="Transcript.",
        summary="Summary.",
        long_summary="Long summary.",
        key_takeaways=["Takeaway"],
        topics=["testing"],
        relevance="low",
        db_path=path,
    )

    assert delete_video("vid-delete", db_path=path) is True
    assert get_video("vid-delete", db_path=path) is None
    assert get_seen("vid-delete", db_path=path)["status"] == "processed"
    assert delete_video("vid-delete", db_path=path) is False


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
        transcript="Agent transcript.",
        summary="How to wire tool-using agents.",
        long_summary="A detailed explanation of wiring tool-using agents.",
        key_takeaways=["graphs compose"],
        topics=["ai-agents", "llms"],
        relevance="high",
        db_path=path,
    )
    rows = query_videos(topic="agents", keyword="LangGraph", db_path=path)
    assert len(rows) == 1
    assert "transcript" not in rows[0]
    assert "description" not in rows[0]
    assert "long_summary" not in rows[0]
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
        transcript="JWST transcript.",
        summary="Telescope news.",
        long_summary="A detailed review of recent telescope news.",
        key_takeaways=["infrared"],
        topics=["space"],
        relevance="medium",
        db_path=path,
    )
    rows = topic_trends(months=3, bucket="month", db_path=path)
    assert rows
    assert rows[0]["topic"] == "space"
    assert rows[0]["count"] == 1


def test_init_db_migrates_existing_videos_table_for_details(tmp_path: Path) -> None:
    path = tmp_path / "legacy.db"
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            CREATE TABLE videos (
                video_id TEXT PRIMARY KEY,
                channel_id TEXT NOT NULL,
                title TEXT NOT NULL,
                published_date TEXT NOT NULL,
                url TEXT NOT NULL,
                transcript_length INTEGER,
                summary TEXT NOT NULL,
                key_takeaways TEXT NOT NULL,
                topics TEXT NOT NULL,
                relevance TEXT NOT NULL,
                processed_date TEXT NOT NULL,
                watched INTEGER NOT NULL DEFAULT 0
            )
            """
        )

    init_db(path)

    with sqlite3.connect(path) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(videos)")}
    assert {"transcript", "long_summary", "description", "chat_transcript"} <= columns


def test_video_chat_transcript_round_trip(tmp_path: Path) -> None:
    path = _db(tmp_path)
    upsert_seen(video_id="vid1", channel_id="chan1", status="processed", db_path=path)
    insert_video(
        video_id="vid1",
        channel_id="chan1",
        title="Orbitals",
        published_date="2026-08-01T10:00:00+00:00",
        url="https://www.youtube.com/watch?v=vid1",
        transcript_length=10,
        transcript="Transcript.",
        summary="Summary.",
        long_summary="Long summary.",
        key_takeaways=["One"],
        topics=["space"],
        relevance="high",
        db_path=path,
    )
    transcript = [
        {"role": "user", "content": "Question?", "display": "Short label"},
        {
            "role": "assistant",
            "content": "Answer.",
            "web_provider": "tavily",
            "web_sources": [{"title": "NASA", "url": "https://nasa.gov"}],
        },
    ]
    assert set_video_chat_transcript("vid1", transcript, db_path=path) is True
    video = get_video("vid1", db_path=path)
    assert video is not None
    assert video["chat_transcript"] == transcript
