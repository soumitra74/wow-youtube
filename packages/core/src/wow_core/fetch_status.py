from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("wow.fetch")

SILENCE_LOG_INTERVAL_SECONDS = 10.0


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@dataclass
class FetchSnapshot:
    running: bool = False
    phase: str = ""
    message: str = ""
    started_at: str | None = None
    updated_at: str | None = None
    silence_seconds: float = 0.0
    result: dict[str, Any] | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "running": self.running,
            "phase": self.phase,
            "message": self.message,
            "started_at": self.started_at,
            "updated_at": self.updated_at,
            "silence_seconds": round(self.silence_seconds, 1),
            "result": self.result,
            "error": self.error,
        }


class FetchProgress:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._running = False
        self._phase = ""
        self._message = ""
        self._started_at: str | None = None
        self._updated_at: str | None = None
        self._last_activity = 0.0
        self._result: dict[str, Any] | None = None
        self._error: str | None = None
        self._watchdog: threading.Thread | None = None
        self._last_silence_log_at = 0.0

    def is_running(self) -> bool:
        with self._lock:
            return self._running

    def begin(self) -> None:
        with self._lock:
            if self._running:
                raise RuntimeError("fetch already running")
            self._running = True
            self._result = None
            self._error = None
            self._started_at = _utc_now()
            self._last_activity = time.monotonic()
            self._last_silence_log_at = self._last_activity
        self.update("start", "Fetch run started")
        self._watchdog = threading.Thread(target=self._watchdog_loop, name="fetch-silence-watchdog", daemon=True)
        self._watchdog.start()

    def update(self, phase: str, message: str) -> None:
        now_mono = time.monotonic()
        with self._lock:
            if not self._running:
                return
            silence = now_mono - self._last_activity if self._last_activity else 0.0
            prev_message = self._message
            self._phase = phase
            self._message = message
            self._updated_at = _utc_now()
            if silence >= 1.0 and prev_message:
                logger.info(
                    "[fetch] Resuming after %.1fs idle (was: %s)",
                    silence,
                    prev_message,
                )
            self._last_activity = now_mono
            self._last_silence_log_at = now_mono
        logger.info("[fetch] %s — %s", phase, message)

    def complete(self, result: dict[str, Any]) -> None:
        with self._lock:
            self._running = False
            self._result = result
            self._updated_at = _utc_now()
        logger.info("[fetch] complete — %s", result)

    def fail(self, error: str) -> None:
        with self._lock:
            self._running = False
            self._error = error
            self._updated_at = _utc_now()
        logger.error("[fetch] failed — %s", error)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            silence = time.monotonic() - self._last_activity if self._running and self._last_activity else 0.0
            return FetchSnapshot(
                running=self._running,
                phase=self._phase,
                message=self._message,
                started_at=self._started_at,
                updated_at=self._updated_at,
                silence_seconds=max(0.0, silence),
                result=self._result,
                error=self._error,
            ).to_dict()

    def _watchdog_loop(self) -> None:
        while True:
            time.sleep(SILENCE_LOG_INTERVAL_SECONDS)
            with self._lock:
                if not self._running:
                    return
                silence = time.monotonic() - self._last_activity
                if silence < SILENCE_LOG_INTERVAL_SECONDS:
                    continue
                if time.monotonic() - self._last_silence_log_at < SILENCE_LOG_INTERVAL_SECONDS:
                    continue
                self._last_silence_log_at = time.monotonic()
                phase, message = self._phase, self._message
            logger.warning(
                "[fetch] No progress update for %.1fs — still in %s: %s",
                silence,
                phase or "?",
                message or "(no message)",
            )


_fetch_progress = FetchProgress()


def get_fetch_progress() -> FetchProgress:
    return _fetch_progress


def active_progress() -> FetchProgress | None:
    return _fetch_progress if _fetch_progress.is_running() else None
