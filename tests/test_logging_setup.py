from __future__ import annotations

import logging

from wow_core.logging_setup import QuietFetchStatusAccessFilter


def test_fetch_status_200_access_line_downgraded_to_debug() -> None:
    filt = QuietFetchStatusAccessFilter()
    record = logging.LogRecord(
        name="uvicorn.access",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg='127.0.0.1:8000 - "GET /api/fetch-status HTTP/1.1" 200',
        args=(),
        exc_info=None,
    )
    assert filt.filter(record) is True
    assert record.levelno == logging.DEBUG

    other = logging.LogRecord(
        name="uvicorn.access",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg='127.0.0.1:8000 - "GET /api/videos HTTP/1.1" 200',
        args=(),
        exc_info=None,
    )
    assert filt.filter(other) is True
    assert other.levelno == logging.INFO
