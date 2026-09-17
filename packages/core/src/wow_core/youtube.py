from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
import tempfile
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from wow_core import settings

logger = logging.getLogger(__name__)

PREFERRED_LANGS = ("en", "en-US", "en-GB", "en-orig")
_CAPTION_EXTS = ("json3", "vtt", "srv3", "srv2", "srv1", "ttml")
_VIDEO_ID_RE = re.compile(r"^[\w-]{11}$")
_VTT_TAG = re.compile(r"<[^>]+>")
_last_caption_at = 0.0
_YOUTUBE_SID_COOKIE_NAMES = ("SAPISID", "__Secure-1PAPISID", "__Secure-3PAPISID")
_COOKIE_EXPORT_HINT = (
    "Export Netscape cookies while logged in: private window → youtube.com → "
    "https://www.youtube.com/robots.txt in that same tab → export youtube.com cookies "
    "(must include LOGIN_INFO plus SAPISID or __Secure-3PAPISID) → save as "
    "config/youtube_cookies.txt → close the private window so YouTube does not rotate them."
)


@dataclass(frozen=True)
class FeedVideo:
    video_id: str
    title: str
    published_date: str
    url: str
    channel_id: str
    channel_name: str = ""


@dataclass(frozen=True)
class ResolvedVideoMeta:
    channel_id: str = ""
    published_date: str = ""
    channel_name: str = ""
    title: str = ""


class NotificationAuthError(Exception):
    """YouTube cookies are missing or unusable for the notification inbox."""


@dataclass(frozen=True)
class TranscriptResult:
    text: str
    source: str  # manual | auto | translated | whisper


class TranscriptUnavailable(Exception):
    """No usable captions and generation did not produce a transcript."""


def fetch_channel_feed(channel_id: str, rss_url: str) -> list[FeedVideo]:
    import feedparser

    parsed = feedparser.parse(rss_url)
    if parsed.bozo and not parsed.entries:
        raise RuntimeError(f"RSS fetch failed for {channel_id}: {parsed.bozo_exception}")

    videos: list[FeedVideo] = []
    for entry in parsed.entries:
        video_id = _entry_video_id(entry)
        if not video_id:
            continue
        videos.append(
            FeedVideo(
                video_id=video_id,
                title=getattr(entry, "title", "") or video_id,
                published_date=getattr(entry, "published", "") or "",
                url=getattr(entry, "link", "") or f"https://www.youtube.com/watch?v={video_id}",
                channel_id=channel_id,
            )
        )
    return videos


def get_transcript(
    video_id: str,
    *,
    generate_if_missing: bool | None = None,
    max_minutes: int | None = None,
) -> TranscriptResult:
    generate = (
        settings.GENERATE_MISSING_TRANSCRIPTS
        if generate_if_missing is None
        else generate_if_missing
    )
    _throttle_captions()
    caption = _fetch_youtube_captions(video_id)
    if caption:
        return caption
    _throttle_captions()
    caption = _fetch_ytdlp_captions(video_id)
    if caption:
        return caption
    if not generate:
        raise TranscriptUnavailable("no YouTube captions and generation disabled")

    from wow_core.fetch_status import active_progress

    prog = active_progress()
    if prog:
        prog.update("transcript", f"Whisper fallback for {video_id} (download + transcribe)…")
    limit = settings.WHISPER_MAX_MINUTES if max_minutes is None else max_minutes
    return _transcribe_with_whisper(video_id, max_minutes=limit)


def truncate_transcript(text: str, max_chars: int | None = None) -> tuple[str, bool]:
    limit = settings.TRANSCRIPT_MAX_CHARS if max_chars is None else max_chars
    if len(text) <= limit:
        return text, False
    return text[:limit], True


def _youtube_dl() -> Any:
    import yt_dlp

    return yt_dlp


def _youtube_cookie_names(path: Path) -> set[str]:
    names: set[str] = set()
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) < 6:
            continue
        domain, name = parts[0], parts[5]
        if "youtube.com" in domain.lower():
            names.add(name)
    return names


