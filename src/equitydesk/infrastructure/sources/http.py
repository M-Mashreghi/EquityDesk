"""Polite HTTP fetching for official sources: identifying User-Agent, rate limit, retries."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from email.utils import parsedate_to_datetime

import httpx

log = logging.getLogger(__name__)

RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})


class SourceUnavailableError(RuntimeError):
    """Upstream source could not be fetched after bounded retries (or returned a hard error)."""

    def __init__(self, url: str, status: int | None, detail: str) -> None:
        self.url = url
        self.status = status
        super().__init__(f"{url}: {detail}" + (f" (HTTP {status})" if status else ""))


@dataclass(frozen=True)
class FetchResult:
    url: str
    status: int
    content: bytes
    content_type: str | None


class RateLimiter:
    """Minimum interval between requests (process-local)."""

    def __init__(
        self,
        max_per_second: float,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.min_interval = 1.0 / max_per_second
        self._clock = clock
        self._sleep = sleep
        self._next = 0.0
        self._lock = threading.Lock()

    def wait(self) -> None:
        with self._lock:
            now = self._clock()
            if now < self._next:
                self._sleep(self._next - now)
                now = self._next
            self._next = now + self.min_interval


class HttpFetcher:
    def __init__(
        self,
        user_agent: str,
        max_requests_per_second: float = 5.0,
        max_retries: int = 3,
        backoff_seconds: float = 1.0,
        timeout_seconds: float = 30.0,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not user_agent.strip():
            raise ValueError("a descriptive User-Agent is required for official sources")
        self.max_retries = max_retries
        self.backoff_seconds = backoff_seconds
        self._sleep = sleep
        self.rate_limiter = RateLimiter(max_requests_per_second, clock=clock, sleep=sleep)
        self.client = httpx.Client(
            headers={"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate"},
            timeout=timeout_seconds,
            transport=transport,
            follow_redirects=True,
        )
        self.request_count = 0

    def _retry_delay(self, attempt: int, response: httpx.Response | None) -> float:
        if response is not None and (retry_after := response.headers.get("Retry-After")):
            try:
                return min(float(retry_after), 60.0)
            except ValueError:
                try:
                    when = parsedate_to_datetime(retry_after)
                    return max(0.0, min(when.timestamp() - time.time(), 60.0))
                except (TypeError, ValueError):
                    pass
        return float(self.backoff_seconds * (2**attempt))

    def get(self, url: str) -> FetchResult:
        last_status: int | None = None
        last_detail = "unknown error"
        for attempt in range(self.max_retries + 1):
            self.rate_limiter.wait()
            self.request_count += 1
            response: httpx.Response | None = None
            try:
                response = self.client.get(url)
            except httpx.TransportError as exc:
                last_status, last_detail = None, f"transport error: {type(exc).__name__}"
            else:
                if response.status_code == 200:
                    return FetchResult(
                        url=str(response.url),
                        status=200,
                        content=response.content,
                        content_type=response.headers.get("Content-Type"),
                    )
                last_status, last_detail = response.status_code, "unexpected status"
                if response.status_code not in RETRYABLE_STATUS:
                    break
            if attempt < self.max_retries:
                delay = self._retry_delay(attempt, response)
                log.warning(
                    "retrying source fetch",
                    extra={"url": url, "status": last_status, "attempt": attempt + 1},
                )
                self._sleep(delay)
        raise SourceUnavailableError(url, last_status, last_detail)

    def close(self) -> None:
        self.client.close()
