FROM denoland/deno:bin AS deno-bin

FROM python:3.12-slim

COPY --from=deno-bin /deno /usr/local/bin/deno

RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg \
        curl \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && deno --version

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

COPY pyproject.toml ./
COPY apps ./apps
COPY packages ./packages
COPY config ./config

RUN uv sync --all-packages \
    && /app/.venv/bin/python -c "import yt_dlp_ejs" \
    && /app/.venv/bin/yt-dlp --version

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONPATH="/app/apps/api/src:/app/apps/poller/src:/app/packages/core/src" \
    WOW_ROOT=/app \
    WOW_CONFIG_DIR=/app/config \
    WOW_DATA_DIR=/app/data \
    PYTHONUNBUFFERED=1

EXPOSE 8000

CMD ["uvicorn", "wow_api.app:app", "--host", "0.0.0.0", "--port", "8000"]