def _assert_youtube_login_cookies(path: Path) -> None:
    names = _youtube_cookie_names(path)
    has_login = "LOGIN_INFO" in names
    has_sid = any(name in names for name in _YOUTUBE_SID_COOKIE_NAMES)
    if has_login and has_sid:
        return
    missing: list[str] = []
    if not has_login:
        missing.append("LOGIN_INFO")
    if not has_sid:
        missing.append("SAPISID (or __Secure-1PAPISID / __Secure-3PAPISID)")
    raise NotificationAuthError(
        f"YouTube cookies at {path} are not a logged-in session (missing {', '.join(missing)}). "
        + _COOKIE_EXPORT_HINT
    )


def _is_youtube_login_error(exc: BaseException) -> bool:
    text = str(exc).lower()
    return "login details are needed" in text or "cookies are no longer valid" in text


@contextmanager
def _copied_cookiefile() -> Iterator[str | None]:
    cookies = Path(settings.YOUTUBE_COOKIES_PATH)
    if not cookies.exists():
        yield None
        return
    with tempfile.TemporaryDirectory(prefix="wow-yt-cookies-") as tmp:
        cookie_copy = Path(tmp) / "cookies.txt"
        shutil.copy(cookies, cookie_copy)
        yield str(cookie_copy)


def _youtube_player_clients(*, cookies: bool) -> list[str]:
    # Logged-in cookies make yt-dlp prefer tv_downgraded, which YouTube currently
    # returns as UNPLAYABLE ("The page needs to be reloaded").
    if cookies:
        return ["web_safari", "web_embedded", "-tv_downgraded"]
    return ["android", "web_safari", "web"]


class _YtDlpWarningLogger:
    def debug(self, msg: str) -> None:
        logger.debug("%s", msg)

    def info(self, msg: str) -> None:
        logger.debug("%s", msg)

    def warning(self, msg: str) -> None:
        logger.warning("%s", msg)

    def error(self, msg: str) -> None:
        text = msg[6:].lstrip() if msg.startswith("ERROR:") else msg
        logger.warning("%s", text)


def _ytdlp_core_opts() -> dict[str, Any]:
    opts: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "logger": _YtDlpWarningLogger(),
    }
    if shutil.which("deno"):
        opts["js_runtimes"] = {"deno": {}}
    return opts


def _ytdlp_video_opts(*, cookiefile: str | None = None, **extra: Any) -> dict[str, Any]:
    opts = _ytdlp_core_opts()
    opts["extractor_args"] = {
        "youtube": {"player_client": _youtube_player_clients(cookies=bool(cookiefile))}
    }
    opts.update(extra)
    if cookiefile:
        opts["cookiefile"] = cookiefile
    return opts


def _whisper_cookie_attempts(cookiefile: str | None) -> list[str | None]:
    if cookiefile:
        return [cookiefile, None]
    return [None]


def _is_rate_limited_error(exc: BaseException) -> bool:
    text = str(exc).lower()
    return "429" in text or "too many requests" in text


def _rate_limit_backoff_seconds(attempt: int) -> float:
    base = float(settings.CAPTION_RATE_LIMIT_BACKOFF_SECONDS)
    return base * (2**attempt)


def _is_retryable_ytdlp_error(exc: BaseException) -> bool:
    text = str(exc).lower()
    return (
        "page needs to be reloaded" in text
        or "requested format is not available" in text
        or "http error 403" in text
        or _is_rate_limited_error(exc)
    )


def _fetch_ytdlp_playlist_listing(extract_target: str, *, scan_limit: int) -> dict[str, Any]:
    """Authenticated flat playlist extract (notifications pseudo-playlist or ?list= URL)."""
    cookies = Path(settings.YOUTUBE_COOKIES_PATH)
    if not cookies.exists():
        raise NotificationAuthError(
            f"YouTube cookies not found at {cookies}. {_COOKIE_EXPORT_HINT}"
        )
    _assert_youtube_login_cookies(cookies)

    yt_dlp = _youtube_dl()
    with _copied_cookiefile() as cookie_copy:
        opts = {
            **_ytdlp_core_opts(),
            "skip_download": True,
            "extract_flat": True,
            "playlistend": scan_limit,
            "cookiefile": cookie_copy,
        }
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                return ydl.extract_info(extract_target, download=False) or {}
        except Exception as exc:
            if _is_youtube_login_error(exc):
                raise NotificationAuthError(
                    "YouTube no longer treats these cookies as a logged-in session. "
                    + _COOKIE_EXPORT_HINT
                ) from exc
            raise


