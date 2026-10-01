from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from wow_core import settings
from wow_core.channels import load_channels
from wow_core.claude_client import SummarizeError, summarize_video
from wow_core.chroma_store import upsert_summary
from wow_core.db import (
    get_seen,
    get_video,
    init_db,
    insert_video,
    list_channels,
    sync_channels,
    upsert_seen,
)
from wow_core.digest import write_digest
from wow_core.fetch_status import FetchProgress, active_progress
from wow_core.logging_setup import configure_poller_logging
from wow_core.youtube import (
    FeedVideo,
    TranscriptUnavailable,
    fetch_channel_feed,
    get_transcript,
    merge_inbox_and_playlist_videos,
    parse_youtube_video_url,
    patch_published_date_if_missing,
    truncate_transcript,
    _resolve_video_metadata,
)

logger = logging.getLogger("wow.poller")


def _poll_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_published(published_date: str) -> datetime | None:
    raw = (published_date or "").strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def video_is_fresh(published_date: str, *, max_age_days: int, now: datetime | None = None) -> bool:
    if max_age_days <= 0:
        return True
    parsed = _parse_published(published_date)
    if parsed is None:
        return True
    current = now or _poll_now()
    return current - parsed <= timedelta(days=max_age_days)


def _already_handled(video_id: str, *, retry_errors: bool) -> bool:
    seen = get_seen(video_id)
    if not seen:
        return False
    status = seen["status"]
    if status in {"processed", "skipped_no_transcript"}:
        return True
    if status == "error" and not retry_errors:
        return True
    return False


def run_poll(
    *,
    retry_errors: bool = False,
    limit: int | None = None,
    max_age_days: int | None = None,
) -> int:
    configure_poller_logging()
    settings.ensure_data_dirs()
    init_db()

    try:
        configured = load_channels()
    except Exception as exc:
        logger.error("Failed to load channels.json: %s", exc)
        return 1

    if not configured:
        logger.warning("channels.json is empty — add {channel_id, channel_name} entries and re-run.")
        write_digest([])
        return 0

    cap = settings.POLL_LIMIT if limit is None else limit
    age_days = settings.POLL_MAX_AGE_DAYS if max_age_days is None else max_age_days
    sync_channels(configured)
    processed: list[dict[str, Any]] = []
    failures = 0
    attempted = 0
    stopped_early = False

    for channel in list_channels():
        try:
            videos = fetch_channel_feed(channel["channel_id"], channel["rss_url"])
        except Exception as exc:
            logger.error("RSS failed for %s: %s", channel["channel_name"], exc)
            failures += 1
            continue

        logger.info("Fetched %s videos from %s", len(videos), channel["channel_name"])
        for video in videos:
            if _already_handled(video.video_id, retry_errors=retry_errors):
                continue
            if not video_is_fresh(video.published_date, max_age_days=age_days, now=_poll_now()):
                logger.debug(
                    "Skipping %s (%s) — older than %s days",
                    video.title,
                    video.video_id,
                    age_days,
                )
                continue
            result = _process_video(video, channel, retry_errors=retry_errors)
            attempted += 1
            if result == "processed":
                stored = get_video(video.video_id)
                if stored:
                    processed.append(stored)
            elif result == "failed":
                failures += 1
            if cap > 0 and attempted >= cap:
                logger.info("Reached POLL_LIMIT=%s — stopping this run", cap)
                stopped_early = True
                break
        if stopped_early:
            break

    digest_path = write_digest(processed)
    logger.info(
        "Run complete: %s processed, %s failures%s. Digest: %s",
        len(processed),
        failures,
        f", {attempted} attempted (limit {cap})" if cap > 0 else "",
        digest_path,
    )
    return 0


def run_latest(*, retry_errors: bool = False) -> dict[str, int]:
    configure_poller_logging()
    settings.ensure_data_dirs()
    init_db()

    prog = active_progress()

    process_cap = settings.YOUTUBE_NOTIFICATIONS_LIMIT
    videos = merge_inbox_and_playlist_videos()
    processed: list[dict[str, Any]] = []
    processed_n = skipped_n = failed_n = 0
    scanned_n = 0
    total = len(videos)

    for video in videos:
        if process_cap > 0 and processed_n >= process_cap:
            if prog:
                prog.update("process", f"Hit process limit ({process_cap}); stopping early")
            break
        scanned_n += 1
        if prog:
            prog.update(
                "process",
                f"Video {scanned_n}/{total}: {video.title[:80]} ({video.video_id})",
            )
        if not (video.published_date or "").strip():
            if prog:
                prog.update("metadata", f"Resolving publish date for {video.video_id}…")
            patch_published_date_if_missing(video.video_id)
        channel_name = video.channel_name or video.channel_id
        sync_channels([{"channel_id": video.channel_id, "channel_name": channel_name}])
        result = _process_video(
            video,
            {"channel_id": video.channel_id, "channel_name": channel_name},
            retry_errors=retry_errors,
            progress=prog,
        )
        if result == "processed":
            processed_n += 1
            stored = get_video(video.video_id)
            if stored:
                processed.append(stored)
        elif result == "skipped":
            skipped_n += 1
        else:
            failed_n += 1

    if processed:
        write_digest(processed)
    logger.info(
        "Inbox/playlist fetch: scanned %s/%s candidates, %s processed, %s skipped, %s failed",
        scanned_n,
        len(videos),
        processed_n,
        skipped_n,
        failed_n,
    )
    return {
        "scanned": scanned_n,
        "fetched": scanned_n,
        "inbox_rows": len(videos),
        "processed": processed_n,
        "skipped": skipped_n,
        "failed": failed_n,
    }


