from __future__ import annotations

import json
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest

from wow_core.youtube import (
    FeedVideo,
    NotificationAuthError,
    TranscriptResult,
    TranscriptUnavailable,
    _audio_shard_ranges,
    _caption_payload_to_text,
    _entry_video_id,
    _fetch_youtube_captions,
    _fetch_ytdlp_captions,
    _json3_to_text,
    _pick_ytdlp_caption,
    _transcribe_with_whisper,
    _vtt_to_text,
    _resolve_channel_id,
    _resolve_video_metadata,
    _youtube_player_clients,
    _YtDlpWarningLogger,
    fetch_channel_feed,
    fetch_notification_videos,
    fetch_playlist_videos,
    fetch_watch_later_videos,
    merge_inbox_and_playlist_videos,
    get_transcript,
    notification_entries_to_videos,
    truncate_transcript,
)


def _write_login_cookies(path: Path, *, login_info: bool = True, sapisid: bool = True) -> None:
    lines = ["# Netscape HTTP Cookie File"]
    if login_info:
        lines.append(".youtube.com\tTRUE\t/\tTRUE\t2147483647\tLOGIN_INFO\tfake-login")
    if sapisid:
        lines.append(".youtube.com\tTRUE\t/\tTRUE\t2147483647\tSAPISID\tfake-sapisid")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _fake_ydl(captured: dict, *, rewrite: bool = False, error: Exception | None = None):
    class FakeYDL:
        def __init__(self, opts):
            captured["opts"] = opts
            if rewrite:
                Path(opts["cookiefile"]).write_text("# rewritten by yt-dlp\n", encoding="utf-8")

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def extract_info(self, url, download=True):
            if error:
                raise error
            captured["url"] = url
            captured["download"] = download
            return {
                "entries": [
                    {
                        "id": "aaaaaaaaaaa",
                        "title": "Latest",
                        "channel_id": "UCchan1",
                        "channel": "Demo",
                        "upload_date": "20260822",
                    }
                ]
            }

    return type("M", (), {"YoutubeDL": FakeYDL})


def test_entry_video_id_from_yt_field() -> None:
    assert _entry_video_id(SimpleNamespace(yt_videoid="abc123", link="")) == "abc123"


def test_entry_video_id_from_watch_url() -> None:
    entry = SimpleNamespace(link="https://www.youtube.com/watch?v=abc123xyz78&t=12s")
    assert _entry_video_id(entry) == "abc123xyz78"


def test_parse_youtube_video_url() -> None:
    from wow_core.youtube import parse_youtube_video_url

    assert parse_youtube_video_url("aaaaaaaaaaa") == "aaaaaaaaaaa"
    assert parse_youtube_video_url("https://www.youtube.com/watch?v=bbbbbbbbbbb") == "bbbbbbbbbbb"
    assert parse_youtube_video_url("https://youtu.be/ccccccccccc") == "ccccccccccc"
    assert parse_youtube_video_url("https://youtube.com/shorts/ddddddddddd") == "ddddddddddd"
    assert parse_youtube_video_url("not-a-url") is None


def test_truncate_transcript() -> None:
    text, truncated = truncate_transcript("hello world", max_chars=5)
    assert truncated is True
    assert text == "hello"
    text, truncated = truncate_transcript("short", max_chars=20)
    assert truncated is False
    assert text == "short"


def test_notification_entries_to_videos_respects_limit_and_skips_non_videos() -> None:
    info = {
        "entries": [
            {
                "id": "aaaaaaaaaaa",
                "title": "First",
                "channel_id": "UCchan1",
                "channel": "Physics Weekly",
                "url": "https://www.youtube.com/watch?v=aaaaaaaaaaa",
                "upload_date": "20260820",
            },
            {
                "id": "community-post",
                "title": "A post",
                "channel_id": "UCchan1",
                "url": "https://www.youtube.com/channel/UCchan1/community?lb=xyz",
            },
            {
                "video_id": "bbbbbbbbbbb",
                "title": "Second",
                "channel_id": "UCchan2",
                "uploader": "Chem Lab",
                "timestamp": 1755907200,
            },
            {
                "id": "ccccccccccc",
                "title": "Third",
                "channel_id": "UCchan3",
                "channel": "Skipped by limit",
            },
        ]
    }
    videos = notification_entries_to_videos(info, limit=2)
    assert [v.video_id for v in videos] == ["aaaaaaaaaaa", "bbbbbbbbbbb"]
    assert videos[0].channel_name == "Physics Weekly"
    assert videos[0].published_date.startswith("2026-08-20")
    assert videos[1].channel_id == "UCchan2"
    assert videos[1].url.endswith("bbbbbbbbbbb")