def fetch_playlist_videos(
    playlist_id: str,
    *,
    scan_limit: int | None = None,
) -> list[FeedVideo]:
    """Videos from a YouTube playlist the signed-in account can read (including WL)."""
    pid = playlist_id.strip()
    if not pid:
        return []
    n = settings.YOUTUBE_PLAYLIST_SCAN_LIMIT if scan_limit is None else scan_limit
    url = f"https://www.youtube.com/playlist?list={pid}"
    info = _fetch_ytdlp_playlist_listing(url, scan_limit=n)
    return notification_entries_to_videos(info, limit=n, resolve_metadata=_resolve_video_metadata)


def fetch_watch_later_videos(*, scan_limit: int | None = None) -> list[FeedVideo]:
    n = settings.YOUTUBE_WATCH_LATER_SCAN_LIMIT if scan_limit is None else scan_limit
    return fetch_playlist_videos("WL", scan_limit=n)


def fetch_notification_videos(*, scan_limit: int | None = None) -> list[FeedVideo]:
    """Latest videos from the signed-in YouTube notification inbox."""
    n = settings.YOUTUBE_NOTIFICATIONS_SCAN_LIMIT if scan_limit is None else scan_limit
    info = _fetch_ytdlp_playlist_listing(":ytnotif", scan_limit=n)
    return notification_entries_to_videos(info, limit=n, resolve_metadata=_resolve_video_metadata)


def merge_inbox_and_playlist_videos() -> list[FeedVideo]:
    """Notification inbox plus configured playlists (Watch Later and extras), de-duplicated."""
    from wow_core.fetch_status import active_progress

    progress = active_progress()
    if progress:
        progress.update("scan", "Reading YouTube notification inbox (yt-dlp)…")
    videos = fetch_notification_videos()
    seen = {v.video_id for v in videos}
    if progress:
        progress.update("scan", f"Inbox: {len(videos)} row(s); reading Watch Later playlist…")

    if settings.YOUTUBE_FETCH_WATCH_LATER:
        for video in fetch_watch_later_videos():
            if video.video_id not in seen:
                videos.append(video)
                seen.add(video.video_id)
        if progress:
            progress.update("scan", f"After Watch Later: {len(videos)} unique candidate(s)")

    for playlist_id in settings.YOUTUBE_EXTRA_PLAYLIST_IDS:
        if progress:
            progress.update("scan", f"Reading playlist {playlist_id}…")
        for video in fetch_playlist_videos(playlist_id):
            if video.video_id not in seen:
                videos.append(video)
                seen.add(video.video_id)

    if progress:
        progress.update("scan", f"Scan complete — {len(videos)} video(s) to consider")
    return videos


def notification_entries_to_videos(
    info: dict[str, Any],
    *,
    limit: int,
    resolve_metadata: Callable[[str], ResolvedVideoMeta | None] | None = None,
) -> list[FeedVideo]:
    videos: list[FeedVideo] = []
    seen: set[str] = set()
    for entry in info.get("entries") or []:
        if not isinstance(entry, dict):
            continue
        video_id = _dict_video_id(entry)
        if not video_id or video_id in seen:
            continue
        channel_id = _dict_channel_id(entry)
        published_date = _dict_published_date(entry)
        channel_name = _dict_channel_name(entry)
        title = str(entry.get("title") or video_id)
        if resolve_metadata and (
            not channel_id or not published_date or not channel_name or title == video_id
        ):
            meta = resolve_metadata(video_id)
            if meta:
                if not channel_id:
                    channel_id = meta.channel_id
                if not published_date:
                    published_date = meta.published_date
                if not channel_name and meta.channel_name:
                    channel_name = meta.channel_name
                if title == video_id and meta.title:
                    title = meta.title
        if not channel_id:
            logger.info("Skipping notification %s — no channel id", video_id)
            continue
        seen.add(video_id)
        watch_url = f"https://www.youtube.com/watch?v={video_id}"
        raw_url = str(entry.get("url") or entry.get("webpage_url") or "")
        videos.append(
            FeedVideo(
                video_id=video_id,
                title=title,
                published_date=published_date,
                url=raw_url if "watch?v=" in raw_url else watch_url,
                channel_id=channel_id,
                channel_name=channel_name,
            )
        )
        if len(videos) >= limit:
            break
    return videos


