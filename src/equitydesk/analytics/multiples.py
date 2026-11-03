"""Peer comparison table and multiples-based valuation ranges.

All inputs are explicit numbers with their provenance; nothing missing is imputed. Peer prices
must be observed at or before `as_of` (point-in-time), and stale or misaligned observations are
surfaced as notes.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from equitydesk.analytics.kpis import Undefined
from equitydesk.analytics.peers import PeerStats, summary_stats
from equitydesk.analytics.valuation import (
    VALUATION_VERSION,
    enterprise_value,
    equity_from_enterprise_value,
    market_cap,
    per_share,
)
from equitydesk.domain.market import MarketObservation, observation_as_of
from equitydesk.domain.valuation import ValuationMethod, ValuationRun

MULTIPLES = ("ev_sales", "ev_ebitda", "pe", "fcf_yield")
# multiple -> (fundamental input, applies to EV (True) or equity (False))
APPLICATION = {
    "ev_sales": ("revenue", True),
    "ev_ebitda": ("ebitda", True),
    "pe": ("net_income", False),
}
FORMULAS = {
    "ev_sales": "enterprise_value / revenue",
    "ev_ebitda": "enterprise_value / ebitda",
    "pe": "market_cap / net_income",
    "fcf_yield": "free_cash_flow / market_cap",
}
MAX_OBSERVATION_AGE_DAYS = 7
MIN_PEERS = 2


@dataclass(frozen=True)
class CompanyFundamentals:
    """Explicit trailing fundamentals with provenance (metric ids / fact ids)."""

    company_id: str
    security_id: str
    fiscal_period_end: date
    revenue: float | None
    ebitda: float | None
    net_income: float | None
    free_cash_flow: float | None
    total_debt: float | None
    cash_and_investments: float | None
    diluted_shares: float | None = None
    sources: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class PeerRow:
    company_id: str
    observation: MarketObservation
    fundamentals: CompanyFundamentals
    market_cap: float | None
    enterprise_value: float | None
    multiples: dict[str, float | None]
    notes: tuple[str, ...]


def _ratio(num: float | None, den: float | None) -> float | None:
    if num is None or den is None or den <= 0:
        return None
    return num / den


def build_row(
    fund: CompanyFundamentals, observations: Sequence[MarketObservation], as_of: datetime
) -> PeerRow:
    obs = observation_as_of(observations, fund.security_id, as_of)
    notes: list[str] = []
    age = (as_of - obs.observed_at).days
    if age > MAX_OBSERVATION_AGE_DAYS:
        notes.append(f"price observation is {age} days older than as_of")
    mcap: float | None = None
    ev: float | None = None
    if obs.shares_outstanding is None:
        notes.append("no sourced share count: market cap not computed")
    else:
        mcap = market_cap(obs.price, obs.shares_outstanding).value
        if fund.total_debt is None or fund.cash_and_investments is None:
            notes.append("debt or cash missing: EV not computed")
        else:
            ev = enterprise_value(mcap, fund.total_debt, fund.cash_and_investments).value
    multiples = {
        "ev_sales": _ratio(ev, fund.revenue),
        "ev_ebitda": _ratio(ev, fund.ebitda),
        "pe": _ratio(mcap, fund.net_income),
        "fcf_yield": _ratio(fund.free_cash_flow, mcap) if fund.free_cash_flow is not None else None,
    }
    for name in ("ev_sales", "ev_ebitda", "pe"):
        denominator = getattr(fund, APPLICATION[name][0])
        if multiples[name] is None and denominator is not None and denominator <= 0:
            notes.append(f"{name}: non-positive {APPLICATION[name][0]}, multiple not meaningful")
    return PeerRow(fund.company_id, obs, fund, mcap, ev, multiples, tuple(notes))


@dataclass(frozen=True)
class ImpliedRange:
    multiple: str
    peer_stats: PeerStats
    target_metric: float
    low: float  # per share, from peer p25
    mid: float  # per share, from peer median
    high: float  # per share, from peer p75
    formula: str


@dataclass
class MultiplesValuation:
    target_company_id: str
    peer_set_id: str
    as_of: datetime
    target: PeerRow
    peers: list[PeerRow]
    stats: dict[str, PeerStats]
    implied: list[ImpliedRange]
    notes: list[str]

    def to_valuation_run(self, research_run_id: str, created_at: datetime) -> ValuationRun:
        inputs: dict[str, Any] = {
            "peer_set_id": self.peer_set_id,
            "target": _row_dict(self.target),
            "peers": [_row_dict(p) for p in self.peers],
        }
        outputs = {
            r.multiple: {"low": r.low, "mid": r.mid, "high": r.high, "target_metric": r.target_metric,
                         "peer_n": r.peer_stats.n, "peers_used": list(r.peer_stats.included)}
            for r in self.implied
        }  # fmt: skip
        digest = hashlib.sha256(
            json.dumps([research_run_id, inputs, outputs], sort_keys=True, default=str).encode()
        ).hexdigest()
        return ValuationRun(
            valuation_id=f"val_mult_{digest[:20]}",
            research_run_id=research_run_id,
            method=ValuationMethod.MULTIPLES,
            valuation_date=self.as_of,
            formula="per_share = bridge(peer_multiple[p25|median|p75] * target_metric) / diluted_shares",
            inputs=inputs,
            assumptions={
                "percentiles": [0.25, 0.5, 0.75],
                "max_observation_age_days": MAX_OBSERVATION_AGE_DAYS,
            },
            outputs=outputs,
            version=VALUATION_VERSION,
            created_at=created_at,
        )


def _row_dict(row: PeerRow) -> dict[str, Any]:
    return {
        "company_id": row.company_id,
        "price": row.observation.price,
        "observed_at": row.observation.observed_at.isoformat(),
        "price_source": row.observation.price_source,
        "shares_outstanding": row.observation.shares_outstanding,
        "fiscal_period_end": row.fundamentals.fiscal_period_end.isoformat(),
        "market_cap": row.market_cap,
        "enterprise_value": row.enterprise_value,
        "multiples": row.multiples,
        "sources": row.fundamentals.sources,
        "notes": list(row.notes),
    }


def multiples_valuation(
    target: CompanyFundamentals,
    peers: Sequence[CompanyFundamentals],
    observations: Sequence[MarketObservation],
    as_of: datetime,
    peer_set_id: str,
    multiples: Sequence[str] = ("ev_sales", "ev_ebitda", "pe"),
) -> MultiplesValuation:
    target_row = build_row(target, observations, as_of)
    peer_rows = [build_row(p, observations, as_of) for p in peers]
    notes = [f"{r.company_id}: {n}" for r in [target_row, *peer_rows] for n in r.notes]
    for r in peer_rows:
        gap = abs((r.observation.observed_at - target_row.observation.observed_at).days)
        if gap > MAX_OBSERVATION_AGE_DAYS:
            notes.append(f"{r.company_id}: price observed {gap} days apart from target")

    stats = {
        m: summary_stats(m, {r.company_id: r.multiples[m] for r in peer_rows}) for m in MULTIPLES
    }
    implied: list[ImpliedRange] = []
    for m in multiples:
        if m not in APPLICATION:
            raise ValueError(f"multiple {m} cannot be applied to value the target")
        metric_name, on_ev = APPLICATION[m]
        st = stats[m]
        metric = getattr(target, metric_name)
        # Every blocking reason is reported, so missing inputs are visible rather than imputed.
        reasons: list[str] = []
        if st.n < MIN_PEERS or st.p25 is None or st.median is None or st.p75 is None:
            reasons.append(f"{m}: only {st.n} peer value(s); no implied range")
        if metric is None or metric <= 0:
            reasons.append(f"{m}: target {metric_name} missing or non-positive; no implied range")
        if target.diluted_shares is None or (
            on_ev and (target.total_debt is None or target.cash_and_investments is None)
        ):
            reasons.append(f"{m}: target share count or net-debt inputs missing; no implied range")
        if reasons:
            notes.extend(reasons)
            continue
        assert st.p25 is not None and st.median is not None and st.p75 is not None
        assert metric is not None
        try:
            values = [
                _implied_per_share(q * metric, on_ev, target) for q in (st.p25, st.median, st.p75)
            ]
        except Undefined as exc:
            notes.append(f"{m}: {exc}")
            continue
        implied.append(
            ImpliedRange(
                m, st, metric, values[0], values[1], values[2],
                ("(peer EV multiple * target metric - debt + cash) / diluted shares" if on_ev
                 else "peer P/E * target net income / diluted shares"),
            )
        )  # fmt: skip
    return MultiplesValuation(
        target.company_id, peer_set_id, as_of, target_row, peer_rows, stats, implied, notes
    )


def _implied_per_share(value: float, on_ev: bool, target: CompanyFundamentals) -> float:
    assert target.diluted_shares is not None
    if on_ev:
        assert target.total_debt is not None and target.cash_and_investments is not None
        equity = equity_from_enterprise_value(
            value, target.total_debt, target.cash_and_investments
        ).value
    else:
        equity = value
    return per_share(equity, target.diluted_shares).value