def test_notification_entries_skips_metadata_when_channel_known() -> None:
    info = {
        "entries": [
            {"id": "aaaaaaaaaaa", "title": "No RSS date", "channel_id": "UCchan1", "channel": "A"},
        ]
    }

    def should_not_resolve(_video_id: str) -> None:
        raise AssertionError("metadata resolve should not run when channel_id is present")

    videos = notification_entries_to_videos(
        info,
        limit=10,
        resolve_metadata=should_not_resolve,  # type: ignore[arg-type]
    )
    assert videos[0].published_date == ""


def test_notification_entries_channel_id_from_channel_url() -> None:
    info = {
        "entries": [
            {
                "id": "aaaaaaaaaaa",
                "title": "From channel url",
                "url": "https://www.youtube.com/channel/UCchan9",
            },
        ]
    }
    videos = notification_entries_to_videos(info, limit=10)
    assert videos[0].channel_id == "UCchan9"


def test_fetch_channel_feed_keeps_plain_description(monkeypatch) -> None:
    import sys
    import types

    entry = SimpleNamespace(
        yt_videoid="aaaaaaaaaaa",
        title="Launch",
        published="2026-08-01T00:00:00+00:00",
        link="https://www.youtube.com/watch?v=aaaaaaaaaaa",
        media_description=None,
        description=None,
        summary="<p>Booster notes &amp; links</p>",
    )
    fake_feedparser = types.ModuleType("feedparser")
    fake_feedparser.parse = lambda _url: SimpleNamespace(bozo=False, entries=[entry])  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "feedparser", fake_feedparser)

    videos = fetch_channel_feed("UCchan", "https://example.invalid/rss")

    assert videos[0].description == "Booster notes & links"


def test_notification_entries_keep_description_from_flat_row_or_metadata() -> None:
    from wow_core.youtube import ResolvedVideoMeta

    listed = notification_entries_to_videos(
        {
            "entries": [
                {
                    "id": "aaaaaaaaaaa",
                    "title": "Listed",
                    "channel_id": "UCchan1",
                    "description": "Shownotes",
                }
            ]
        },
        limit=5,
    )
    assert listed[0].description == "Shownotes"

    resolved = notification_entries_to_videos(
        {"entries": [{"id": "bbbbbbbbbbb", "title": "Needs lookup"}]},
        limit=5,
        resolve_metadata=lambda _video_id: ResolvedVideoMeta(
            channel_id="UClooked",
            description="From the video page",
        ),
    )
    assert resolved[0].description == "From the video page"


def test_notification_entries_dedupe_and_resolve_missing_channel_id() -> None:
    info = {
        "entries": [
            {"id": "aaaaaaaaaaa", "title": "Dup A", "channel_id": "UCchan1", "channel": "A"},
            {"id": "aaaaaaaaaaa", "title": "Dup B", "channel_id": "UCchan1", "channel": "A"},
            {"id": "bbbbbbbbbbb", "title": "Needs lookup", "channel": "B"},
        ]
    }
    from wow_core.youtube import ResolvedVideoMeta

    videos = notification_entries_to_videos(
        info,
        limit=10,
        resolve_metadata=lambda video_id: ResolvedVideoMeta(channel_id="UClooked"),
    )
    assert [v.video_id for v in videos] == ["aaaaaaaaaaa", "bbbbbbbbbbb"]
    assert videos[1].channel_id == "UClooked"


def test_fetch_notification_videos_uses_env_limit_and_cookies(tmp_path: Path, monkeypatch) -> None:
    cookies = tmp_path / "youtube_cookies.txt"
    _write_login_cookies(cookies)
    original = cookies.read_text(encoding="utf-8")
    monkeypatch.setattr("wow_core.settings.YOUTUBE_COOKIES_PATH", cookies)
    monkeypatch.setattr("wow_core.settings.YOUTUBE_NOTIFICATIONS_SCAN_LIMIT", 3)
    captured: dict = {}
    monkeypatch.setattr("wow_core.youtube._youtube_dl", lambda: _fake_ydl(captured, rewrite=True))
    videos = fetch_notification_videos()
    assert captured["url"] == ":ytnotif"
    assert captured["download"] is False
    assert captured["opts"]["playlistend"] == 3
    assert captured["opts"]["cookiefile"] != str(cookies)
    assert cookies.read_text(encoding="utf-8") == original
    assert [v.video_id for v in videos] == ["aaaaaaaaaaa"]