def _dict_video_id(entry: dict[str, Any]) -> str | None:
    for key in ("video_id", "id"):
        value = entry.get(key)
        if isinstance(value, str) and _VIDEO_ID_RE.fullmatch(value):
            return value
    url = str(entry.get("url") or entry.get("webpage_url") or "")
    parsed = urlparse(url)
    if parsed.path == "/watch":
        values = parse_qs(parsed.query).get("v")
        if values and _VIDEO_ID_RE.fullmatch(values[0]):
            return values[0]
    return None


def _dict_channel_id(entry: dict[str, Any]) -> str:
    for key in ("channel_id", "uploader_id"):
        value = entry.get(key)
        if isinstance(value, str) and value.startswith("UC"):
            return value
    return ""


def _dict_channel_name(entry: dict[str, Any]) -> str:
    for key in ("channel", "uploader", "channel_name"):
        value = entry.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _dict_published_date(entry: dict[str, Any]) -> str:
    upload = entry.get("upload_date")
    if upload and len(str(upload)) == 8:
        raw = str(upload)
        return f"{raw[:4]}-{raw[4:6]}-{raw[6:8]}T00:00:00+00:00"
    ts = entry.get("timestamp") or entry.get("release_timestamp")
    if ts:
        return datetime.fromtimestamp(int(ts), tz=timezone.utc).replace(microsecond=0).isoformat()
    return ""


def _resolve_video_metadata(video_id: str) -> ResolvedVideoMeta | None:
    yt_dlp = _youtube_dl()
    url = f"https://www.youtube.com/watch?v={video_id}"
    max_retries = settings.CAPTION_RATE_LIMIT_RETRIES
    with _copied_cookiefile() as cookie_copy:
        opts = _ytdlp_video_opts(
            cookiefile=cookie_copy,
            skip_download=True,
            ignore_no_formats_error=True,
        )
        for attempt in range(max_retries + 1):
            _throttle_captions()
            try:
                with yt_dlp.YoutubeDL(opts) as ydl:
                    info = ydl.extract_info(url, download=False) or {}
                return ResolvedVideoMeta(
                    channel_id=_dict_channel_id(info),
                    published_date=_dict_published_date(info),
                    channel_name=_dict_channel_name(info),
                    title=str(info.get("title") or ""),
                )
            except Exception as exc:
                if _is_rate_limited_error(exc) and attempt < max_retries:
                    wait = _rate_limit_backoff_seconds(attempt)
                    logger.warning(
                        "Rate limited resolving metadata for %s; retry %s/%s in %ss",
                        video_id,
                        attempt + 1,
                        max_retries,
                        wait,
                    )
                    from wow_core.fetch_status import active_progress

                    prog = active_progress()
                    if prog:
                        prog.update(
                            "wait",
                            f"Rate limited (metadata {video_id}); waiting {wait:.0f}s",
                        )
                    time.sleep(wait)
                    continue
                logger.info("Could not resolve metadata for %s: %s", video_id, exc)
                return None
    return None


def _resolve_channel_id(video_id: str) -> str | None:
    meta = _resolve_video_metadata(video_id)
    if not meta or not meta.channel_id:
        return None
    return meta.channel_id


def patch_published_date_if_missing(video_id: str) -> bool:
    from wow_core.db import get_video, update_published_date_if_missing

    existing = get_video(video_id)
    if existing and (existing.get("published_date") or "").strip():
        return False
    meta = _resolve_video_metadata(video_id)
    if not meta or not meta.published_date.strip():
        return False
    return update_published_date_if_missing(video_id, meta.published_date)


def _entry_video_id(entry: Any) -> str | None:
    video_id = getattr(entry, "yt_videoid", None)
    if video_id:
        return str(video_id)
    link = getattr(entry, "link", "") or ""
    parsed = urlparse(link)
    if parsed.path == "/watch":
        values = parse_qs(parsed.query).get("v")
        return values[0] if values else None
    if "youtu.be" in parsed.netloc:
        return parsed.path.lstrip("/") or None
    return None