def run_fetch_url(url: str, *, retry_errors: bool = False) -> dict[str, Any]:
    configure_poller_logging()
    settings.ensure_data_dirs()
    init_db()

    video_id = parse_youtube_video_url(url)
    if not video_id:
        raise ValueError("Invalid YouTube URL")

    prog = active_progress()
    watch_url = f"https://www.youtube.com/watch?v={video_id}"
    title = video_id
    channel_id = ""
    channel_name = ""
    published_date = ""
    description = ""

    if prog:
        prog.update("metadata", f"Resolving metadata for {video_id}…")
    meta = _resolve_video_metadata(video_id)
    if meta:
        if meta.title.strip():
            title = meta.title.strip()
        channel_id = meta.channel_id or ""
        channel_name = meta.channel_name or ""
        published_date = meta.published_date or ""
        description = (getattr(meta, "description", "") or "").strip()

    if not channel_id:
        raise ValueError(f"Could not resolve channel for {video_id}")

    channel_name = channel_name or channel_id
    sync_channels([{"channel_id": channel_id, "channel_name": channel_name}])
    video = FeedVideo(
        video_id=video_id,
        title=title,
        published_date=published_date,
        url=watch_url,
        channel_id=channel_id,
        channel_name=channel_name,
        description=description,
    )
    if prog:
        prog.update("process", f"Processing {title[:80]} ({video_id})")
    outcome = _process_video(
        video,
        {"channel_id": channel_id, "channel_name": channel_name},
        retry_errors=retry_errors,
        progress=prog,
    )
    processed_n = 1 if outcome == "processed" else 0
    skipped_n = 1 if outcome == "skipped" else 0
    failed_n = 1 if outcome == "failed" else 0
    return {
        "video_id": video_id,
        "scanned": 1,
        "fetched": 1,
        "inbox_rows": 1,
        "processed": processed_n,
        "skipped": skipped_n,
        "failed": failed_n,
        "outcome": outcome,
    }


def _process_video(
    video: Any,
    channel: dict[str, Any],
    *,
    retry_errors: bool,
    progress: FetchProgress | None = None,
) -> str:
    seen = get_seen(video.video_id)
    if seen:
        status = seen["status"]
        if status == "processed":
            return "skipped"
        if status == "skipped_no_transcript":
            return "skipped"
        if status == "error" and not retry_errors:
            return "skipped"

    published_date = (video.published_date or "").strip()
    description = str(getattr(video, "description", "") or "").strip()
    if not published_date or not description:
        meta = _resolve_video_metadata(video.video_id)
        if meta:
            if not published_date and meta.published_date.strip():
                published_date = meta.published_date.strip()
            if not description:
                description = (getattr(meta, "description", "") or "").strip()

    logger.info("Processing %s (%s)", video.title, video.video_id)
    try:
        if progress:
            progress.update("transcript", f"Fetching transcript for {video.video_id}…")
        transcript = get_transcript(video.video_id)
        if progress:
            progress.update("summarize", f"Summarizing {video.video_id} via Claude…")
        text, truncated = truncate_transcript(transcript.text)
        if truncated:
            logger.warning("Truncated transcript for %s to %s chars", video.video_id, len(text))

        summary = summarize_video(
            title=video.title,
            channel_name=channel["channel_name"],
            published_date=published_date,
            url=video.url,
            transcript=text,
        )
        upsert_seen(
            video_id=video.video_id,
            channel_id=channel["channel_id"],
            status="processed",
            title=video.title,
            published_date=published_date,
            url=video.url,
        )
        insert_video(
            video_id=video.video_id,
            channel_id=channel["channel_id"],
            title=video.title,
            published_date=published_date,
            url=video.url,
            description=description,
            transcript_length=len(transcript.text),
            transcript=transcript.text,
            summary=summary["summary"],
            long_summary=summary["long_summary"],
            key_takeaways=summary["key_takeaways"],
            topics=summary["topics"],
            relevance=summary["estimated_relevance"],
        )
        upsert_summary(
            video_id=video.video_id,
            summary=summary["summary"],
            channel_id=channel["channel_id"],
            channel_name=channel["channel_name"],
            published_date=published_date,
            topics=summary["topics"],
            relevance=summary["estimated_relevance"],
            title=video.title,
        )
        logger.info("Processed %s via %s captions", video.video_id, transcript.source)
        return "processed"
    except TranscriptUnavailable as exc:
        logger.warning("No transcript for %s: %s", video.video_id, exc)
        upsert_seen(
            video_id=video.video_id,
            channel_id=channel["channel_id"],
            status="skipped_no_transcript",
            title=video.title,
            published_date=published_date,
            url=video.url,
            error_message=str(exc),
        )
        return "skipped"
    except SummarizeError as exc:
        logger.error("Claude failed for %s: %s", video.video_id, exc)
        upsert_seen(
            video_id=video.video_id,
            channel_id=channel["channel_id"],
            status="error",
            title=video.title,
            published_date=published_date,
            url=video.url,
            error_message=str(exc),
        )
        return "failed"
    except Exception as exc:
        logger.exception("Unexpected error for %s: %s", video.video_id, exc)
        upsert_seen(
            video_id=video.video_id,
            channel_id=channel["channel_id"],
            status="error",
            title=video.title,
            published_date=published_date,
            url=video.url,
            error_message=str(exc),
        )
        return "failed"
