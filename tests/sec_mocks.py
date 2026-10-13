"""httpx MockTransport serving SEC fixtures so CI never hits the network."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import httpx

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "sec"


def sec_transport(
    overrides: dict[str, Callable[[httpx.Request], httpx.Response]] | None = None,
    seen: list[httpx.Request] | None = None,
) -> httpx.MockTransport:
    overrides = overrides or {}

    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        url = str(request.url)
        if url in overrides:
            return overrides[url](request)
        name = url.rsplit("/", 1)[-1]
        if url.startswith("https://data.sec.gov/submissions/"):
            candidates = [FIXTURES / f"submissions_{name}", FIXTURES / name]
        elif url.startswith("https://data.sec.gov/api/xbrl/companyfacts/"):
            candidates = [FIXTURES / f"companyfacts_{name}"]
        else:
            candidates = [FIXTURES / name]
        for path in candidates:
            if path.exists():
                ctype = "application/json" if path.suffix == ".json" else "text/html"
                return httpx.Response(
                    200, content=path.read_bytes(), headers={"Content-Type": ctype}
                )
        return httpx.Response(404, text="not found")

    return httpx.MockTransport(handler)
