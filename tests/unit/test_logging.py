from __future__ import annotations

import io
import json
import logging
from collections.abc import Iterator

import pytest

from equitydesk.logging_setup import (
    REDACTED,
    configure_logging,
    log_context,
    scrub_text,
    secret_registry,
)


@pytest.fixture
def capture() -> Iterator[io.StringIO]:
    stream = io.StringIO()
    handler = configure_logging(
        level="DEBUG", fmt="json", secrets=["my-registered-secret"], stream=stream
    )
    yield stream
    logger = logging.getLogger("equitydesk")
    for h in [handler, *logger.handlers]:
        if getattr(h, "_equitydesk", False):
            logger.removeHandler(h)
    logger.propagate = True
    secret_registry.clear()


def _records(stream: io.StringIO) -> list[dict[str, object]]:
    return [json.loads(line) for line in stream.getvalue().splitlines() if line]


@pytest.mark.parametrize(
    "raw",
    [
        "Authorization: Bearer abcdef123456789",
        "headers={'x-api-key': 'abcdef123456789'}",
        "url=https://h/x?api_key=abcdef123456789&q=1",
        "key sk-ant-api03-abcdefghijklmnop",
        '{"password": "hunter2hunter2"}',
    ],
)
def test_scrub_generic_credential_shapes(raw: str) -> None:
    out = scrub_text(raw)
    assert REDACTED in out
    assert "abcdef123456789" not in out
    assert "hunter2hunter2" not in out
    assert "abcdefghijklmnop" not in out


def test_registered_secret_never_logged(capture: io.StringIO) -> None:
    log = logging.getLogger("equitydesk.test")
    log.info("calling provider with my-registered-secret", extra={"detail": "my-registered-secret"})
    log.info(
        "headers", extra={"headers": {"Authorization": "Bearer zzzzzzzzzzzz", "Accept": "json"}}
    )
    text = capture.getvalue()
    assert "my-registered-secret" not in text
    assert "zzzzzzzzzzzz" not in text
    recs = _records(capture)
    assert recs[1]["headers"] == {"Authorization": REDACTED, "Accept": "json"}


def test_exception_text_is_scrubbed(capture: io.StringIO) -> None:
    log = logging.getLogger("equitydesk.test")
    try:
        raise RuntimeError("upstream rejected api_key=my-registered-secret")
    except RuntimeError:
        log.exception("failure")
    assert "my-registered-secret" not in capture.getvalue()


def test_correlation_and_run_context(capture: io.StringIO) -> None:
    log = logging.getLogger("equitydesk.test")
    with log_context(research_run_id="run_1") as cid:
        log.info("inside")
        with log_context():
            log.info("nested")
    log.info("outside")
    inside, nested, outside = _records(capture)
    assert inside["correlation_id"] == cid and inside["research_run_id"] == "run_1"
    assert nested["correlation_id"] == cid and nested["research_run_id"] == "run_1"
    assert outside["correlation_id"] is None and outside["research_run_id"] is None
    assert inside["level"] == "INFO" and "ts" in inside


def test_configure_is_idempotent(capture: io.StringIO) -> None:
    configure_logging(level="INFO", stream=capture)
    logger = logging.getLogger("equitydesk")
    assert sum(1 for h in logger.handlers if getattr(h, "_equitydesk", False)) == 1
