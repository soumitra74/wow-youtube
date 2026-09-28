# YouTube Feed Digest

Personal archive: poll followed channels, summarize new videos with Claude, store them in SQLite, index summaries in Chroma, and browse from a single FastAPI page.

## Dev setup (Docker, bind-mounted)

```bash
copy .env.example .env
# put your ANTHROPIC_API_KEY in .env

# add channels to config/channels.json:
# [{ "channel_id": "UCxxxxxxxx", "channel_name": "Example" }]

docker compose up --build
```

UI: http://localhost:8000

Inbox sync (refresh icon) and **Fetch Now** for a YouTube URL share one background job—starting either while the other is running is rejected (HTTP 409); wait for the current run to finish, then retry the URL import if needed.

`docker-compose.override.yml` bind-mounts `apps/`, `packages/`, `config/`, and `data/` and enables uvicorn `--reload`. Rebuild the image when Python dependencies change (includes Deno + `yt-dlp-ejs` for YouTube JS challenges). The image uses CPU PyTorch so Linux does not pull CUDA libraries.

Runtime settings live in `.env` (copy from `.env.example`). Poll budget:

| Variable | Default | Meaning |
|---|---|---|
| `POLL_LIMIT` | `10` | Max new videos per scheduled poll (`0` = unlimited) |
| `POLL_MAX_AGE_DAYS` | `3` | Skip RSS items older than this (`0` = no age filter) |

## Poll (same container)

```bash
docker compose exec wow python -m wow_poller
docker compose exec wow python -m wow_poller --retry-errors
# One-shot backlog fill (ignores POLL_LIMIT and POLL_MAX_AGE_DAYS):
docker compose exec wow python -m wow_poller --seed
```

Already-seen rows are skipped and do not count toward `POLL_LIMIT`. Change the two variables in `.env` if a scheduled run should do more or less work.

Windows Task Scheduler can run `scripts\poll.bat`. The API container must already be up.

## Tests

```bash
uv run pytest tests --ignore=tests/e2e   # unit tests (pre-commit)
uv run playwright install chromium       # once per machine
uv run pytest tests/e2e                  # browser e2e (pre-push)
bash scripts/install-hooks.sh            # enable the git hooks
```

`pre-commit` runs the unit suite. `pre-push` runs the Playwright e2e suite. Bypass with `--no-verify` if you must.

## Layout

- `apps/poller` — headless RSS → transcript → Claude → SQLite/Chroma → digest
- `apps/api` — browse / keyword / semantic / ask / trends
- `packages/core` — shared library
- `config/prompts` — editable Claude templates
- `data/` — SQLite, Chroma, logs, digests, Whisper/HF caches