def _throttle_captions(
    *,
    now: Callable[[], float] | None = None,
    sleeper: Callable[[float], None] | None = None,
) -> None:
    global _last_caption_at
    delay = float(settings.CAPTION_REQUEST_DELAY_SECONDS)
    if delay <= 0:
        return
    clock = now or time.monotonic
    sleep = sleeper or time.sleep
    current = clock()
    if _last_caption_at > 0:
        remaining = delay - (current - _last_caption_at)
        if remaining > 0:
            sleep(remaining)
            current = clock()
    _last_caption_at = current


def _transcript_api() -> Any:
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("youtube-transcript-api is not installed") from exc
    return YouTubeTranscriptApi()


def _fetch_youtube_captions(video_id: str) -> TranscriptResult | None:
    try:
        api = _transcript_api()
        if hasattr(api, "list"):
            transcript_list = api.list(video_id)
        else:
            transcript_list = api.list_transcripts(video_id)
    except Exception as exc:
        logger.info("No transcript list for %s: %s", video_id, exc)
        return None

    try:
        transcripts = list(transcript_list)
    except TypeError:
        transcripts = list(transcript_list)

    manual = [t for t in transcripts if not getattr(t, "is_generated", False)]
    generated = [t for t in transcripts if getattr(t, "is_generated", False)]

    picked = _pick_by_language(manual) or _pick_by_language(generated)
    if picked:
        try:
            text = _snippets_to_text(_fetch_track(picked))
        except Exception as exc:
            logger.info("YouTube timedtext failed for %s: %s", video_id, exc)
            return None
        if text:
            source = "auto" if getattr(picked, "is_generated", False) else "manual"
            return TranscriptResult(text=text, source=source)

    for track in [*manual, *generated]:
        try:
            translated = track.translate("en")
            text = _snippets_to_text(_fetch_track(translated))
            if text:
                return TranscriptResult(text=text, source="translated")
        except Exception as exc:
            logger.info("YouTube timedtext translate failed for %s: %s", video_id, exc)
            if type(exc).__name__ in {"IpBlocked", "RequestBlocked"}:
                return None

    return None


def _pick_by_language(tracks: list[Any]) -> Any | None:
    by_code = {getattr(t, "language_code", ""): t for t in tracks}
    for code in PREFERRED_LANGS:
        if code in by_code:
            return by_code[code]
    for track in tracks:
        code = getattr(track, "language_code", "")
        if code.startswith("en"):
            return track
    return None


def _fetch_track(track: Any) -> list[Any]:
    fetched = track.fetch()
    if hasattr(fetched, "snippets"):
        return list(fetched.snippets)
    return list(fetched)


def _snippets_to_text(snippets: list[Any]) -> str:
    parts: list[str] = []
    for item in snippets:
        if isinstance(item, dict):
            text = item.get("text", "")
        else:
            text = getattr(item, "text", "")
        if text:
            parts.append(text.replace("\n", " ").strip())
    return " ".join(parts).strip()


def _vtt_to_text(raw: str) -> str:
    parts: list[str] = []
    for line in raw.replace("\r\n", "\n").split("\n"):
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith(("WEBVTT", "NOTE", "Kind:", "Language:", "Style:")):
            continue
        if "-->" in stripped:
            continue
        if stripped.isdigit():
            continue
        parts.append(_VTT_TAG.sub("", stripped))
    return " ".join(parts).strip()


def _json3_to_text(raw: str) -> str:
    data = json.loads(raw)
    chunks: list[str] = []
    for event in data.get("events") or []:
        if not isinstance(event, dict):
            continue
        for seg in event.get("segs") or []:
            if not isinstance(seg, dict):
                continue
            utf = seg.get("utf8")
            if utf:
                chunks.append(str(utf).replace("\n", " "))
    return " ".join("".join(chunks).split())


def _caption_payload_to_text(payload: bytes | str, ext: str) -> str:
    raw = payload.decode("utf-8", errors="replace") if isinstance(payload, bytes) else payload
    if (ext or "").lower() == "json3" or raw.lstrip().startswith("{"):
        try:
            return _json3_to_text(raw)
        except json.JSONDecodeError:
            if (ext or "").lower() == "json3":
                return ""
    return _vtt_to_text(raw)


