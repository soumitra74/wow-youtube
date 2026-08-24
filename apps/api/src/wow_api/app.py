from __future__ import annotations

from pathlib import Path
from typing import Any

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from wow_core import settings
from wow_core.logging_setup import configure_server_logging
from wow_core.chroma_store import query_similar
from wow_core.claude_client import ask_across_videos
from wow_core.db import get_videos_by_ids, init_db, list_channels, query_videos, set_watched, topic_trends
from wow_core.youtube import NotificationAuthError
from wow_poller.pipeline import run_latest

STATIC_DIR = Path(__file__).resolve().parent / "static"

configure_server_logging()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    configure_server_logging()
    settings.ensure_data_dirs()
    init_db()
    yield


app = FastAPI(title="YouTube Feed Digest", version="0.1.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def _index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/")
def index() -> FileResponse:
    return _index()


@app.get("/index.html")
def index_html() -> FileResponse:
    return _index()


@app.get("/favicon.ico")
def favicon() -> Response:
    icon = STATIC_DIR / "favicon.ico"
    if icon.exists():
        return FileResponse(icon)
    return Response(status_code=204)


@app.get("/api/channels")
def api_channels() -> list[dict[str, Any]]:
    return list_channels()


@app.get("/api/videos")
def api_videos(
    channel_id: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    topic: str | None = None,
    relevance: str | None = None,
    watched: bool | None = None,
    q: str | None = Query(default=None, description="Keyword search over title and summary"),
) -> list[dict[str, Any]]:
    return query_videos(
        channel_id=channel_id,
        date_from=date_from,
        date_to=date_to,
        topic=topic,
        relevance=relevance,
        watched=watched,
        keyword=q,
    )


@app.get("/api/search/semantic")
def api_semantic(q: str, k: int | None = None) -> list[dict[str, Any]]:
    query = q.strip()
    if not query:
        raise HTTPException(status_code=400, detail="q is required")
    hits = query_similar(query, top_k=k)
    videos = {video["video_id"]: video for video in get_videos_by_ids([h["video_id"] for h in hits])}
    results = []
    for hit in hits:
        video = videos.get(hit["video_id"])
        if not video:
            continue
        results.append({**video, "distance": hit["distance"]})
    return results


class AskBody(BaseModel):
    question: str = Field(min_length=1)
    k: int | None = None


@app.post("/api/ask")
def api_ask(body: AskBody) -> dict[str, Any]:
    hits = query_similar(body.question, top_k=body.k)
    videos = get_videos_by_ids([h["video_id"] for h in hits])
    answer = ask_across_videos(question=body.question, videos=videos)
    return {"answer": answer, "sources": videos}


@app.get("/api/trends")
def api_trends(period: str = "week", months: int = 3) -> list[dict[str, Any]]:
    if period not in {"week", "month"}:
        raise HTTPException(status_code=400, detail="period must be week or month")
    if months < 1 or months > 24:
        raise HTTPException(status_code=400, detail="months must be between 1 and 24")
    return topic_trends(months=months, bucket=period)  # type: ignore[arg-type]


@app.post("/api/fetch-latest")
def api_fetch_latest() -> dict[str, Any]:
    try:
        return run_latest()
    except NotificationAuthError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"YouTube fetch failed: {exc}") from exc


class WatchedBody(BaseModel):
    watched: bool


@app.patch("/api/videos/{video_id}/watched")
def api_watched(video_id: str, body: WatchedBody) -> dict[str, Any]:
    if not set_watched(video_id, body.watched):
        raise HTTPException(status_code=404, detail="video not found")
    return {"video_id": video_id, "watched": body.watched}


def main() -> None:
    import copy

    import uvicorn
    from uvicorn.config import LOGGING_CONFIG

    log_config = copy.deepcopy(LOGGING_CONFIG)
    for formatter in log_config["formatters"].values():
        fmt = formatter.get("fmt")
        if fmt and "%(asctime)s" not in fmt:
            formatter["fmt"] = "%(asctime)s " + fmt

    uvicorn.run(
        "wow_api.app:app",
        host="0.0.0.0",
        port=8000,
        reload=False,
        log_config=log_config,
    )
