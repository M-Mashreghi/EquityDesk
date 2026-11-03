"""Bull / base / bear scenario builder on top of the deterministic DCF engine.

Every output value is labeled `fact` (reported input), `assumption` (explicit scenario input) or
`calculation` (deterministic result), and scenario sets are checked for internal consistency
before they can feed a report.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any

from equitydesk.analytics.dcf import DCF_VERSION, DcfBase, DcfResult, assumptions_hash, run_dcf
from equitydesk.analytics.kpis import Undefined
from equitydesk.analytics.valuation import VALUATION_VERSION
from equitydesk.domain.market import MarketObservation
from equitydesk.domain.valuation import (
    DOCUMENTED_FIELDS,
    ScenarioCase,
    ScenarioName,
    TerminalMethod,
    ValuationMethod,
    ValuationRun,
)

SCENARIO_VERSION = "1.0.0"
PROBABILITY_TOLERANCE = 1e-6
MAX_TERMINAL_SHARE_OF_EV = 0.85
MAX_TERMINAL_GROWTH = 0.04


class ValueKind(StrEnum):
    FACT = "fact"
    ASSUMPTION = "assumption"
    CALCULATION = "calculation"


@dataclass(frozen=True)
class LabeledValue:
    name: str
    value: float
    kind: ValueKind
    unit: str
    provenance: str  # fact source id, assumption source, or formula


@dataclass(frozen=True)
class ConsistencyIssue:
    code: str
    blocking: bool
    message: str
    scenario: str | None = None


@dataclass(frozen=True)
class ScenarioOutput:
    case: ScenarioCase
    dcf: DcfResult
    values: tuple[LabeledValue, ...]
    upside: float | None  # value per share / current price - 1

    def value(self, name: str) -> LabeledValue:
        return next(v for v in self.values if v.name == name)


@dataclass(frozen=True)
class ScenarioAnalysis:
    company_id: str
    base: DcfBase
    price: MarketObservation | None
    outputs: dict[ScenarioName, ScenarioOutput]
    probability_weighted_value: float | None
    issues: tuple[ConsistencyIssue, ...]

    @property
    def is_consistent(self) -> bool:
        return not any(i.blocking for i in self.issues)

    def to_valuation_run(
        self, research_run_id: str, valuation_date: datetime, created_at: datetime
    ) -> ValuationRun:
        inputs: dict[str, Any] = {
            "company_id": self.company_id,
            "base_period_end": self.base.base_period_end.isoformat(),
            "facts": {
                v.name: {"value": v.value, "source": v.provenance}
                for v in next(iter(self.outputs.values())).values
                if v.kind is ValueKind.FACT
            },
        }
        if self.price is not None:
            inputs["price"] = {
                "value": self.price.price,
                "observed_at": self.price.observed_at.isoformat(),
                "source": self.price.price_source,
            }
        assumptions = {
            name.value: o.case.assumptions.model_dump(mode="json")
            | {"assumptions_hash": assumptions_hash(o.case.assumptions),
               "probability": o.case.probability, "narrative": o.case.narrative}
            for name, o in self.outputs.items()
        }  # fmt: skip
        outputs: dict[str, Any] = {
            name.value: {
                "values": [
                    {"name": v.name, "value": v.value, "kind": v.kind.value, "unit": v.unit,
                     "provenance": v.provenance}
                    for v in o.values
                ],
                "upside": o.upside,
            }
            for name, o in self.outputs.items()
        }  # fmt: skip
        outputs["probability_weighted_value"] = self.probability_weighted_value
        outputs["issues"] = [vars(i) for i in self.issues]
        digest = hashlib.sha256(
            json.dumps([inputs, assumptions], sort_keys=True).encode()
        ).hexdigest()
        return ValuationRun(
            valuation_id=f"val_scn_{digest[:20]}",
            research_run_id=research_run_id,
            method=ValuationMethod.SCENARIO,
            valuation_date=valuation_date,
            formula="per scenario: DCF(base facts, scenario assumptions); "
            "weighted = sum(probability_s * value_per_share_s)",
            inputs=inputs,
            assumptions=assumptions,
            outputs=outputs,
            version=f"{VALUATION_VERSION}/dcf-{DCF_VERSION}/scenario-{SCENARIO_VERSION}",
            created_at=created_at,
        )


def _labeled(case: ScenarioCase, base: DcfBase, r: DcfResult) -> tuple[LabeledValue, ...]:
    a = case.assumptions
    src = base.sources
    facts = [
        LabeledValue("base_revenue", base.base_revenue, ValueKind.FACT, "currency",
                     src.get("base_revenue", "unsourced")),
        LabeledValue("total_debt", base.total_debt, ValueKind.FACT, "currency",
                     src.get("total_debt", "unsourced")),
        LabeledValue("cash_and_investments", base.cash_and_investments, ValueKind.FACT,
                     "currency", src.get("cash_and_investments", "unsourced")),
        LabeledValue("diluted_shares", base.diluted_shares, ValueKind.FACT, "shares",
                     src.get("diluted_shares", "unsourced")),
    ]  # fmt: skip
    assumed = [
        *(LabeledValue(f"revenue_growth_y{t}", g, ValueKind.ASSUMPTION, "ratio", a.source)
          for t, g in enumerate(a.revenue_growth, 1)),
        *(LabeledValue(f"ebit_margin_y{t}", m, ValueKind.ASSUMPTION, "ratio", a.source)
          for t, m in enumerate(a.ebit_margin, 1)),
        LabeledValue("tax_rate", a.tax_rate, ValueKind.ASSUMPTION, "ratio", a.source),
        LabeledValue("da_pct_revenue", a.da_pct_revenue, ValueKind.ASSUMPTION, "ratio", a.source),
        LabeledValue("capex_pct_revenue", a.capex_pct_revenue, ValueKind.ASSUMPTION, "ratio",
                     a.source),
        LabeledValue("nwc_pct_incremental_revenue", a.nwc_pct_incremental_revenue,
                     ValueKind.ASSUMPTION, "ratio", a.source),
        LabeledValue("discount_rate", a.discount_rate, ValueKind.ASSUMPTION, "ratio", a.source),
    ]  # fmt: skip
    if a.terminal_growth is not None:
        assumed.append(LabeledValue("terminal_growth", a.terminal_growth, ValueKind.ASSUMPTION,
                                    "ratio", a.source))  # fmt: skip
    if a.exit_ev_ebitda is not None:
        assumed.append(LabeledValue("exit_ev_ebitda", a.exit_ev_ebitda, ValueKind.ASSUMPTION,
                                    "x", a.source))  # fmt: skip
    final = r.years[-1]
    n = len(r.years)
    calc = [
        LabeledValue("final_year_revenue", final.revenue, ValueKind.CALCULATION, "currency",
                     "base_revenue * prod(1 + revenue_growth_t)"),
        LabeledValue("revenue_cagr", (final.revenue / base.base_revenue) ** (1 / n) - 1,
                     ValueKind.CALCULATION, "ratio", "(final_year_revenue / base_revenue) ** (1 / n) - 1"),
        LabeledValue("final_year_ebit", final.ebit, ValueKind.CALCULATION, "currency",
                     "final_year_revenue * ebit_margin_n"),
        LabeledValue("cumulative_fcff", sum(y.fcff for y in r.years), ValueKind.CALCULATION,
                     "currency", "sum(fcff_t)"),
        LabeledValue("enterprise_value", r.enterprise_value, ValueKind.CALCULATION, "currency",
                     "sum(pv fcff_t) + pv terminal value"),
        LabeledValue("equity_value", r.equity_value.value, ValueKind.CALCULATION, "currency",
                     r.equity_value.formula),
        LabeledValue("value_per_share", r.value_per_share.value, ValueKind.CALCULATION,
                     "currency/share", r.value_per_share.formula),
        LabeledValue("terminal_share_of_ev", r.terminal_share_of_ev, ValueKind.CALCULATION,
                     "ratio", "pv terminal value / enterprise_value"),
    ]  # fmt: skip
    return (*facts, *assumed, *calc)


def _check_cases(cases: Sequence[ScenarioCase]) -> list[ConsistencyIssue]:
    issues: list[ConsistencyIssue] = []
    names = [c.name for c in cases]
    for name in ScenarioName:
        if names.count(name) != 1:
            issues.append(ConsistencyIssue("scenario_set_incomplete", True,
                                           f"expected exactly one {name.value} case, got {names.count(name)}"))  # fmt: skip
    for c in cases:
        a = c.assumptions
        documented = set(DOCUMENTED_FIELDS) | {
            "terminal_growth" if a.terminal_method is TerminalMethod.GORDON else "exit_ev_ebitda"
        }
        for fld in sorted(documented - {k for k, v in a.rationale.items() if v.strip()}):
            issues.append(ConsistencyIssue("undocumented_assumption", True,
                                           f"{fld} has no written rationale", c.name.value))  # fmt: skip
        if a.terminal_growth is not None and a.terminal_growth > MAX_TERMINAL_GROWTH:
            issues.append(ConsistencyIssue("aggressive_terminal_growth", False,
                                           f"terminal growth {a.terminal_growth:.2%} exceeds {MAX_TERMINAL_GROWTH:.0%}",
                                           c.name.value))  # fmt: skip
    if len({c.assumptions.horizon_years for c in cases}) > 1:
        issues.append(ConsistencyIssue("horizon_mismatch", True,
                                       "scenarios use different forecast horizons"))  # fmt: skip
    if len({c.assumptions.terminal_method for c in cases}) > 1:
        issues.append(ConsistencyIssue("terminal_method_mismatch", False,
                                       "scenarios use different terminal-value methods"))  # fmt: skip
    probs = [c.probability for c in cases]
    if any(p is not None for p in probs):
        if any(p is None for p in probs):
            issues.append(ConsistencyIssue("partial_probabilities", True,
                                           "either every scenario or none carries a probability"))  # fmt: skip
        elif abs(sum(p for p in probs if p is not None) - 1.0) > PROBABILITY_TOLERANCE:
            issues.append(ConsistencyIssue("probabilities_do_not_sum_to_one", True,
                                           f"probabilities sum to {sum(p for p in probs if p is not None):.4f}"))  # fmt: skip
    return issues


def _check_ordering(outputs: dict[ScenarioName, ScenarioOutput]) -> list[ConsistencyIssue]:
    issues: list[ConsistencyIssue] = []
    if not all(n in outputs for n in ScenarioName):
        return issues
    bull, base, bear = (outputs[ScenarioName.BULL], outputs[ScenarioName.BASE],
                        outputs[ScenarioName.BEAR])  # fmt: skip
    v = [o.dcf.value_per_share.value for o in (bull, base, bear)]
    if not v[0] >= v[1] >= v[2]:
        issues.append(ConsistencyIssue("value_ordering", True,
                                       f"expected bull >= base >= bear per-share value, got {v[0]:.2f} / {v[1]:.2f} / {v[2]:.2f}"))  # fmt: skip
    for label, metric in (("revenue", "final_year_revenue"), ("EBIT", "final_year_ebit")):
        x = [o.value(metric).value for o in (bull, base, bear)]
        if not x[0] >= x[1] >= x[2]:
            issues.append(ConsistencyIssue("driver_ordering", False,
                                           f"final-year {label} is not ordered bull >= base >= bear"))  # fmt: skip
    for name, o in outputs.items():
        if o.dcf.terminal_share_of_ev > MAX_TERMINAL_SHARE_OF_EV:
            issues.append(ConsistencyIssue("terminal_value_dominates", False,
                                           f"terminal value is {o.dcf.terminal_share_of_ev:.0%} of EV",
                                           name.value))  # fmt: skip
    return issues


def build_scenarios(
    base: DcfBase,
    cases: Sequence[ScenarioCase],
    price: MarketObservation | None = None,
) -> ScenarioAnalysis:
    """Run every case on the same reported base facts and check the set for consistency.

    `price` must already be the point-in-time observation for the research `as_of`.
    """
    issues = _check_cases(cases)
    outputs: dict[ScenarioName, ScenarioOutput] = {}
    for c in cases:
        if c.name in outputs:
            continue
        try:
            r = run_dcf(base, c.assumptions)
        except Undefined as exc:
            issues.append(ConsistencyIssue("undefined_valuation", True, str(exc), c.name.value))
            continue
        upside = r.value_per_share.value / price.price - 1 if price is not None else None
        outputs[c.name] = ScenarioOutput(c, r, _labeled(c, base, r), upside)
    issues.extend(_check_ordering(outputs))
    weighted: float | None = None
    if (
        outputs
        and len(outputs) == len(cases)
        and all(c.probability is not None for c in cases)
        and not any(i.code.startswith(("probabilities", "partial")) for i in issues)
    ):
        weighted = sum(
            (o.case.probability or 0.0) * o.dcf.value_per_share.value for o in outputs.values()
        )
    return ScenarioAnalysis(base.company_id, base, price, outputs, weighted, tuple(issues))