def test_fetch_watch_later_videos_uses_playlist_url(tmp_path: Path, monkeypatch) -> None:
    cookies = tmp_path / "youtube_cookies.txt"
    _write_login_cookies(cookies)
    monkeypatch.setattr("wow_core.settings.YOUTUBE_COOKIES_PATH", cookies)
    monkeypatch.setattr("wow_core.settings.YOUTUBE_WATCH_LATER_SCAN_LIMIT", 5)
    captured: dict = {}
    monkeypatch.setattr("wow_core.youtube._youtube_dl", lambda: _fake_ydl(captured, rewrite=True))
    videos = fetch_watch_later_videos()
    assert "playlist?list=WL" in captured["url"]
    assert captured["opts"]["playlistend"] == 5
    assert [v.video_id for v in videos] == ["aaaaaaaaaaa"]


def test_merge_inbox_and_playlist_videos_dedupes(tmp_path: Path, monkeypatch) -> None:
    cookies = tmp_path / "youtube_cookies.txt"
    _write_login_cookies(cookies)
    monkeypatch.setattr("wow_core.settings.YOUTUBE_COOKIES_PATH", cookies)
    monkeypatch.setattr(
        "wow_core.youtube.fetch_notification_videos",
        lambda scan_limit=None: [
            FeedVideo(
                video_id="aaaaaaaaaaa",
                title="Inbox",
                published_date="",
                url="",
                channel_id="UC1",
            )
        ],
    )
    monkeypatch.setattr(
        "wow_core.youtube.fetch_watch_later_videos",
        lambda scan_limit=None: [
            FeedVideo(
                video_id="aaaaaaaaaaa",
                title="Dup",
                published_date="",
                url="",
                channel_id="UC1",
            ),
            FeedVideo(
                video_id="bbbbbbbbbbb",
                title="WL only",
                published_date="",
                url="",
                channel_id="UC2",
            ),
        ],
    )
    monkeypatch.setattr("wow_core.settings.YOUTUBE_FETCH_WATCH_LATER", True)
    monkeypatch.setattr("wow_core.settings.YOUTUBE_EXTRA_PLAYLIST_IDS", [])
    merged = merge_inbox_and_playlist_videos()
    assert [v.video_id for v in merged] == ["aaaaaaaaaaa", "bbbbbbbbbbb"]


def test_fetch_playlist_videos_custom_id(tmp_path: Path, monkeypatch) -> None:
    cookies = tmp_path / "youtube_cookies.txt"
    _write_login_cookies(cookies)
    monkeypatch.setattr("wow_core.settings.YOUTUBE_COOKIES_PATH", cookies)
    captured: dict = {}
    monkeypatch.setattr("wow_core.youtube._youtube_dl", lambda: _fake_ydl(captured))
    fetch_playlist_videos("PLcustom123", scan_limit=2)
    assert captured["url"] == "https://www.youtube.com/playlist?list=PLcustom123"
    assert captured["opts"]["playlistend"] == 2


