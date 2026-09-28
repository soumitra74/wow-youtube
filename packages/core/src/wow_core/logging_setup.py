from __future__ import annotations

import logging
from datetime import datetime, timezone

from wow_core import settings

_SERVER_LOGGERS = ("uvicorn", "uvicorn.error", "uvicorn.access")
_ACCESS_FILTER_INSTALLED = False


class QuietFetchStatusAccessFilter(logging.Filter):
    """Downgrade successful fetch-status polling lines so they stay at DEBUG under INFO servers."""

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        if "/api/fetch-status" in msg and " 200" in msg:
            record.levelno = logging.DEBUG
            record.levelname = logging.DEBUG
        return True


def configure_poller_logging() -> logging.Logger:
    settings.ensure_data_dirs()
    logger = logging.getLogger("wow")
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")

    stream = logging.StreamHandler()
    stream.setFormatter(formatter)
    logger.addHandler(stream)

    day = datetime.now(timezone.utc).date().isoformat()
    file_handler = logging.FileHandler(
        settings.LOGS_DIR / f"poller-{day}.log", encoding="utf-8"
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    return logger


def configure_server_logging() -> None:
    """Uvicorn console logging at INFO; quiet high-frequency fetch-status access lines."""
    global _ACCESS_FILTER_INSTALLED

    for name in _SERVER_LOGGERS:
        logger = logging.getLogger(name)
        logger.setLevel(logging.INFO)
        for handler in logger.handlers:
            handler.setLevel(logging.INFO)
            formatter = handler.formatter
            if formatter is None:
                handler.setFormatter(
                    logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
                )
                continue
            fmt = formatter._style._fmt
            if "%(asctime)s" in fmt:
                continue
            stamped = "%(asctime)s " + fmt
            formatter._style._fmt = stamped
            formatter._fmt = stamped

    logging.getLogger("wow.fetch").setLevel(logging.INFO)

    access = logging.getLogger("uvicorn.access")
    if not _ACCESS_FILTER_INSTALLED:
        access.addFilter(QuietFetchStatusAccessFilter())
        _ACCESS_FILTER_INSTALLED = True
