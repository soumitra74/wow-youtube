from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from wow_core import settings
from wow_core.logging_setup import configure_server_logging
from wow_core.chroma_store import delete_summary, query_similar
from wow_core.claude_client import ask_across_videos
from wow_core.db import (
    delete_video,
    get_video,
    get_videos_by_ids,
    init_db,
    list_channels,
    query_videos,
    set_watched,
    topic_trends,
)
from wow_core.fetch_status import get_fetch_progress
from wow_core.youtube import NotificationAuthError
from wow_poller.pipeline import run_fetch_url, run_latest

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


@app.get("/api/video")
def api_video_list(
    channel_id: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    date_field: str = "published",
    topic: str | None = None,
    relevance: str | None = None,
    watched: bool | None = None,
    q: str | None = Query(default=None, description="Keyword search over title and summary"),
) -> list[dict[str, Any]]:
    if date_field not in {"published", "processed"}:
        raise HTTPException(status_code=400, detail="date_field must be published or processed")
    return query_videos(
        channel_id=channel_id,
        date_from=date_from,
        date_to=date_to,
        date_field=date_field,
        topic=topic,
        relevance=relevance,
        watched=watched,
        keyword=q,
    )


@app.get("/api/video/{video_id}")
def api_video_detail(video_id: str) -> dict[str, Any]:
    video = get_video(video_id)
    if video is None:
        raise HTTPException(status_code=404, detail="video not found")
    return video


@app.delete("/api/video/{video_id}", status_code=204)
def api_video_delete(video_id: str) -> Response:
    if get_video(video_id) is None:
        raise HTTPException(status_code=404, detail="video not found")
    try:
        delete_summary(video_id)
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail="could not remove video from semantic index",
        ) from exc
    if not delete_video(video_id):
        raise HTTPException(status_code=404, detail="video not found")
    return Response(status_code=204)


class VideoPatchBody(BaseModel):
    watched: bool


@app.patch("/api/video/{video_id}")
def api_video_patch(video_id: str, body: VideoPatchBody) -> dict[str, Any]:
    if not set_watched(video_id, body.watched):
        raise HTTPException(status_code=404, detail="video not found")
    return {"video_id": video_id, "watched": body.watched}


@app.get("/api/query")
def api_query(q: str, k: int | None = None) -> list[dict[str, Any]]:
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


class AnswerBody(BaseModel):
    question: str = Field(min_length=1)
    k: int | None = None


@app.post("/api/answers")
def api_answers(body: AnswerBody) -> dict[str, Any]:
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


def _run_fetch_job() -> None:
    progress = get_fetch_progress()
    try:
        result = run_latest()
        progress.complete(result)
    except NotificationAuthError as exc:
        progress.fail(str(exc))
    except Exception as exc:
        progress.fail(f"YouTube fetch failed: {exc}")


def _run_fetch_url_job(url: str) -> None:
    progress = get_fetch_progress()
    try:
        result = run_fetch_url(url)
        progress.complete(result)
    except ValueError as exc:
        progress.fail(str(exc))
    except Exception as exc:
        progress.fail(f"YouTube fetch failed: {exc}")


def _begin_sync_run() -> None:
    progress = get_fetch_progress()
    if progress.is_running():
        raise HTTPException(status_code=409, detail="Fetch already in progress")
    try:
        progress.begin()
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.get("/api/videos")
def api_videos_sync_status() -> dict[str, Any]:
    return get_fetch_progress().snapshot()


@app.post("/api/videos")
def api_videos_sync_start() -> JSONResponse:
    _begin_sync_run()
    threading.Thread(target=_run_fetch_job, name="fetch-latest", daemon=True).start()
    return JSONResponse({"started": True}, status_code=202)


class VideoImportBody(BaseModel):
    url: str = Field(min_length=1)


@app.post("/api/video")
def api_video_import(body: VideoImportBody) -> JSONResponse:
    url = body.url.strip()
    if not url:
        raise HTTPException(status_code=400, detail="url is required")
    _begin_sync_run()
    threading.Thread(
        target=_run_fetch_url_job,
        args=(url,),
        name="fetch-url",
        daemon=True,
    ).start()
    return JSONResponse({"started": True}, status_code=202)


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