def test_fetch_notification_videos_requires_cookies(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("wow_core.settings.YOUTUBE_COOKIES_PATH", tmp_path / "missing.txt")
    with pytest.raises(NotificationAuthError, match="not found"):
        fetch_notification_videos()


def test_fetch_notification_videos_requires_login_info(tmp_path: Path, monkeypatch) -> None:
    cookies = tmp_path / "youtube_cookies.txt"
    _write_login_cookies(cookies, login_info=False)
    monkeypatch.setattr("wow_core.settings.YOUTUBE_COOKIES_PATH", cookies)
    called = {"n": 0}

    def boom():
        called["n"] += 1
        raise AssertionError("yt-dlp should not run without LOGIN_INFO")

    monkeypatch.setattr("wow_core.youtube._youtube_dl", boom)
    with pytest.raises(NotificationAuthError, match="LOGIN_INFO"):
        fetch_notification_videos()
    assert called["n"] == 0


def test_fetch_notification_videos_maps_yt_dlp_login_error(tmp_path: Path, monkeypatch) -> None:
    cookies = tmp_path / "youtube_cookies.txt"
    _write_login_cookies(cookies)
    monkeypatch.setattr("wow_core.settings.YOUTUBE_COOKIES_PATH", cookies)
    captured: dict = {}
    monkeypatch.setattr(
        "wow_core.youtube._youtube_dl",
        lambda: _fake_ydl(
            captured,
            error=Exception(
                "ERROR: [youtube:notif] Login details are needed to download this content"
            ),
        ),
    )
    with pytest.raises(NotificationAuthError, match="logged-in"):
        fetch_notification_videos()


def test_resolve_channel_id_uses_safari_client_and_cookie_copy(tmp_path: Path, monkeypatch) -> None:
    cookies = tmp_path / "youtube_cookies.txt"
    _write_login_cookies(cookies)
    original = cookies.read_text(encoding="utf-8")
    monkeypatch.setattr("wow_core.settings.YOUTUBE_COOKIES_PATH", cookies)
    captured: dict = {}

    class FakeYDL:
        def __init__(self, opts):
            captured["opts"] = opts
            Path(opts["cookiefile"]).write_text("# rewritten by yt-dlp\n", encoding="utf-8")

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def extract_info(self, url, download=True):
            captured["url"] = url
            captured["download"] = download
            return {
                "channel_id": "UCresolvedchannelidxx",
                "title": "Ignored",
                "upload_date": "20260910",
                "description": "<p>Flight notes &amp; links</p>",
            }

    monkeypatch.setattr("wow_core.youtube._youtube_dl", lambda: type("M", (), {"YoutubeDL": FakeYDL}))
    meta = _resolve_video_metadata("aaaaaaaaaaa")
    assert meta is not None
    assert meta.channel_id == "UCresolvedchannelidxx"
    assert meta.published_date.startswith("2026-09-10")
    assert meta.description == "Flight notes & links"
    assert _resolve_channel_id("aaaaaaaaaaa") == "UCresolvedchannelidxx"
    assert captured["url"] == "https://www.youtube.com/watch?v=aaaaaaaaaaa"
    assert captured["download"] is False
    assert captured["opts"]["cookiefile"] != str(cookies)
    assert captured["opts"]["ignore_no_formats_error"] is True
    assert captured["opts"]["extractor_args"]["youtube"]["player_client"] == _youtube_player_clients(
        cookies=True
    )
    assert cookies.read_text(encoding="utf-8") == original


def test_audio_shard_ranges_keeps_short_audio_as_one_clip() -> None:
    assert _audio_shard_ranges(120, 600, 5) == [(0.0, 120.0)]


def test_audio_shard_ranges_splits_long_audio_with_overlap() -> None:
    ranges = _audio_shard_ranges(1000, 400, 10)
    assert ranges[0] == (0.0, 400.0)
    assert ranges[1] == (390.0, 790.0)
    assert ranges[2] == (780.0, 1000.0)
    assert ranges[-1][1] == 1000.0


def test_vtt_to_text_strips_cues_and_tags() -> None:
    raw = """WEBVTT

00:00:00.000 --> 00:00:01.500
Hello <c>world</c>

00:00:01.500 --> 00:00:03.000
from vtt
"""
    assert _vtt_to_text(raw) == "Hello world from vtt"


def test_json3_to_text_joins_segments() -> None:
    payload = {
        "events": [
            {"segs": [{"utf8": "Hello "}, {"utf8": "there"}]},
            {"tStartMs": 0},
            {"segs": [{"utf8": " folks"}]},
        ]
    }
    assert _json3_to_text(json.dumps(payload)) == "Hello there folks"


def test_caption_payload_prefers_json3_when_ext_says_so() -> None:
    payload = json.dumps({"events": [{"segs": [{"utf8": "json3 text"}]}]})
    assert _caption_payload_to_text(payload.encode(), "json3") == "json3 text"


def test_pick_ytdlp_caption_prefers_manual_english() -> None:
    info = {
        "subtitles": {"en": [{"ext": "json3", "url": "https://example/manual"}]},
        "automatic_captions": {"en": [{"ext": "json3", "url": "https://example/auto"}]},
    }
    tracks, source = _pick_ytdlp_caption(info)
    assert source == "manual"
    assert tracks[0]["url"] == "https://example/manual"


def test_pick_ytdlp_caption_falls_back_to_auto_english() -> None:
    info = {
        "subtitles": {},
        "automatic_captions": {"en-US": [{"ext": "vtt", "url": "https://example/auto"}]},
    }
    tracks, source = _pick_ytdlp_caption(info)
    assert source == "auto"
    assert tracks[0]["url"] == "https://example/auto"


def test_throttle_captions_skips_sleep_on_first_call(monkeypatch) -> None:
    import wow_core.youtube as youtube

    monkeypatch.setattr("wow_core.settings.CAPTION_REQUEST_DELAY_SECONDS", 3.0)
    youtube._last_caption_at = 0.0
    slept: list[float] = []
    youtube._throttle_captions(now=lambda: 10.0, sleeper=slept.append)
    assert slept == []
    assert youtube._last_caption_at == 10.0


def test_throttle_captions_sleeps_remaining_delay(monkeypatch) -> None:
    import wow_core.youtube as youtube

    monkeypatch.setattr("wow_core.settings.CAPTION_REQUEST_DELAY_SECONDS", 3.0)
    youtube._last_caption_at = 10.0
    slept: list[float] = []
    youtube._throttle_captions(now=lambda: 11.0, sleeper=slept.append)
    assert slept == [2.0]


def test_throttle_captions_disabled_when_delay_is_zero(monkeypatch) -> None:
    import wow_core.youtube as youtube

    monkeypatch.setattr("wow_core.settings.CAPTION_REQUEST_DELAY_SECONDS", 0)
    youtube._last_caption_at = 10.0
    slept: list[float] = []
    youtube._throttle_captions(now=lambda: 11.0, sleeper=slept.append)
    assert slept == []


def test_fetch_youtube_captions_returns_none_when_ip_blocked(monkeypatch) -> None:
    class IpBlocked(Exception):
        pass

    class Track:
        is_generated = False
        language_code = "en"

        def fetch(self):
            raise IpBlocked("blocked")

    class Api:
        def list(self, video_id):
            return [Track()]

    monkeypatch.setattr("wow_core.youtube._transcript_api", lambda: Api())
    assert _fetch_youtube_captions("aaaaaaaaaaa") is None


def test_fetch_ytdlp_captions_retries_on_429(tmp_path: Path, monkeypatch) -> None:
    cookies = tmp_path / "youtube_cookies.txt"
    _write_login_cookies(cookies)
    monkeypatch.setattr("wow_core.settings.YOUTUBE_COOKIES_PATH", cookies)
    monkeypatch.setattr("wow_core.settings.CAPTION_RATE_LIMIT_RETRIES", 2)
    monkeypatch.setattr("wow_core.settings.CAPTION_RATE_LIMIT_BACKOFF_SECONDS", 0.01)
    monkeypatch.setattr("wow_core.youtube._throttle_captions", lambda **kwargs: None)
    calls = {"n": 0}

    class FakeResp:
        def read(self):
            return json.dumps({"events": [{"segs": [{"utf8": "ok"}]}]}).encode()

    class FakeYDL:
        def __init__(self, opts):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def extract_info(self, url, download=False):
            calls["n"] += 1
            if calls["n"] < 3:
                raise Exception("HTTP Error 429: Too Many Requests")
            return {
                "subtitles": {"en": [{"ext": "json3", "url": "https://example/json3"}]},
            }

        def urlopen(self, url):
            return FakeResp()

    monkeypatch.setattr("wow_core.youtube._youtube_dl", lambda: type("M", (), {"YoutubeDL": FakeYDL})())
    result = _fetch_ytdlp_captions("aaaaaaaaaaa")
    assert result is not None
    assert result.text == "ok"
    assert calls["n"] == 3


def test_fetch_ytdlp_captions_uses_cookies_and_manual_json3(tmp_path: Path, monkeypatch) -> None:
    cookies = tmp_path / "youtube_cookies.txt"
    _write_login_cookies(cookies)
    monkeypatch.setattr("wow_core.settings.YOUTUBE_COOKIES_PATH", cookies)
    captured: dict = {}

    class FakeResp:
        def read(self):
            return json.dumps({"events": [{"segs": [{"utf8": "Hello "}, {"utf8": "from cookies"}]}]}).encode()

    class FakeYDL:
        def __init__(self, opts):
            captured["opts"] = opts

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def extract_info(self, url, download=False):
            captured["url"] = url
            return {
                "subtitles": {
                    "en": [
                        {"ext": "vtt", "url": "https://example/vtt"},
                        {"ext": "json3", "url": "https://example/json3"},
                    ]
                },
                "automatic_captions": {"en": [{"ext": "json3", "url": "https://example/auto"}]},
            }

        def urlopen(self, url):
            captured["caption_url"] = url
            return FakeResp()

    monkeypatch.setattr("wow_core.youtube._youtube_dl", lambda: type("M", (), {"YoutubeDL": FakeYDL}))
    result = _fetch_ytdlp_captions("aaaaaaaaaaa")
    assert captured["url"].endswith("aaaaaaaaaaa")
    assert captured["opts"]["cookiefile"]
    assert captured["opts"]["ignore_no_formats_error"] is True
    assert captured["opts"]["extractor_args"]["youtube"]["player_client"] == _youtube_player_clients(
        cookies=True
    )
    assert "-tv_downgraded" in captured["opts"]["extractor_args"]["youtube"]["player_client"]
    assert captured["caption_url"] == "https://example/json3"
    assert result is not None
    assert result.text == "Hello from cookies"
    assert result.source == "manual"


def test_get_transcript_uses_ytdlp_when_timedtext_missing(monkeypatch) -> None:
    monkeypatch.setattr("wow_core.youtube._throttle_captions", lambda **kwargs: None)
    monkeypatch.setattr("wow_core.youtube._fetch_youtube_captions", lambda _id: None)
    monkeypatch.setattr(
        "wow_core.youtube._fetch_ytdlp_captions",
        lambda _id: TranscriptResult(text="from ytdlp", source="manual"),
    )

    def fail_whisper(*_args, **_kwargs):
        raise AssertionError("whisper should not run when yt-dlp captions exist")

    monkeypatch.setattr("wow_core.youtube._transcribe_with_whisper", fail_whisper)
    result = get_transcript("aaaaaaaaaaa", generate_if_missing=True)
    assert result.text == "from ytdlp"
    assert result.source == "manual"


def test_get_transcript_uses_whisper_when_both_caption_sources_fail(monkeypatch) -> None:
    monkeypatch.setattr("wow_core.youtube._throttle_captions", lambda **kwargs: None)
    monkeypatch.setattr("wow_core.youtube._fetch_youtube_captions", lambda _id: None)
    monkeypatch.setattr("wow_core.youtube._fetch_ytdlp_captions", lambda _id: None)
    monkeypatch.setattr(
        "wow_core.youtube._transcribe_with_whisper",
        lambda _id, max_minutes: TranscriptResult(text="from whisper", source="whisper"),
    )
    result = get_transcript("aaaaaaaaaaa", generate_if_missing=True)
    assert result.source == "whisper"
    assert result.text == "from whisper"


def test_whisper_transcribes_long_audio_in_shards(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("wow_core.settings.WHISPER_DIR", tmp_path)
    monkeypatch.setattr("wow_core.settings.WHISPER_SHARD_MINUTES", 10)
    monkeypatch.setattr("wow_core.settings.WHISPER_SHARD_OVERLAP_SECONDS", 0)
    captured: dict = {"shards": [], "extracts": []}

    class FakeYDL:
        def __init__(self, opts):
            self.opts = opts

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def extract_info(self, url, download=False):
            return {"duration": 1500}

        def download(self, urls):
            Path(self.opts["outtmpl"].replace("%(ext)s", "mp3")).write_bytes(b"audio")

    class FakeSegment:
        def __init__(self, text: str):
            self.text = text

    class FakeModel:
        def transcribe(self, path, vad_filter=True):
            captured["shards"].append(Path(path).name)
            return [FakeSegment(f"part-{Path(path).name}")], None

    def fake_extract(src, dest, start, end):
        captured["extracts"].append((round(start), round(end)))
        dest.write_bytes(b"shard")

    monkeypatch.setattr("wow_core.youtube._youtube_dl", lambda: type("M", (), {"YoutubeDL": FakeYDL}))
    monkeypatch.setattr("wow_core.youtube._load_whisper_model", lambda: FakeModel())
    monkeypatch.setattr("wow_core.youtube._extract_audio_shard", fake_extract)
    result = _transcribe_with_whisper("aaaaaaaaaaa", max_minutes=90)
    assert captured["extracts"] == [(0, 600), (600, 1200), (1200, 1500)]
    assert len(captured["shards"]) == 3
    assert result.source == "whisper"
    assert "part-" in result.text


def test_whisper_does_not_split_short_audio(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("wow_core.settings.WHISPER_DIR", tmp_path)
    monkeypatch.setattr("wow_core.settings.WHISPER_SHARD_MINUTES", 10)
    extracts: list = []

    class FakeYDL:
        def __init__(self, opts):
            self.opts = opts

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def extract_info(self, url, download=False):
            return {"duration": 120}

        def download(self, urls):
            Path(self.opts["outtmpl"].replace("%(ext)s", "mp3")).write_bytes(b"audio")

    class FakeSegment:
        def __init__(self, text: str):
            self.text = text

    class FakeModel:
        def transcribe(self, path, vad_filter=True):
            return [FakeSegment("whole file")], None

    monkeypatch.setattr("wow_core.youtube._youtube_dl", lambda: type("M", (), {"YoutubeDL": FakeYDL}))
    monkeypatch.setattr("wow_core.youtube._load_whisper_model", lambda: FakeModel())
    monkeypatch.setattr(
        "wow_core.youtube._extract_audio_shard",
        lambda *args, **kwargs: extracts.append(args),
    )
    result = _transcribe_with_whisper("aaaaaaaaaaa", max_minutes=90)
    assert extracts == []
    assert result.text == "whole file"


def test_youtube_player_clients_avoid_broken_tv_client_when_logged_in() -> None:
    with_cookies = _youtube_player_clients(cookies=True)
    assert "web_safari" in with_cookies
    assert "web_embedded" in with_cookies
    assert "-tv_downgraded" in with_cookies
    assert "tv_downgraded" not in [c for c in with_cookies if not c.startswith("-")]
    assert "android" not in with_cookies
    anonymous = _youtube_player_clients(cookies=False)
    assert "android" in anonymous
    assert "-tv_downgraded" not in anonymous


def test_whisper_retries_without_cookies_when_format_unavailable(tmp_path: Path, monkeypatch) -> None:
    cookies = tmp_path / "youtube_cookies.txt"
    _write_login_cookies(cookies)
    monkeypatch.setattr("wow_core.settings.YOUTUBE_COOKIES_PATH", cookies)
    monkeypatch.setattr("wow_core.settings.WHISPER_DIR", tmp_path)
    attempts: list[bool] = []

    class FakeYDL:
        def __init__(self, opts):
            self.opts = opts
            attempts.append("cookiefile" in opts)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def extract_info(self, url, download=False):
            return {"duration": 120}

        def download(self, urls):
            if self.opts.get("cookiefile"):
                raise Exception(
                    "ERROR: [youtube] aaaaaaaaaaa: Requested format is not available. "
                    "Use --list-formats for a list of available formats"
                )
            Path(self.opts["outtmpl"].replace("%(ext)s", "mp3")).write_bytes(b"audio")

    class FakeSegment:
        def __init__(self, text: str):
            self.text = text

    class FakeModel:
        def transcribe(self, path, vad_filter=True):
            return [FakeSegment("from android client")], None

    monkeypatch.setattr("wow_core.youtube._youtube_dl", lambda: type("M", (), {"YoutubeDL": FakeYDL}))
    monkeypatch.setattr("wow_core.youtube._load_whisper_model", lambda: FakeModel())
    result = _transcribe_with_whisper("aaaaaaaaaaa", max_minutes=90)
    assert attempts[0] is True
    assert False in attempts
    assert result.text == "from android client"


def test_ytdlp_logger_emits_errors_as_warnings(caplog) -> None:
    caplog.set_level(logging.WARNING)
    _YtDlpWarningLogger().error(
        "ERROR: [youtube] aaaaaaaaaaa: Requested format is not available"
    )
    assert "Requested format is not available" in caplog.text
    assert all(record.levelno == logging.WARNING for record in caplog.records)


def test_whisper_maps_reload_failure_to_unavailable(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("wow_core.settings.WHISPER_DIR", tmp_path)

    class FakeYDL:
        def __init__(self, opts):
            self.opts = opts

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def extract_info(self, url, download=False):
            raise Exception("ERROR: [youtube] aaaaaaaaaaa: The page needs to be reloaded.")

        def download(self, urls):
            raise AssertionError("download should not run")

    monkeypatch.setattr("wow_core.youtube._youtube_dl", lambda: type("M", (), {"YoutubeDL": FakeYDL}))
    with pytest.raises(TranscriptUnavailable, match="page needs to be reloaded"):
        _transcribe_with_whisper("aaaaaaaaaaa", max_minutes=90)
