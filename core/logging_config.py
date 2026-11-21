"""Logging that writes to logs/app.log and strips secrets."""

from __future__ import annotations

import logging
import re
from logging.handlers import RotatingFileHandler
from pathlib import Path

from core.paths import LOG_PATH

_SECRET_PATTERNS = (
    re.compile(r"Bearer\s+[A-Za-z0-9._\-]+", re.IGNORECASE),
    re.compile(r"sk-[A-Za-z0-9]+"),
    re.compile(r"(?i)(api[_-]?key|token|cookie|authorization|password)\s*[:=]\s*\S+"),
    re.compile(r"1[3-9]\d{9}"),
)


def redact(text: str) -> str:
    cleaned = str(text)
    for pattern in _SECRET_PATTERNS:
        cleaned = pattern.sub("[REDACTED]", cleaned)
    return cleaned


class RedactFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            rendered = record.getMessage()
        except Exception:
            rendered = str(record.msg)
        record.msg = redact(rendered)
        record.args = ()
        return True


def setup_logging(path: Path | None = None, force: bool = False) -> logging.Logger:
    logger = logging.getLogger("jobhunter")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    if force:
        for handler in list(logger.handlers):
            handler.close()
            logger.removeHandler(handler)
    if logger.handlers:
        return logger

    log_path = path or LOG_PATH
    log_path.parent.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    file_handler = RotatingFileHandler(
        log_path,
        maxBytes=2_000_000,
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    file_handler.addFilter(RedactFilter())
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    stream_handler.addFilter(RedactFilter())
    logger.addHandler(file_handler)
    logger.addHandler(stream_handler)
    return logger


def get_logger(name: str) -> logging.Logger:
    setup_logging()
    return logging.getLogger(f"jobhunter.{name}")
