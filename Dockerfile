FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg \
        curl \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

COPY pyproject.toml ./
COPY apps ./apps
COPY packages ./packages
COPY config ./config

RUN uv sync --all-packages

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONPATH="/app/apps/api/src:/app/apps/poller/src:/app/packages/core/src" \
    WOW_ROOT=/app \
    WOW_CONFIG_DIR=/app/config \
    WOW_DATA_DIR=/app/data \
    PYTHONUNBUFFERED=1

EXPOSE 8000

CMD ["uvicorn", "wow_api.app:app", "--host", "0.0.0.0", "--port", "8000"]
