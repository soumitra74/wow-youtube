FROM denoland/deno:bin AS deno-bin

FROM python:3.12-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
COPY --from=deno-bin /deno /usr/local/bin/deno

WORKDIR /app

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_TORCH_BACKEND=cpu \
    UV_NO_CACHE=1

COPY pyproject.toml uv.lock ./
COPY apps ./apps
COPY packages ./packages

RUN uv sync --frozen --all-packages --no-dev \
    && /app/.venv/bin/python -c "import yt_dlp_ejs" \
    && /app/.venv/bin/yt-dlp --version \
    && find /app/.venv -type d -name __pycache__ -exec rm -rf {} + \
    && find /app/.venv -name "*.pyc" -delete

FROM python:3.12-slim

COPY --from=deno-bin /deno /usr/local/bin/deno
COPY --from=mwader/static-ffmpeg:7.1.1 /ffmpeg /usr/local/bin/ffmpeg
COPY --from=mwader/static-ffmpeg:7.1.1 /ffprobe /usr/local/bin/ffprobe

RUN deno --version && ffmpeg -version

WORKDIR /app

COPY --from=builder /app/.venv /app/.venv
COPY apps ./apps
COPY packages ./packages
COPY config ./config

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONPATH="/app/apps/api/src:/app/apps/poller/src:/app/packages/core/src" \
    WOW_ROOT=/app \
    WOW_CONFIG_DIR=/app/config \
    WOW_DATA_DIR=/app/data \
    PYTHONUNBUFFERED=1

EXPOSE 8000

CMD ["uvicorn", "wow_api.app:app", "--host", "0.0.0.0", "--port", "8000"]
