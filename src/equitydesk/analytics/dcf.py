"""Deterministic DCF (free cash flow to the firm) with explicit, versioned assumptions.

Forecast per year t = 1..n:

    revenue_t  = revenue_{t-1} * (1 + growth_t)
    ebit_t     = revenue_t * ebit_margin_t
    nopat_t    = ebit_t * (1 - tax_rate)
    d&a_t      = revenue_t * da_pct_revenue
    capex_t    = revenue_t * capex_pct_revenue
    dnwc_t     = (revenue_t - revenue_{t-1}) * nwc_pct_incremental_revenue
    fcff_t     = nopat_t + d&a_t - capex_t - dnwc_t

Terminal value at year n (Gordon on fcff_n, or exit EV/EBITDA on ebitda_n) is always discounted
with the end-of-year-n factor. EV -> equity -> per share uses the sourced balance-sheet bridge.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from equitydesk.analytics.accounting import StatementPeriod
from equitydesk.analytics.kpis import Undefined
from equitydesk.analytics.valuation import (
    VALUATION_VERSION,
    Computation,
    SensitivityTable,
    discount_factor,
    equity_from_enterprise_value,
    exit_multiple_terminal_value,
    gordon_terminal_value,
    per_share,
    sensitivity_table,
)
from equitydesk.domain.valuation import (
    DcfAssumptions,
    TerminalMethod,
    ValuationMethod,
    ValuationRun,
)

DCF_VERSION = "1.0.0"
FCFF_FORMULA = "nopat + d&a - capex - delta_nwc; nopat = ebit * (1 - tax_rate)"


@dataclass(frozen=True)
class DcfBase:
    """Reported starting point (facts, not assumptions) with provenance per input."""

    company_id: str
    base_period_end: date
    base_revenue: float
    total_debt: float
    cash_and_investments: float
    diluted_shares: float
    sources: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.base_revenue <= 0:
            raise Undefined("base revenue must be positive")
        if self.diluted_shares <= 0:
            raise Undefined("diluted share count must be positive")


@dataclass(frozen=True)
class ForecastYear:
    year: int
    revenue: float
    ebit: float
    nopat: float
    depreciation_amortization: float
    capex: float
    delta_nwc: float
    fcff: float
    ebitda: float
    discount_factor: float
    present_value: float


@dataclass(frozen=True)
class DcfResult:
    base: DcfBase
    assumptions: DcfAssumptions
    years: tuple[ForecastYear, ...]
    pv_forecast: float
    terminal_value: Computation
    pv_terminal_value: float
    enterprise_value: float
    equity_value: Computation
    value_per_share: Computation
    terminal_share_of_ev: float
    sensitivity: SensitivityTable | None = None

    @property
    def assumptions_hash(self) -> str:
        return assumptions_hash(self.assumptions)

    def to_valuation_run(
        self, research_run_id: str, valuation_date: datetime, created_at: datetime
    ) -> ValuationRun:
        inputs: dict[str, Any] = {
            "company_id": self.base.company_id,
            "base_period_end": self.base.base_period_end.isoformat(),
            "base_revenue": self.base.base_revenue,
            "total_debt": self.base.total_debt,
            "cash_and_investments": self.base.cash_and_investments,
            "diluted_shares": self.base.diluted_shares,
            "sources": dict(self.base.sources),
        }
        outputs: dict[str, Any] = {
            "forecast": [vars(y) for y in self.years],
            "pv_forecast": self.pv_forecast,
            "terminal_value": self.terminal_value.as_dict(),
            "pv_terminal_value": self.pv_terminal_value,
            "enterprise_value": self.enterprise_value,
            "equity_value": self.equity_value.value,
            "value_per_share": self.value_per_share.value,
            "terminal_share_of_ev": self.terminal_share_of_ev,
        }
        if self.sensitivity is not None:
            s = self.sensitivity
            outputs["sensitivity"] = {
                "rows": {"name": s.row_name, "values": list(s.row_values)},
                "cols": {"name": s.col_name, "values": list(s.col_values)},
                "grid": [list(r) for r in s.grid],
            }
        assumptions = self.assumptions.model_dump(mode="json") | {
            "assumptions_hash": self.assumptions_hash
        }
        digest = hashlib.sha256(
            json.dumps([research_run_id, inputs, assumptions], sort_keys=True).encode()
        ).hexdigest()
        return ValuationRun(
            valuation_id=f"val_dcf_{digest[:20]}",
            research_run_id=research_run_id,
            method=ValuationMethod.DCF,
            valuation_date=valuation_date,
            formula=f"EV = sum(fcff_t * df_t) + TV * df_n; fcff = {FCFF_FORMULA}",
            inputs=inputs,
            assumptions=assumptions,
            outputs=outputs,
            version=f"{VALUATION_VERSION}/dcf-{DCF_VERSION}",
            created_at=created_at,
        )


def assumptions_hash(assumptions: DcfAssumptions) -> str:
    payload = json.dumps(assumptions.model_dump(mode="json"), sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()


def run_dcf(base: DcfBase, a: DcfAssumptions) -> DcfResult:
    years: list[ForecastYear] = []
    prev_revenue = base.base_revenue
    for t, (growth, margin) in enumerate(zip(a.revenue_growth, a.ebit_margin, strict=True), 1):
        revenue = prev_revenue * (1 + growth)
        ebit = revenue * margin
        nopat = ebit * (1 - a.tax_rate)
        da = revenue * a.da_pct_revenue
        capex = revenue * a.capex_pct_revenue
        dnwc = (revenue - prev_revenue) * a.nwc_pct_incremental_revenue
        fcff = nopat + da - capex - dnwc
        df = discount_factor(a.discount_rate, t, a.mid_year)
        years.append(
            ForecastYear(t, revenue, ebit, nopat, da, capex, dnwc, fcff, ebit + da, df, fcff * df)
        )
        prev_revenue = revenue

    final = years[-1]
    if a.terminal_method is TerminalMethod.GORDON:
        assert a.terminal_growth is not None
        tv = gordon_terminal_value(final.fcff, a.discount_rate, a.terminal_growth)
    else:
        assert a.exit_ev_ebitda is not None
        tv = exit_multiple_terminal_value(final.ebitda, a.exit_ev_ebitda)
    pv_tv = tv.value * discount_factor(a.discount_rate, a.horizon_years)
    pv_forecast = sum(y.present_value for y in years)
    ev = pv_forecast + pv_tv
    equity = equity_from_enterprise_value(ev, base.total_debt, base.cash_and_investments)
    value_ps = per_share(equity.value, base.diluted_shares)
    return DcfResult(
        base=base,
        assumptions=a,
        years=tuple(years),
        pv_forecast=pv_forecast,
        terminal_value=tv,
        pv_terminal_value=pv_tv,
        enterprise_value=ev,
        equity_value=equity,
        value_per_share=value_ps,
        terminal_share_of_ev=pv_tv / ev if ev > 0 else float("nan"),
    )


def dcf_with_sensitivity(
    base: DcfBase,
    a: DcfAssumptions,
    discount_rates: tuple[float, ...],
    terminal_values: tuple[float, ...],
) -> DcfResult:
    """Run the DCF and attach a per-share grid over discount rate x terminal growth/multiple.

    Cells where the economics are undefined (e.g. rate <= terminal growth) are None.
    """
    result = run_dcf(base, a)
    col_name = "terminal_growth" if a.terminal_method is TerminalMethod.GORDON else "exit_ev_ebitda"

    def cell(rate: float, terminal: float) -> float:
        try:
            varied = a.model_copy(update={"discount_rate": rate, col_name: terminal})
            DcfAssumptions.model_validate(varied.model_dump())
        except ValueError as exc:
            raise Undefined(str(exc)) from exc
        return run_dcf(base, varied).value_per_share.value

    grid = sensitivity_table(cell, "discount_rate", discount_rates, col_name, terminal_values)
    return DcfResult(**{**vars(result), "sensitivity": grid})


DEBT_CONCEPTS = ("LongTermDebt", "CurrentDebt", "CommercialPaper")
CASH_CONCEPTS = ("CashAndEquivalents", "ShortTermInvestments")


def dcf_base_from_statements(company_id: str, statements: dict[date, StatementPeriod]) -> DcfBase:
    """Reported starting point from the latest fiscal year; every input keeps its fact id.

    Revenue, at least one cash line and a diluted share count are required (`Undefined`
    otherwise). Debt components a filer does not report count as zero, as in the KPI engine.
    """
    if not statements:
        raise Undefined("no fiscal-year statements available")
    period = statements[max(statements)]
    missing = [c for c in ("Revenue",) if c not in period.facts]
    shares = period.facts.get("WeightedSharesDiluted") or period.facts.get("SharesOutstanding")
    if shares is None:
        missing.append("WeightedSharesDiluted")
    if not any(c in period.facts for c in CASH_CONCEPTS):
        missing.append("CashAndEquivalents")
    if missing:
        raise Undefined(f"missing reported inputs for the DCF base: {', '.join(missing)}")
    assert shares is not None
    debt = [period.facts[c] for c in DEBT_CONCEPTS if c in period.facts]
    cash = [period.facts[c] for c in CASH_CONCEPTS if c in period.facts]
    revenue = period.facts["Revenue"]
    return DcfBase(
        company_id=company_id,
        base_period_end=period.period_end,
        base_revenue=revenue.value,
        total_debt=sum(f.value for f in debt),
        cash_and_investments=sum(f.value for f in cash),
        diluted_shares=shares.value,
        sources={
            "base_revenue": revenue.fact_id,
            "total_debt": ",".join(f.fact_id for f in debt) or "not reported (0)",
            "cash_and_investments": ",".join(f.fact_id for f in cash),
            "diluted_shares": shares.fact_id,
        },
    )
