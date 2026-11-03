"""Watchlist scheduler process: runs `WatchlistService.tick()` every N seconds.

Usage: `python -m equitydesk.infrastructure.jobs.scheduler [--once]`. Several processes may run
concurrently; per-company refresh slots are claimed atomically in SQLite, so no refresh repeats.
"""

from __future__ import annotations

import argparse
import logging
import threading
from collections.abc import Callable

from equitydesk.application.watchlist import TickResult, WatchlistService

log = logging.getLogger("equitydesk.scheduler")


def run_scheduler(
    service: WatchlistService,
    tick_seconds: float,
    stop: threading.Event,
    on_tick: Callable[[TickResult], None] | None = None,
    max_ticks: int | None = None,
) -> int:
    """Tick until `stop` is set (or `max_ticks` reached); returns the number of ticks run."""
    ticks = 0
    while not stop.is_set():
        try:
            result = service.tick()
        except Exception:  # keep the scheduler alive; the next tick retries
            log.exception("watchlist tick failed")
        else:
            for r in result.refreshes:
                if r.action != "skipped_not_due":
                    log.info(
                        "refresh %s %s %s", r.company_id, r.action, r.research_run_id or r.detail
                    )
            if on_tick is not None:
                on_tick(result)
        ticks += 1
        if max_ticks is not None and ticks >= max_ticks:
            break
        stop.wait(tick_seconds)
    return ticks


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - process entry point
    from equitydesk.bootstrap import build_container
    from equitydesk.logging_setup import configure_logging

    parser = argparse.ArgumentParser(description="EquityDesk watchlist scheduler")
    parser.add_argument("--once", action="store_true", help="run a single tick and exit")
    args = parser.parse_args(argv)
    container = build_container()
    s = container.settings
    configure_logging(s.log_level, s.log_format, s.secret_values())
    stop = threading.Event()
    run_scheduler(container.watchlist, container.settings.scheduler_tick_seconds, stop,
                  max_ticks=1 if args.once else None)  # fmt: skip
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
