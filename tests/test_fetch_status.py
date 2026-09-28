from __future__ import annotations

import logging

import wow_core.fetch_status as fs


def test_snapshot_reports_silence_seconds(monkeypatch) -> None:
    clock = {"t": 100.0}
    monkeypatch.setattr(fs.time, "monotonic", lambda: clock["t"])

    prog = fs.FetchProgress()
    prog.begin()
    prog.update("scan", "Reading inbox")
    clock["t"] = 125.0
    snap = prog.snapshot()
    assert snap["running"] is True
    assert snap["silence_seconds"] >= 24.0


def test_stale_run_is_failed_by_watchdog(monkeypatch) -> None:
    monkeypatch.setattr("wow_core.fetch_status.settings.SYNC_STALE_SECONDS", 60)
    prog = fs.FetchProgress()
    prog.begin()
    prog.update("scan", "Reading inbox")
    with prog._lock:
        prog._last_activity = fs.time.monotonic() - 120

    assert prog._maybe_fail_stale(120.0) is True
    assert prog.is_running() is False
    snap = prog.snapshot()
    assert snap["error"] and "timed out" in snap["error"]


def test_update_logs_resume_after_idle(caplog) -> None:
    caplog.set_level(logging.INFO, logger="wow.fetch")
    prog = fs.FetchProgress()
    prog.begin()
    prog._last_activity = 1000.0
    prog._message = "Reading inbox"
    fs.time.monotonic = lambda: 1035.0  # type: ignore[method-assign]
    prog.update("process", "Video 1/3")
    assert any("Resuming after" in r.message for r in caplog.records)
