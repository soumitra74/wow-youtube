from __future__ import annotations

import socket
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import uvicorn

from wow_api import app as api_module
from wow_core import settings
from wow_core.db import init_db, insert_video, sync_channels, upsert_seen
from wow_core.fetch_status import FetchProgress

ORBITALS_ID = "vid-orbitals"
ORBITALS_TITLE = "Orbitals Explained"
ROME_ID = "vid-rome"
ROME_TITLE = "The Fall of Rome"


def _recent(*, days_ago: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).replace(microsecond=0).isoformat()


def _seed_archive(db_path: Path) -> None:
    init_db(db_path)
    sync_channels(
        [
            {"channel_id": "chan1", "channel_name": "Physics Weekly"},
            {"channel_id": "chan2", "channel_name": "History Hour"},
        ],
        db_path=db_path,
    )
    orbitals_date = _recent(days_ago=2)
    rome_date = _recent(days_ago=5)
    upsert_seen(
        video_id=ORBITALS_ID,
        channel_id="chan1",
        status="processed",
        title=ORBITALS_TITLE,
        published_date=orbitals_date,
        url=f"https://www.youtube.com/watch?v={ORBITALS_ID}",
        db_path=db_path,
    )
    insert_video(
        video_id=ORBITALS_ID,
        channel_id="chan1",
        title=ORBITALS_TITLE,
        published_date=orbitals_date,
        url=f"https://www.youtube.com/watch?v={ORBITALS_ID}",
        transcript_length=1200,
        transcript="Full orbital mechanics transcript.",
        summary="A walkthrough of orbital mechanics.",
        long_summary="A detailed walkthrough of orbital mechanics and mission planning.",
        key_takeaways=["Inclination matters", "Delta-v is scarce"],
        topics=["space", "physics"],
        relevance="high",
        description="Orbital mechanics shownotes and reference links.",
        db_path=db_path,
    )
    upsert_seen(
        video_id=ROME_ID,
        channel_id="chan2",
        status="processed",
        title=ROME_TITLE,
        published_date=rome_date,
        url=f"https://www.youtube.com/watch?v={ROME_ID}",
        db_path=db_path,
    )
    insert_video(
        video_id=ROME_ID,
        channel_id="chan2",
        title=ROME_TITLE,
        published_date=rome_date,
        url=f"https://www.youtube.com/watch?v={ROME_ID}",
        transcript_length=800,
        transcript="A transcript about the late Roman empire.",
        summary="How Rome fell.",
        long_summary="A detailed account of the political and military collapse of Rome.",
        key_takeaways=["Borders stretched too far", "Institutions decayed"],
        topics=["history"],
        relevance="medium",
        db_path=db_path,
    )


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_for_server(url: str, server: uvicorn.Server, timeout: float = 15.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if server.started:
            try:
                urllib.request.urlopen(url, timeout=0.5)
                return
            except (urllib.error.URLError, TimeoutError, ConnectionError, OSError):
                pass
        time.sleep(0.05)
    raise RuntimeError(f"API server did not start at {url}")


@pytest.fixture
def live_server(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    data_dir = tmp_path / "data"
    db_path = data_dir / "wow.db"
    monkeypatch.setattr(settings, "DATA_DIR", data_dir)
    monkeypatch.setattr(settings, "DB_PATH", db_path)
    monkeypatch.setattr(settings, "CHROMA_DIR", data_dir / "chroma")
    monkeypatch.setattr(settings, "LOGS_DIR", data_dir / "logs")
    monkeypatch.setattr(settings, "DIGESTS_DIR", data_dir / "digests")
    monkeypatch.setattr(settings, "WHISPER_DIR", data_dir / "whisper")
    monkeypatch.setattr(settings, "HF_HOME", data_dir / "hf")

    progress = FetchProgress()
    monkeypatch.setattr(api_module, "get_fetch_progress", lambda: progress)
    monkeypatch.setattr(api_module, "delete_summary", lambda _video_id: None)
    monkeypatch.setattr(
        api_module,
        "query_similar",
        lambda _text, top_k=None: [{"video_id": ORBITALS_ID, "distance": 0.12}],
    )
    monkeypatch.setattr(
        api_module,
        "ask_across_videos",
        lambda question, videos: "Orbitals matter for mission planning.",
    )
    monkeypatch.setattr(
        api_module,
        "search_web",
        lambda question, video=None: (
            [{"title": "NASA update", "url": "https://www.nasa.gov/example", "snippet": "Recent mission news."}],
            "tavily",
        ),
    )
    monkeypatch.setattr(
        api_module,
        "ask_about_video",
        lambda video, messages, web_hits=None: "Inclination and delta-v drive mission design.",
    )
    monkeypatch.setattr(
        api_module,
        "run_latest",
        lambda: {"inbox_rows": 2, "scanned": 2, "processed": 1, "skipped": 1, "failed": 0},
    )
    monkeypatch.setattr(
        api_module,
        "run_fetch_url",
        lambda _url: {
            "inbox_rows": 1,
            "video_id": ORBITALS_ID,
            "processed": 1,
            "skipped": 0,
            "failed": 0,
            "outcome": "processed",
        },
    )

    _seed_archive(db_path)

    port = _free_port()
    config = uvicorn.Config(api_module.app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{port}"
    _wait_for_server(url, server)
    try:
        yield url
    finally:
        server.should_exit = True
        thread.join(timeout=5)