def _pick_ytdlp_lang(tracks_by_lang: dict[str, Any]) -> list[dict[str, Any]] | None:
    for lang in PREFERRED_LANGS:
        tracks = tracks_by_lang.get(lang)
        if tracks:
            return list(tracks)
    for lang, tracks in tracks_by_lang.items():
        if str(lang).startswith("en") and tracks:
            return list(tracks)
    return None


def _pick_ytdlp_caption(info: dict[str, Any]) -> tuple[list[dict[str, Any]], str] | None:
    manuals = info.get("subtitles") or {}
    autos = info.get("automatic_captions") or {}
    picked = _pick_ytdlp_lang(manuals)
    if picked:
        return picked, "manual"
    picked = _pick_ytdlp_lang(autos)
    if picked:
        return picked, "auto"
    return None


def _select_caption_format(formats: list[dict[str, Any]]) -> dict[str, Any] | None:
    by_ext = {str(item.get("ext") or ""): item for item in formats if item.get("url")}
    for ext in _CAPTION_EXTS:
        if ext in by_ext:
            return by_ext[ext]
    return next((item for item in formats if item.get("url")), None)


def _fetch_ytdlp_captions(video_id: str) -> TranscriptResult | None:
    yt_dlp = _youtube_dl()
    url = f"https://www.youtube.com/watch?v={video_id}"
    max_retries = settings.CAPTION_RATE_LIMIT_RETRIES
    try:
        with _copied_cookiefile() as cookiefile:
            attempts: list[str | None] = [cookiefile]
            if cookiefile:
                attempts.append(None)
            for cookies in attempts:
                for attempt in range(max_retries + 1):
                    try:
                        _throttle_captions()
                        return _fetch_ytdlp_captions_once(yt_dlp, url, cookies)
                    except Exception as exc:
                        if _is_rate_limited_error(exc) and attempt < max_retries:
                            wait = _rate_limit_backoff_seconds(attempt)
                            logger.warning(
                                "Rate limited (yt-dlp captions) for %s; retry %s/%s in %ss",
                                video_id,
                                attempt + 1,
                                max_retries,
                                wait,
                            )
                            from wow_core.fetch_status import active_progress

                            prog = active_progress()
                            if prog:
                                prog.update(
                                    "wait",
                                    f"Rate limited (captions {video_id}); waiting {wait:.0f}s",
                                )
                            time.sleep(wait)
                            continue
                        logger.warning("yt-dlp captions failed for %s: %s", video_id, exc)
                        if cookies is not None and _is_retryable_ytdlp_error(exc):
                            break
                        return None
    except Exception as exc:
        logger.warning("yt-dlp captions failed for %s: %s", video_id, exc)
        return None
    return None


def _fetch_ytdlp_captions_once(yt_dlp: Any, url: str, cookiefile: str | None) -> TranscriptResult | None:
    opts = _ytdlp_video_opts(
        cookiefile=cookiefile,
        skip_download=True,
        ignore_no_formats_error=True,
    )
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False) or {}
        picked = _pick_ytdlp_caption(info)
        if not picked:
            return None
        formats, source = picked
        fmt = _select_caption_format(formats)
        if not fmt:
            return None
        raw = ydl.urlopen(fmt["url"]).read()
    text = _caption_payload_to_text(raw, str(fmt.get("ext") or ""))
    if not text:
        return None
    return TranscriptResult(text=text, source=source)


def _audio_shard_ranges(
    duration: float, shard_seconds: float, overlap_seconds: float = 0.0
) -> list[tuple[float, float]]:
    if duration <= 0:
        return [(0.0, 0.0)]
    if shard_seconds <= 0 or duration <= shard_seconds:
        return [(0.0, duration)]
    overlap = min(max(overlap_seconds, 0.0), shard_seconds / 2)
    ranges: list[tuple[float, float]] = []
    start = 0.0
    while start < duration:
        end = min(start + shard_seconds, duration)
        ranges.append((start, end))
        if end >= duration:
            break
        next_start = end - overlap
        start = end if next_start <= start else next_start
    return ranges


