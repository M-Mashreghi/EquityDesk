"""Structured, secret-safe logging with correlation / research-run context."""

from __future__ import annotations

import json
import logging
import re
import sys
import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

REDACTED = "***REDACTED***"

correlation_id_var: ContextVar[str | None] = ContextVar("correlation_id", default=None)
research_run_id_var: ContextVar[str | None] = ContextVar("research_run_id", default=None)

# Generic credential shapes scrubbed even when the exact value was never registered.
_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(r"(?i)(authorization\s*[:=]\s*)(bearer|basic|token)?\s*[^\s,;'\"]+"),
        r"\1" + REDACTED,
    ),
    (
        re.compile(
            r"(?i)((?:x-api-key|api[_-]?key|access[_-]?token|secret|password|token)"
            r"[\"']?\s*[:=]\s*[\"']?)[^\s,;'\"&}]+"
        ),
        r"\1" + REDACTED,
    ),
    (re.compile(r"\bsk-[A-Za-z0-9_\-]{8,}"), REDACTED),
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-~+/]{8,}=*"), "Bearer " + REDACTED),
]

_STANDARD_ATTRS = set(vars(logging.makeLogRecord({}))) | {"message", "asctime"}


class SecretRegistry:
    """Thread-safe set of exact secret values that must never be logged."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._values: set[str] = set()

    def register(self, *values: str | None) -> None:
        with self._lock:
            # Very short values would cause absurd over-redaction; they are not real secrets.
            self._values.update(v for v in values if v and len(v) >= 4)

    def clear(self) -> None:
        with self._lock:
            self._values.clear()

    def values(self) -> list[str]:
        with self._lock:
            return sorted(self._values, key=len, reverse=True)


secret_registry = SecretRegistry()


def scrub_text(text: str, registry: SecretRegistry = secret_registry) -> str:
    for value in registry.values():
        text = text.replace(value, REDACTED)
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def scrub_value(value: Any, registry: SecretRegistry = secret_registry) -> Any:
    if isinstance(value, str):
        return scrub_text(value, registry)
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            if isinstance(k, str) and re.search(
                r"(?i)(authorization|api[_-]?key|secret|password|token)", k
            ):
                out[k] = REDACTED
            else:
                out[k] = scrub_value(v, registry)
        return out
    if isinstance(value, (list, tuple)):
        return type(value)(scrub_value(v, registry) for v in value)
    return value


def _extra_fields(record: logging.LogRecord) -> dict[str, Any]:
    return {
        k: v
        for k, v in record.__dict__.items()
        if k not in _STANDARD_ATTRS and not k.startswith("_")
    }


class ContextFilter(logging.Filter):
    """Attach correlation and research-run ids from context variables."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.correlation_id = correlation_id_var.get()
        record.research_run_id = research_run_id_var.get()
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        payload.update(_extra_fields(record))
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return scrub_text(json.dumps(scrub_value(payload), default=str, ensure_ascii=False))


class TextFormatter(logging.Formatter):
    def __init__(self) -> None:
        super().__init__("%(asctime)s %(levelname)s %(name)s [%(correlation_id)s] %(message)s")

    def format(self, record: logging.LogRecord) -> str:
        return scrub_text(super().format(record))


def configure_logging(
    level: str = "INFO",
    fmt: str = "json",
    secrets: list[str] | None = None,
    stream: Any = None,
) -> logging.Handler:
    """Install one scrubbed handler on the `equitydesk` logger; returns that handler."""
    secret_registry.register(*(secrets or []))
    handler = logging.StreamHandler(stream or sys.stderr)
    handler.addFilter(ContextFilter())
    handler.setFormatter(JsonFormatter() if fmt == "json" else TextFormatter())
    logger = logging.getLogger("equitydesk")
    for existing in list(logger.handlers):
        if getattr(existing, "_equitydesk", False):
            logger.removeHandler(existing)
    handler._equitydesk = True  # type: ignore[attr-defined]
    logger.addHandler(handler)
    logger.setLevel(level)
    logger.propagate = False
    return handler


def new_correlation_id() -> str:
    return uuid.uuid4().hex


@contextmanager
def log_context(
    correlation_id: str | None = None, research_run_id: str | None = None
) -> Iterator[str]:
    """Bind correlation / research-run ids to every log record emitted inside the block."""
    cid = correlation_id or correlation_id_var.get() or new_correlation_id()
    t1 = correlation_id_var.set(cid)
    t2 = research_run_id_var.set(research_run_id or research_run_id_var.get())
    try:
        yield cid
    finally:
        correlation_id_var.reset(t1)
        research_run_id_var.reset(t2)
