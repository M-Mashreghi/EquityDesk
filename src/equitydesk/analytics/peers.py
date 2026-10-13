"""Peer comparable tables and summary statistics (deterministic).

Statistics are computed over peers only (never the target), exclude missing values explicitly,
and use linear-interpolation percentiles (same convention as numpy's default).
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date

from equitydesk.domain.metrics import MetricResult, MetricStatus
from equitydesk.domain.peers import PeerSet

DEFAULT_OPERATING_METRICS = (
    "revenue_growth",
    "gross_margin",
    "operating_margin",
    "net_margin",
    "fcf_margin",
    "roic",
    "net_debt_to_ebitda",
)


def percentile(sorted_values: list[float], q: float) -> float:
    """Linear interpolation between closest ranks; q in [0, 1]."""
    if not sorted_values:
        raise ValueError("percentile of empty data")
    if not 0.0 <= q <= 1.0:
        raise ValueError("q must be within [0, 1]")
    pos = (len(sorted_values) - 1) * q
    lo, hi = math.floor(pos), math.ceil(pos)
    return sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * (pos - lo)


@dataclass(frozen=True)
class PeerStats:
    metric: str
    n: int
    median: float | None
    mean: float | None
    minimum: float | None
    maximum: float | None
    p25: float | None
    p75: float | None
    included: tuple[str, ...]
    excluded_missing: tuple[str, ...]


def summary_stats(metric: str, values: Mapping[str, float | None]) -> PeerStats:
    present = {k: v for k, v in values.items() if v is not None and math.isfinite(v)}
    missing = tuple(sorted(k for k in values if k not in present))
    data = sorted(present.values())
    if not data:
        return PeerStats(metric, 0, None, None, None, None, None, None, (), missing)
    return PeerStats(
        metric=metric,
        n=len(data),
        median=statistics.median(data),
        mean=statistics.fmean(data),
        minimum=data[0],
        maximum=data[-1],
        p25=percentile(data, 0.25),
        p75=percentile(data, 0.75),
        included=tuple(sorted(present)),
        excluded_missing=missing,
    )


@dataclass(frozen=True)
class ComparableCell:
    value: float | None
    period_end: date | None
    status: MetricStatus | None
    metric_id: str | None


@dataclass
class ComparableTable:
    peer_set_id: str
    target_company_id: str
    metrics: tuple[str, ...]
    rows: dict[str, dict[str, ComparableCell]]  # company_id -> metric -> cell
    stats: dict[str, PeerStats]
    notes: list[str] = field(default_factory=list)

    def target_vs_median(self, metric: str) -> float | None:
        cell = self.rows[self.target_company_id][metric]
        med = self.stats[metric].median
        return None if cell.value is None or med is None else cell.value - med


def latest_metric(results: Iterable[MetricResult], name: str) -> ComparableCell:
    """Most recent fiscal-year result for a metric (any status), or an empty cell."""
    candidates = [r for r in results if r.name == name]
    if not candidates:
        return ComparableCell(None, None, None, None)
    r = max(candidates, key=lambda r: r.period_end)
    return ComparableCell(
        r.value if r.status is MetricStatus.OK else None, r.period_end, r.status, r.metric_id
    )


def comparable_table(
    peer_set: PeerSet,
    results_by_company: Mapping[str, Iterable[MetricResult]],
    metrics: tuple[str, ...] = DEFAULT_OPERATING_METRICS,
    max_period_gap_days: int = 120,
) -> ComparableTable:
    """Latest fiscal-year metrics for target + explicit peers; stats over peers only."""
    companies = (peer_set.target_company_id, *peer_set.member_ids)
    rows: dict[str, dict[str, ComparableCell]] = {}
    notes: list[str] = []
    for company in companies:
        results = list(results_by_company.get(company, []))
        if not results:
            notes.append(f"{company}: no deterministic metrics available")
        rows[company] = {m: latest_metric(results, m) for m in metrics}

    stats: dict[str, PeerStats] = {}
    for m in metrics:
        target_end = rows[peer_set.target_company_id][m].period_end
        values: dict[str, float | None] = {}
        for peer in peer_set.member_ids:
            cell = rows[peer][m]
            values[peer] = cell.value
            if (
                target_end
                and cell.period_end
                and abs((cell.period_end - target_end).days) > max_period_gap_days
            ):
                notes.append(
                    f"{peer}/{m}: fiscal period {cell.period_end} is more than "
                    f"{max_period_gap_days} days from target period {target_end}"
                )
        stats[m] = summary_stats(m, values)
    return ComparableTable(
        peer_set.peer_set_id, peer_set.target_company_id, metrics, rows, stats, notes
    )
