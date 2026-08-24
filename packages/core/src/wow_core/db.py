from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator, Literal

from wow_core import settings

SeenStatus = Literal["processed", "skipped_no_transcript", "error"]
Relevance = Literal["high", "medium", "low"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS channels (
    channel_id   TEXT PRIMARY KEY,
    channel_name TEXT NOT NULL,
    rss_url      TEXT NOT NULL,
    added_date   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS seen (
    video_id         TEXT PRIMARY KEY,
    channel_id       TEXT NOT NULL,
    title            TEXT,
    published_date   TEXT,
    url              TEXT,
    first_seen_date  TEXT NOT NULL,
    status           TEXT NOT NULL CHECK (status IN (
                         'processed',
                         'skipped_no_transcript',
                         'error'
                     )),
    error_message    TEXT,
    FOREIGN KEY (channel_id) REFERENCES channels(channel_id)
);

CREATE TABLE IF NOT EXISTS videos (
    video_id           TEXT PRIMARY KEY,
    channel_id         TEXT NOT NULL,
    title              TEXT NOT NULL,
    published_date     TEXT NOT NULL,
    url                TEXT NOT NULL,
    transcript_length  INTEGER,
    summary            TEXT NOT NULL,
    key_takeaways      TEXT NOT NULL,
    topics             TEXT NOT NULL,
    relevance          TEXT NOT NULL CHECK (relevance IN ('high', 'medium', 'low')),
    processed_date     TEXT NOT NULL,
    watched            INTEGER NOT NULL DEFAULT 0 CHECK (watched IN (0, 1)),
    FOREIGN KEY (channel_id) REFERENCES channels(channel_id),
    FOREIGN KEY (video_id) REFERENCES seen(video_id)
);

CREATE INDEX IF NOT EXISTS idx_videos_published_date ON videos(published_date);
CREATE INDEX IF NOT EXISTS idx_videos_channel_id     ON videos(channel_id);
CREATE INDEX IF NOT EXISTS idx_videos_relevance      ON videos(relevance);
CREATE INDEX IF NOT EXISTS idx_seen_status           ON seen(status);
"""


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def rss_url_for(channel_id: str) -> str:
    return f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"


@contextmanager
def connect(db_path: Path | None = None) -> Iterator[sqlite3.Connection]:
    path = db_path or settings.DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db(db_path: Path | None = None) -> None:
    with connect(db_path) as conn:
        conn.executescript(SCHEMA)


def sync_channels(channels: list[dict[str, str]], db_path: Path | None = None) -> int:
    """Upsert channels.json into SQLite. Never deletes existing rows."""
    added = 0
    now = utc_now()
    with connect(db_path) as conn:
        for item in channels:
            channel_id = item["channel_id"].strip()
            name = item["channel_name"].strip()
            existing = conn.execute(
                "SELECT channel_id FROM channels WHERE channel_id = ?",
                (channel_id,),
            ).fetchone()
            if existing:
                conn.execute(
                    "UPDATE channels SET channel_name = ?, rss_url = ? WHERE channel_id = ?",
                    (name, rss_url_for(channel_id), channel_id),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO channels (channel_id, channel_name, rss_url, added_date)
                    VALUES (?, ?, ?, ?)
                    """,
                    (channel_id, name, rss_url_for(channel_id), now),
                )
                added += 1
    return added


def list_channels(db_path: Path | None = None) -> list[dict[str, Any]]:
    with connect(db_path) as conn:
        rows = conn.execute(
            "SELECT channel_id, channel_name, rss_url, added_date FROM channels ORDER BY channel_name"
        ).fetchall()
    return [dict(row) for row in rows]


def get_seen(video_id: str, db_path: Path | None = None) -> dict[str, Any] | None:
    with connect(db_path) as conn:
        row = conn.execute("SELECT * FROM seen WHERE video_id = ?", (video_id,)).fetchone()
    return dict(row) if row else None


def upsert_seen(
    *,
    video_id: str,
    channel_id: str,
    status: SeenStatus,
    title: str | None = None,
    published_date: str | None = None,
    url: str | None = None,
    error_message: str | None = None,
    db_path: Path | None = None,
) -> None:
    now = utc_now()
    with connect(db_path) as conn:
        existing = conn.execute(
            "SELECT first_seen_date FROM seen WHERE video_id = ?", (video_id,)
        ).fetchone()
        first_seen = existing["first_seen_date"] if existing else now
        conn.execute(
            """
            INSERT INTO seen (
                video_id, channel_id, title, published_date, url,
                first_seen_date, status, error_message
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(video_id) DO UPDATE SET
                channel_id = excluded.channel_id,
                title = COALESCE(excluded.title, seen.title),
                published_date = COALESCE(excluded.published_date, seen.published_date),
                url = COALESCE(excluded.url, seen.url),
                status = excluded.status,
                error_message = excluded.error_message
            """,
            (video_id, channel_id, title, published_date, url, first_seen, status, error_message),
        )


def insert_video(
    *,
    video_id: str,
    channel_id: str,
    title: str,
    published_date: str,
    url: str,
    transcript_length: int,
    summary: str,
    key_takeaways: list[str],
    topics: list[str],
    relevance: Relevance,
    db_path: Path | None = None,
) -> None:
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO videos (
                video_id, channel_id, title, published_date, url,
                transcript_length, summary, key_takeaways, topics,
                relevance, processed_date, watched
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
            ON CONFLICT(video_id) DO UPDATE SET
                title = excluded.title,
                published_date = excluded.published_date,
                url = excluded.url,
                transcript_length = excluded.transcript_length,
                summary = excluded.summary,
                key_takeaways = excluded.key_takeaways,
                topics = excluded.topics,
                relevance = excluded.relevance,
                processed_date = excluded.processed_date
            """,
            (
                video_id,
                channel_id,
                title,
                published_date,
                url,
                transcript_length,
                summary,
                json.dumps(key_takeaways, ensure_ascii=False),
                json.dumps(topics, ensure_ascii=False),
                relevance,
                utc_now(),
            ),
        )


def set_watched(video_id: str, watched: bool, db_path: Path | None = None) -> bool:
    with connect(db_path) as conn:
        cur = conn.execute(
            "UPDATE videos SET watched = ? WHERE video_id = ?",
            (1 if watched else 0, video_id),
        )
        return cur.rowcount > 0


def get_video(video_id: str, db_path: Path | None = None) -> dict[str, Any] | None:
    with connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT v.*, c.channel_name
            FROM videos v
            JOIN channels c ON c.channel_id = v.channel_id
            WHERE v.video_id = ?
            """,
            (video_id,),
        ).fetchone()
    return _video_row(row) if row else None


def get_videos_by_ids(video_ids: list[str], db_path: Path | None = None) -> list[dict[str, Any]]:
    if not video_ids:
        return []
    placeholders = ",".join("?" * len(video_ids))
    with connect(db_path) as conn:
        rows = conn.execute(
            f"""
            SELECT v.*, c.channel_name
            FROM videos v
            JOIN channels c ON c.channel_id = v.channel_id
            WHERE v.video_id IN ({placeholders})
            """,
            video_ids,
        ).fetchall()
    by_id = {row["video_id"]: _video_row(row) for row in rows}
    return [by_id[vid] for vid in video_ids if vid in by_id]


def query_videos(
    *,
    channel_id: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    topic: str | None = None,
    relevance: str | None = None,
    watched: bool | None = None,
    keyword: str | None = None,
    db_path: Path | None = None,
) -> list[dict[str, Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if channel_id:
        clauses.append("v.channel_id = ?")
        params.append(channel_id)
    if date_from:
        clauses.append("v.published_date >= ?")
        params.append(date_from)
    if date_to:
        clauses.append("v.published_date <= ?")
        params.append(date_to)
    if relevance:
        clauses.append("v.relevance = ?")
        params.append(relevance)
    if watched is not None:
        clauses.append("v.watched = ?")
        params.append(1 if watched else 0)
    if keyword:
        clauses.append("(v.title LIKE ? OR v.summary LIKE ?)")
        like = f"%{keyword}%"
        params.extend([like, like])
    if topic:
        clauses.append(
            "EXISTS (SELECT 1 FROM json_each(v.topics) WHERE json_each.value LIKE ?)"
        )
        params.append(f"%{topic}%")

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    sql = f"""
        SELECT v.*, c.channel_name
        FROM videos v
        JOIN channels c ON c.channel_id = v.channel_id
        {where}
        ORDER BY v.published_date DESC
    """
    with connect(db_path) as conn:
        rows = conn.execute(sql, params).fetchall()
    return [_video_row(row) for row in rows]


def topic_trends(
    *,
    months: int = 3,
    bucket: Literal["week", "month"] = "week",
    db_path: Path | None = None,
) -> list[dict[str, Any]]:
    date_fmt = "%Y-%W" if bucket == "week" else "%Y-%m"
    cutoff = (datetime.now(timezone.utc) - timedelta(days=30 * months)).date().isoformat()
    sql = f"""
        SELECT
            json_each.value AS topic,
            strftime('{date_fmt}', substr(v.published_date, 1, 10)) AS period,
            COUNT(*) AS count
        FROM videos v, json_each(v.topics)
        WHERE substr(v.published_date, 1, 10) >= ?
        GROUP BY topic, period
        ORDER BY period ASC, count DESC
    """
    with connect(db_path) as conn:
        rows = conn.execute(sql, (cutoff,)).fetchall()
    return [dict(row) for row in rows]


def _video_row(row: sqlite3.Row) -> dict[str, Any]:
    data = dict(row)
    data["key_takeaways"] = json.loads(data["key_takeaways"])
    data["topics"] = json.loads(data["topics"])
    data["watched"] = bool(data["watched"])
    return data
