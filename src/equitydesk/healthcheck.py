"""Container health probe without curl: `python -m equitydesk.healthcheck [URL]` exits 0 when the
endpoint answers 2xx within the timeout, 1 otherwise."""

from __future__ import annotations

import sys
import urllib.request

DEFAULT_URL = "http://127.0.0.1:8000/ready"


def probe(url: str = DEFAULT_URL, timeout: float = 3.0) -> bool:
    if not url.startswith(("http://127.0.0.1", "http://localhost")):
        raise ValueError("health probes only target the local container")
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310 - local URL only
            status: int = resp.status
            return 200 <= status < 300
    except OSError:
        return False


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    return 0 if probe(args[0] if args else DEFAULT_URL) else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
