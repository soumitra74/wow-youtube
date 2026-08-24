from __future__ import annotations

import logging
from datetime import datetime, timezone

from wow_core import settings


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
