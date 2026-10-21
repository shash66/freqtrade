"""Structured logging with secret redaction. RPC URLs often embed API keys; they never reach the logs."""
from __future__ import annotations

import logging
import re
import sys

import structlog

_URL_KEY = re.compile(r"(https?://[^\s]*?/)([A-Za-z0-9_\-]{16,})(?=[/\s]|$)")
_KV_SECRET = re.compile(r"(?i)(password|secret|token|api[_-]?key|authorization)(=|: ?)([^\s,'\"]+)")


def redact(value: str) -> str:
    value = _URL_KEY.sub(r"\1<redacted>", value)
    return _KV_SECRET.sub(r"\1\2<redacted>", value)


def _redact_processor(_logger, _method, event_dict):
    for k, v in list(event_dict.items()):
        if isinstance(v, str):
            event_dict[k] = redact(v)
    return event_dict


def configure_logging(level: str = "INFO", json_output: bool = True) -> None:
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=level.upper())
    renderer = structlog.processors.JSONRenderer() if json_output else structlog.dev.ConsoleRenderer()
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            _redact_processor,
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.getLevelName(level.upper())),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str):
    return structlog.get_logger(name)
