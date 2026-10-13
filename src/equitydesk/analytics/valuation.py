"""Reusable, transparent valuation primitives.

Every primitive returns a `Computation` carrying its value, formula and inputs, so any number
in a report can be traced. Invalid economics (non-positive share count, r <= g, negative
multiple denominators) raise `Undefined` instead of producing a misleading number.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime

from equitydesk.analytics.kpis import Undefined
from equitydesk.domain.market import MarketObservation, observation_as_of
from equitydesk.domain.valuation import Assumption

VALUATION_VERSION = "1.0.0"


@dataclass(frozen=True)
class Computation:
    name: str
    value: float
    formula: str
    inputs: Mapping[str, float]
    components: tuple[Computation, ...] = field(default=())

    def as_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "value": self.value,
            "formula": self.formula,
            "inputs": dict(self.inputs),
            "components": [c.as_dict() for c in self.components],
        }


def market_cap(price: float, shares: float) -> Computation:
    if price <= 0 or shares <= 0:
        raise Undefined("price and shares must be positive")
    return Computation(
        "market_cap", price * shares, "price * shares", {"price": price, "shares": shares}
    )


def enterprise_value(
    market_capitalization: float,
    total_debt: float,
    cash_and_investments: float,
    preferred_equity: float = 0.0,
    minority_interest: float = 0.0,
) -> Computation:
    value = (
        market_capitalization
        + total_debt
        + preferred_equity
        + minority_interest
        - cash_and_investments
    )
    return Computation(
        "enterprise_value",
        value,
        "market_cap + total_debt + preferred_equity + minority_interest - cash_and_investments",
        {
            "market_cap": market_capitalization,
            "total_debt": total_debt,
            "preferred_equity": preferred_equity,
            "minority_interest": minority_interest,
            "cash_and_investments": cash_and_investments,
        },
    )


def equity_from_enterprise_value(
    ev: float,
    total_debt: float,
    cash_and_investments: float,
    preferred_equity: float = 0.0,
    minority_interest: float = 0.0,
) -> Computation:
    value = ev - total_debt - preferred_equity - minority_interest + cash_and_investments
    return Computation(
        "equity_value",
        value,
        "enterprise_value - total_debt - preferred_equity - minority_interest + cash_and_investments",
        {
            "enterprise_value": ev,
            "total_debt": total_debt,
            "preferred_equity": preferred_equity,
            "minority_interest": minority_interest,
            "cash_and_investments": cash_and_investments,
        },
    )


def per_share(equity_value: float, diluted_shares: float) -> Computation:
    if diluted_shares <= 0:
        raise Undefined("diluted share count must be positive")
    return Computation(
        "value_per_share",
        equity_value / diluted_shares,
        "equity_value / diluted_shares",
        {"equity_value": equity_value, "diluted_shares": diluted_shares},
    )


def valuation_multiple(name: str, numerator: float, denominator: float) -> Computation:
    """e.g. EV/EBITDA, P/E. A non-positive denominator gives no meaningful multiple."""
    if denominator <= 0:
        raise Undefined(f"{name}: denominator must be positive")
    return Computation(name, numerator / denominator, f"{name} = numerator / denominator",
                       {"numerator": numerator, "denominator": denominator})  # fmt: skip


def apply_multiple(name: str, multiple: float, metric: float) -> Computation:
    if multiple <= 0:
        raise Undefined("multiple must be positive")
    return Computation(
        name, multiple * metric, "multiple * metric", {"multiple": multiple, "metric": metric}
    )


def discount_factor(rate: float, period: float, mid_year: bool = False) -> float:
    if rate <= -1:
        raise Undefined("discount rate must exceed -100%")
    t = period - 0.5 if mid_year else period
    return float(1.0 / (1.0 + rate) ** t)


def present_value(cash_flows: Sequence[float], rate: float, mid_year: bool = False) -> Computation:
    """PV of cash flows at the end (or middle) of years 1..n."""
    factors = [discount_factor(rate, t, mid_year) for t in range(1, len(cash_flows) + 1)]
    value = sum(cf * df for cf, df in zip(cash_flows, factors, strict=True))
    inputs = {f"cf_{t}": cf for t, cf in enumerate(cash_flows, start=1)} | {"rate": rate}
    formula = "sum(cf_t / (1 + rate) ** (t - 0.5))" if mid_year else "sum(cf_t / (1 + rate) ** t)"
    return Computation("present_value", value, formula, inputs)


def gordon_terminal_value(final_cash_flow: float, rate: float, growth: float) -> Computation:
    if rate <= growth:
        raise Undefined("terminal value requires discount rate > terminal growth")
    return Computation(
        "terminal_value",
        final_cash_flow * (1 + growth) / (rate - growth),
        "final_cash_flow * (1 + g) / (rate - g)",
        {"final_cash_flow": final_cash_flow, "rate": rate, "g": growth},
    )


def exit_multiple_terminal_value(final_metric: float, multiple: float) -> Computation:
    if multiple <= 0:
        raise Undefined("exit multiple must be positive")
    return Computation("terminal_value", final_metric * multiple, "final_metric * exit_multiple",
                       {"final_metric": final_metric, "exit_multiple": multiple})  # fmt: skip


@dataclass(frozen=True)
class SensitivityTable:
    row_name: str
    row_values: tuple[float, ...]
    col_name: str
    col_values: tuple[float, ...]
    grid: tuple[tuple[float | None, ...], ...]  # None where undefined (e.g. r <= g)

    def value(self, row: float, col: float) -> float | None:
        return self.grid[self.row_values.index(row)][self.col_values.index(col)]


def sensitivity_table(
    fn: Callable[[float, float], float],
    row_name: str,
    row_values: Sequence[float],
    col_name: str,
    col_values: Sequence[float],
) -> SensitivityTable:
    grid: list[tuple[float | None, ...]] = []
    for r in row_values:
        row: list[float | None] = []
        for c in col_values:
            try:
                row.append(fn(r, c))
            except Undefined:
                row.append(None)
        grid.append(tuple(row))
    return SensitivityTable(row_name, tuple(row_values), col_name, tuple(col_values), tuple(grid))


@dataclass(frozen=True)
class MarketBridge:
    """Market-cap -> EV bridge pinned to one dated observation and explicit balance-sheet inputs."""

    observation: MarketObservation
    market_cap: Computation
    enterprise_value: Computation
    as_of: datetime
    balance_sheet_date: str
    assumptions: tuple[Assumption, ...]


def build_market_bridge(
    observations: Sequence[MarketObservation],
    security_id: str,
    as_of: datetime,
    total_debt: float,
    cash_and_investments: float,
    balance_sheet_date: str,
    assumptions: Sequence[Assumption] = (),
    preferred_equity: float = 0.0,
    minority_interest: float = 0.0,
) -> MarketBridge:
    obs = observation_as_of(observations, security_id, as_of)
    if obs.shares_outstanding is None:
        raise Undefined("market observation lacks a sourced share count")
    mcap = market_cap(obs.price, obs.shares_outstanding)
    ev = enterprise_value(
        mcap.value, total_debt, cash_and_investments, preferred_equity, minority_interest
    )
    return MarketBridge(
        observation=obs,
        market_cap=mcap,
        enterprise_value=Computation(ev.name, ev.value, ev.formula, ev.inputs, (mcap,)),
        as_of=as_of,
        balance_sheet_date=balance_sheet_date,
        assumptions=tuple(assumptions),
    )
