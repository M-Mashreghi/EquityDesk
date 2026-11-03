"""Per-run telemetry: stage timings, LLM token/cost metadata and source/parser counters.

Telemetry is operational metadata only; it never feeds research outputs. Costs are computed
only from explicitly configured per-million-token prices, otherwise reported as unknown.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from pydantic import BaseModel

from equitydesk.application.llm import StructuredResult


class TelemetryStore(Protocol):
    def save_telemetry(self, research_run_id: str, summary: dict[str, Any]) -> None: ...
    def get_telemetry(self, research_run_id: str) -> dict[str, Any] | None: ...


log = logging.getLogger("equitydesk.telemetry")


@dataclass(frozen=True)
class TokenPrices:
    """USD per million tokens; both must be configured for costs to be computed."""

    input_per_mtok: float | None = None
    output_per_mtok: float | None = None

    def cost(self, input_tokens: int, output_tokens: int) -> float | None:
        if self.input_per_mtok is None or self.output_per_mtok is None:
            return None
        return (input_tokens * self.input_per_mtok + output_tokens * self.output_per_mtok) / 1e6


@dataclass
class RunTelemetry:
    research_run_id: str
    prices: TokenPrices = field(default_factory=TokenPrices)
    timer: Callable[[], float] = time.perf_counter
    stages: list[dict[str, Any]] = field(default_factory=list)
    llm_calls: list[dict[str, Any]] = field(default_factory=list)
    counters: dict[str, int] = field(default_factory=dict)
    _open: tuple[str, float] | None = None

    def enter(self, stage: str) -> None:
        """Close the running stage (if any) and start timing `stage`."""
        self.close("ok")
        self._open = (stage, self.timer())
        log.info("stage started", extra={"stage": stage})

    def close(self, outcome: str) -> None:
        if self._open is None:
            return
        name, start = self._open
        seconds = round(self.timer() - start, 6)
        self.stages.append({"stage": name, "seconds": seconds, "outcome": outcome})
        log.info("stage finished", extra={"stage": name, "seconds": seconds, "outcome": outcome})
        self._open = None

    def count(self, name: str, n: int = 1) -> None:
        self.counters[name] = self.counters.get(name, 0) + n

    def record_llm(self, result: StructuredResult[BaseModel]) -> None:
        call = {
            "prompt": result.prompt_ref,
            "schema": result.schema_version,
            "model": result.model,
            "attempts": result.attempts,
            "ok": result.ok,
            "failure": result.failure.code.value if result.failure else None,
            "input_tokens": result.input_tokens,
            "output_tokens": result.output_tokens,
            "cost_usd": self.prices.cost(result.input_tokens, result.output_tokens),
        }
        self.llm_calls.append(call)
        log.info("llm call", extra={k: v for k, v in call.items() if k != "schema"})

    def summary(self) -> dict[str, Any]:
        costs = [c["cost_usd"] for c in self.llm_calls]
        return {
            "research_run_id": self.research_run_id,
            "stages": self.stages,
            "total_seconds": round(sum(s["seconds"] for s in self.stages), 6),
            "llm": {
                "calls": len(self.llm_calls),
                "input_tokens": sum(c["input_tokens"] for c in self.llm_calls),
                "output_tokens": sum(c["output_tokens"] for c in self.llm_calls),
                "cost_usd": 0.0
                if not costs
                else (round(sum(costs), 6) if None not in costs else None),
                "by_call": self.llm_calls,
            },
            "counters": dict(sorted(self.counters.items())),
        }