def _extract_audio_shard(src: Path, dest: Path, start: float, end: float) -> None:
    duration = max(end - start, 0.1)
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-ss",
        f"{start:.3f}",
        "-t",
        f"{duration:.3f}",
        "-i",
        str(src),
        "-ac",
        "1",
        "-ar",
        "16000",
        str(dest),
    ]
    subprocess.run(cmd, check=True, capture_output=True)


def _load_whisper_model() -> Any:
    from faster_whisper import WhisperModel

    settings.WHISPER_DIR.mkdir(parents=True, exist_ok=True)
    return WhisperModel(
        settings.WHISPER_MODEL,
        device="cpu",
        compute_type="int8",
        download_root=str(settings.WHISPER_DIR),
    )


def _segment_text(segments: Any) -> str:
    return " ".join(segment.text.strip() for segment in segments if getattr(segment, "text", "")).strip()


def _download_whisper_audio(
    yt_dlp: Any, url: str, tmp_dir: Path, cookiefile: str | None, *, max_minutes: int
) -> float:
    info_opts = _ytdlp_video_opts(cookiefile=cookiefile, skip_download=True)
    with yt_dlp.YoutubeDL(info_opts) as ydl:
        info = ydl.extract_info(url, download=False) or {}
    duration = float(info.get("duration") or 0)
    if duration and duration > max_minutes * 60:
        raise TranscriptUnavailable(
            f"video is {duration / 60:.0f} minutes, over {max_minutes} minute Whisper cap"
        )
    ydl_opts = _ytdlp_video_opts(
        cookiefile=cookiefile,
        format="bestaudio/bestaudio*/best/wa",
        outtmpl=str(tmp_dir / "audio.%(ext)s"),
        postprocessors=[
            {"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "64"}
        ],
    )
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])
    return duration


def _transcribe_with_whisper(video_id: str, *, max_minutes: int) -> TranscriptResult:
    yt_dlp = _youtube_dl()
    url = f"https://www.youtube.com/watch?v={video_id}"
    shard_seconds = settings.WHISPER_SHARD_MINUTES * 60
    overlap = settings.WHISPER_SHARD_OVERLAP_SECONDS
    with tempfile.TemporaryDirectory(prefix="wow-whisper-") as tmp:
        tmp_dir = Path(tmp)
        last_exc: BaseException | None = None
        duration = 0.0
        with _copied_cookiefile() as cookiefile:
            for cookies in _whisper_cookie_attempts(cookiefile):
                try:
                    duration = _download_whisper_audio(
                        yt_dlp, url, tmp_dir, cookies, max_minutes=max_minutes
                    )
                    last_exc = None
                    break
                except TranscriptUnavailable:
                    raise
                except Exception as exc:
                    last_exc = exc
                    logger.warning("Whisper audio download failed for %s: %s", video_id, exc)
                    if cookies is None or not _is_retryable_ytdlp_error(exc):
                        break
        if last_exc is not None:
            raise TranscriptUnavailable(f"could not download audio for Whisper: {last_exc}") from last_exc
        if duration and duration > max_minutes * 60:
            raise TranscriptUnavailable(
                f"video is {duration / 60:.0f} minutes, over {max_minutes} minute Whisper cap"
            )

        audio_files = list(tmp_dir.glob("audio.*"))
        if not audio_files:
            raise TranscriptUnavailable("yt-dlp did not produce an audio file")

        ranges = _audio_shard_ranges(duration, shard_seconds, overlap)
        split = duration > shard_seconds and len(ranges) > 1
        model = _load_whisper_model()
        texts: list[str] = []
        for index, (start, end) in enumerate(ranges, start=1):
            if split:
                shard_path = tmp_dir / f"shard-{index}.wav"
                logger.info(
                    "Whisper shard %s/%s (%.0f-%.0fs) for %s",
                    index,
                    len(ranges),
                    start,
                    end,
                    video_id,
                )
                _extract_audio_shard(audio_files[0], shard_path, start, end)
                audio_path = shard_path
            else:
                audio_path = audio_files[0]
            segments, _info = model.transcribe(str(audio_path), vad_filter=True)
            piece = _segment_text(segments)
            if piece:
                texts.append(piece)
        text = " ".join(texts).strip()
        if not text:
            raise TranscriptUnavailable("Whisper produced an empty transcript")
        return TranscriptResult(text=text, source="whisper")
