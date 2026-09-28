from __future__ import annotations

import os
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass


def _repo_root() -> Path:
    env_root = os.getenv("WOW_ROOT")
    if env_root:
        return Path(env_root).resolve()
    # packages/core/src/wow_core/settings.py -> repo root is 4 parents up
    return Path(__file__).resolve().parents[4]


ROOT = _repo_root()
CONFIG_DIR = Path(os.getenv("WOW_CONFIG_DIR", ROOT / "config")).resolve()
DATA_DIR = Path(os.getenv("WOW_DATA_DIR", ROOT / "data")).resolve()

CHANNELS_PATH = CONFIG_DIR / "channels.json"
PROMPTS_DIR = CONFIG_DIR / "prompts"
SUMMARIZE_PROMPT_PATH = PROMPTS_DIR / "summarize.txt"
ASK_PROMPT_PATH = PROMPTS_DIR / "ask.txt"

DB_PATH = DATA_DIR / "wow.db"
CHROMA_DIR = DATA_DIR / "chroma"
LOGS_DIR = DATA_DIR / "logs"
DIGESTS_DIR = DATA_DIR / "digests"
WHISPER_DIR = DATA_DIR / "whisper"
HF_HOME = Path(os.getenv("HF_HOME", DATA_DIR / "hf")).resolve()

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
CLAUDE_SUMMARIZE_MODEL = os.getenv("CLAUDE_SUMMARIZE_MODEL", "claude-haiku-4-5")
CLAUDE_ASK_MODEL = os.getenv("CLAUDE_ASK_MODEL", "claude-sonnet-4-5")

CHROMA_COLLECTION = os.getenv("CHROMA_COLLECTION", "youtube_summaries")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "all-MiniLM-L6-v2")

TRANSCRIPT_MAX_CHARS = int(os.getenv("TRANSCRIPT_MAX_CHARS", "80000"))
GENERATE_MISSING_TRANSCRIPTS = os.getenv(
    "GENERATE_MISSING_TRANSCRIPTS", "true"
).lower() in {"1", "true", "yes", "on"}
CAPTION_REQUEST_DELAY_SECONDS = float(os.getenv("CAPTION_REQUEST_DELAY_SECONDS", "3"))
# Retries when YouTube returns HTTP 429 during caption/metadata fetches
CAPTION_RATE_LIMIT_RETRIES = int(os.getenv("CAPTION_RATE_LIMIT_RETRIES", "3"))
CAPTION_RATE_LIMIT_BACKOFF_SECONDS = float(
    os.getenv("CAPTION_RATE_LIMIT_BACKOFF_SECONDS", "15")
)
WHISPER_MODEL = os.getenv("WHISPER_MODEL", "base")
WHISPER_MAX_MINUTES = int(os.getenv("WHISPER_MAX_MINUTES", "90"))
WHISPER_SHARD_MINUTES = int(os.getenv("WHISPER_SHARD_MINUTES", "10"))
WHISPER_SHARD_OVERLAP_SECONDS = float(os.getenv("WHISPER_SHARD_OVERLAP_SECONDS", "5"))
SEMANTIC_TOP_K = int(os.getenv("SEMANTIC_TOP_K", "8"))
# Max new videos to summarize per reload (0 = no cap for one run).
YOUTUBE_NOTIFICATIONS_LIMIT = int(os.getenv("YOUTUBE_NOTIFICATIONS_LIMIT", "10"))
# How many inbox rows yt-dlp reads (top N are often already archived).
YOUTUBE_NOTIFICATIONS_SCAN_LIMIT = int(
    os.getenv("YOUTUBE_NOTIFICATIONS_SCAN_LIMIT", "40")
)
# Abort in-memory sync if FetchProgress has no update for this many seconds (0 = disabled).
SYNC_STALE_SECONDS = int(os.getenv("SYNC_STALE_SECONDS", "900"))
YOUTUBE_FETCH_WATCH_LATER = os.getenv("YOUTUBE_FETCH_WATCH_LATER", "true").lower() in {
    "1",
    "true",
    "yes",
    "on",
}
YOUTUBE_WATCH_LATER_SCAN_LIMIT = int(os.getenv("YOUTUBE_WATCH_LATER_SCAN_LIMIT", "40"))
YOUTUBE_PLAYLIST_SCAN_LIMIT = int(os.getenv("YOUTUBE_PLAYLIST_SCAN_LIMIT", "40"))
# Additional playlist IDs (comma-separated), e.g. PLxxx. Watch Later (WL) is controlled separately.
YOUTUBE_EXTRA_PLAYLIST_IDS = [
    part.strip()
    for part in os.getenv("YOUTUBE_EXTRA_PLAYLIST_IDS", "").split(",")
    if part.strip()
]
# 0 = unlimited. Default keeps scheduled polls from eating the full RSS backlog.
POLL_LIMIT = int(os.getenv("POLL_LIMIT", "10"))
POLL_MAX_AGE_DAYS = int(os.getenv("POLL_MAX_AGE_DAYS", "3"))
_cookies_path = os.getenv("YOUTUBE_COOKIES_PATH", str(CONFIG_DIR / "youtube_cookies.txt"))
YOUTUBE_COOKIES_PATH = Path(_cookies_path)
if not YOUTUBE_COOKIES_PATH.is_absolute():
    YOUTUBE_COOKIES_PATH = (ROOT / YOUTUBE_COOKIES_PATH).resolve()


def ensure_data_dirs() -> None:
    for path in (DATA_DIR, CHROMA_DIR, LOGS_DIR, DIGESTS_DIR, WHISPER_DIR, HF_HOME):
        path.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("HF_HOME", str(HF_HOME))
    os.environ.setdefault("TRANSFORMERS_CACHE", str(HF_HOME))
