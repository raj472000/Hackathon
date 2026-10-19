"""Structured JSON logging with secret redaction."""

from __future__ import annotations

import logging
import re
import sys
from typing import Any

import structlog

_SECRET_KEYS = re.compile(r"authorization|token|secret|password|api[_-]?key|cookie", re.I)
_SECRET_VALUES = re.compile(r"(Bearer\s+)[A-Za-z0-9._\-]{8,}|(sk-)[A-Za-z0-9_\-]{8,}|(token\s+[a-z0-9]+:)[A-Za-z0-9]{8,}", re.I)


def redact(value: Any) -> Any:
    if isinstance(value, str):
        return _SECRET_VALUES.sub(lambda m: next(g for g in m.groups() if g) + "[REDACTED]", value)
    if isinstance(value, dict):
        return {k: "[REDACTED]" if _SECRET_KEYS.search(str(k)) else redact(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [redact(v) for v in value]
    return value


def _redact_processor(_logger: Any, _name: str, event: dict[str, Any]) -> dict[str, Any]:
    return {k: ("[REDACTED]" if _SECRET_KEYS.search(k) else redact(v)) for k, v in event.items()}


def configure(level: str = "INFO") -> None:
    logging.basicConfig(stream=sys.stdout, level=getattr(logging, level.upper(), logging.INFO), format="%(message)s")
    for noisy in ("httpx", "httpcore", "apscheduler", "aiosqlite"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    structlog.configure(
        processors=[structlog.contextvars.merge_contextvars, structlog.processors.add_log_level,
                    structlog.processors.TimeStamper(fmt="iso", utc=True), _redact_processor,  # type: ignore[list-item]
                    structlog.processors.JSONRenderer()],
        wrapper_class=structlog.make_filtering_bound_logger(getattr(logging, level.upper(), logging.INFO)),
        cache_logger_on_first_use=True,
    )
