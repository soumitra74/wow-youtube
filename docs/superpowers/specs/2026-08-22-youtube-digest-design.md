# YouTube Feed Digest — Design

Date: 2026-08-22

## Goal

Personal archive that polls followed YouTube channels, summarizes new videos with Claude, stores metadata in SQLite, indexes summaries in ChromaDB, and serves a browse/search/ask/trends UI.

## Architecture

Python monorepo in one Docker image and one Compose service.

- `packages/core` — settings, SQLite, YouTube, Claude, Chroma, digest
- `apps/poller` — scheduled headless pipeline
- `apps/api` — FastAPI + single-page HTML

Poller never starts the web server. API never polls RSS. Both share `wow_core` and `./data`.

Dev: bind-mount `apps/`, `packages/`, `config/`, `data/`. Uvicorn `--reload`. Rebuild only when dependencies change.

Poll: `docker compose exec wow python -m wow_poller` (Task Scheduler). Optional internal cron is off by default.

Scheduled runs process at most `POLL_LIMIT` new videos (default 10) and skip RSS items older than `POLL_MAX_AGE_DAYS` (default 3). `--seed` disables both for a one-shot backlog fill.

## Transcripts

1. Manual English captions (`youtube-transcript-api`, then yt-dlp + cookies)
2. YouTube auto-captions (English)
3. Any other timedtext track, translated to English
4. If YouTube timedtext is IP-blocked or missing: yt-dlp subtitle download
5. If none: local faster-whisper in ~10 minute shards (`GENERATE_MISSING_TRANSCRIPTS=true`, skip if longer than `WHISPER_MAX_MINUTES`)

Caption fetches wait `CAPTION_REQUEST_DELAY_SECONDS` between requests. Full transcripts are not stored. Only `transcript_length` + summary.

## Embeddings

Local `all-MiniLM-L6-v2` via Chroma. No second vendor API. Embed the summary text, keyed by `video_id`.

## SQLite

- `channels` — channel_id, channel_name, rss_url, added_date
- `seen` — video_id PK, status `processed` | `skipped_no_transcript` | `error`
- `videos` — metadata, summary, key_takeaways JSON, topics JSON, relevance, watched

Indexes on `published_date`, `channel_id`, `relevance`, `seen.status`.

`channels.json` is the source of truth; each poll upserts channels (no cascade delete).

## Claude

- Prompts in `config/prompts/summarize.txt` and `ask.txt` (not hardcoded)
- Haiku for summarization, Sonnet for ask-across-videos
- Per-video try/except; failures log and mark `seen.error` without aborting the run



## Idempotency

`seen.video_id` is the dedup key. Re-runs skip processed and skipped-no-transcript rows. `--retry-errors` re-attempts `error` only.

## UI

Browse/filter, keyword LIKE, Chroma semantic search, ask (top-K summaries → Claude + citations), Chart.js topic trends, mark watched.