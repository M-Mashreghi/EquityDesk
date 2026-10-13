"""Multi-period trends, margin deltas and quality-of-earnings diagnostics.

Thresholds are explicit, documented heuristics (docs/ARCHITECTURE.md). A triggered diagnostic
is a prompt for investigation, not a conclusion.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date

from equitydesk.analytics.accounting import StatementPeriod
from equitydesk.analytics.kpis import CALC_VERSION, Undefined, avg, div, growth, prior_period
from equitydesk.domain.metrics import DiagnosticResult, MetricResult, MetricStatus, TrendSummary

FLAT_TOLERANCE = {"ratio": 0.0005, "default": 1e-9}  # 5 bps for ratios


def series(results: Iterable[MetricResult], name: str) -> list[tuple[date, float]]:
    return sorted(
        (r.period_end, r.value)
        for r in results
        if r.name == name and r.status is MetricStatus.OK and r.value is not None
    )


def trend(results: Iterable[MetricResult], name: str) -> TrendSummary:
    results = list(results)
    unit = next((r.unit for r in results if r.name == name), "unknown")
    points = series(results, name)
    if len(points) < 2:
        return TrendSummary(
            metric=name,
            unit=unit,
            points=tuple(points),
            first=points[0][1] if points else None,
            last=points[-1][1] if points else None,
            direction="insufficient_data",
        )
    first, last = points[0][1], points[-1][1]
    is_ratio = unit == "ratio"
    change = (last - first) * (10_000 if is_ratio else 1)
    tol = FLAT_TOLERANCE["ratio"] if is_ratio else FLAT_TOLERANCE["default"] * max(1.0, abs(first))
    direction = "flat" if abs(last - first) <= tol else ("up" if last > first else "down")
    declines = increases = 0
    for (_, a), (_, b) in zip(reversed(points[:-1]), reversed(points[1:]), strict=True):
        if b < a and increases == 0:
            declines += 1
        elif b > a and declines == 0:
            increases += 1
        else:
            break
    return TrendSummary(
        metric=name,
        unit=unit,
        points=tuple(points),
        first=first,
        last=last,
        change=change,
        change_unit="bps" if is_ratio else unit,
        consecutive_declines=declines,
        consecutive_increases=increases,
        direction=direction,
    )


def margin_deltas_bps(results: Iterable[MetricResult]) -> dict[str, list[tuple[date, float]]]:
    """Year-over-year change of each margin in basis points."""
    results = list(results)
    out: dict[str, list[tuple[date, float]]] = {}
    for name in ("gross_margin", "operating_margin", "net_margin", "fcf_margin"):
        pts = series(results, name)
        out[name] = [
            (b_end, (b - a) * 10_000)
            for (a_end, a), (b_end, b) in zip(pts, pts[1:], strict=False)
            if 330 <= (b_end - a_end).days <= 380
        ]
    return out


@dataclass(frozen=True)
class DiagnosticDef:
    name: str
    formula: str
    concepts: dict[str, tuple[str, bool]]  # label -> (concept, from prior year)
    compute: Callable[[dict[str, float]], float]
    threshold: float
    comparison: str  # ">" triggers when value > threshold, "<" when value < threshold
    explanation: str


DIAGNOSTICS: tuple[DiagnosticDef, ...] = (
    DiagnosticDef(
        "low_cash_conversion",
        "OperatingCashFlow / NetIncome",
        {"operating_cash_flow": ("OperatingCashFlow", False), "net_income": ("NetIncome", False)},
        lambda v: div(v["operating_cash_flow"], v["net_income"]) if v["net_income"] > 0 else _undef(),
        0.8,
        "<",
        "Operating cash flow below 80% of positive net income: earnings less backed by cash.",
    ),
    DiagnosticDef(
        "high_accruals",
        "(NetIncome - OperatingCashFlow) / avg(TotalAssets[t], TotalAssets[t-1])",
        {
            "net_income": ("NetIncome", False),
            "operating_cash_flow": ("OperatingCashFlow", False),
            "assets": ("TotalAssets", False),
            "assets_prior": ("TotalAssets", True),
        },
        lambda v: div(v["net_income"] - v["operating_cash_flow"], avg(v["assets"], v["assets_prior"])),
        0.10,
        ">",
        "Sloan accrual ratio above 10% of average assets.",
    ),
    DiagnosticDef(
        "receivables_outpacing_revenue",
        "growth(AccountsReceivable) - growth(Revenue)",
        {
            "receivables": ("AccountsReceivable", False),
            "receivables_prior": ("AccountsReceivable", True),
            "revenue": ("Revenue", False),
            "revenue_prior": ("Revenue", True),
        },
        lambda v: growth(v["receivables"], v["receivables_prior"]) - growth(v["revenue"], v["revenue_prior"]),
        0.10,
        ">",
        "Receivables growing >10 percentage points faster than revenue.",
    ),
    DiagnosticDef(
        "inventory_outpacing_revenue",
        "growth(Inventory) - growth(Revenue)",
        {
            "inventory": ("Inventory", False),
            "inventory_prior": ("Inventory", True),
            "revenue": ("Revenue", False),
            "revenue_prior": ("Revenue", True),
        },
        lambda v: growth(v["inventory"], v["inventory_prior"]) - growth(v["revenue"], v["revenue_prior"]),
        0.10,
        ">",
        "Inventory growing >10 percentage points faster than revenue.",
    ),
    DiagnosticDef(
        "negative_fcf_with_profit",
        "OperatingCashFlow - CapitalExpenditure  (evaluated only when NetIncome > 0)",
        {
            "operating_cash_flow": ("OperatingCashFlow", False),
            "capex": ("CapitalExpenditure", False),
            "net_income": ("NetIncome", False),
        },
        lambda v: v["operating_cash_flow"] - v["capex"] if v["net_income"] > 0 else _undef(),
        0.0,
        "<",
        "Free cash flow negative while net income is positive.",
    ),
    DiagnosticDef(
        "share_dilution",
        "WeightedSharesDiluted / WeightedSharesDiluted[t-1] - 1",
        {"shares": ("WeightedSharesDiluted", False), "shares_prior": ("WeightedSharesDiluted", True)},
        lambda v: growth(v["shares"], v["shares_prior"]),
        0.02,
        ">",
        "Diluted share count up more than 2% year over year.",
    ),
    DiagnosticDef(
        "high_sbc_share_of_ocf",
        "StockBasedCompensation / OperatingCashFlow",
        {"sbc": ("StockBasedCompensation", False), "operating_cash_flow": ("OperatingCashFlow", False)},
        lambda v: div(v["sbc"], v["operating_cash_flow"]) if v["operating_cash_flow"] > 0 else _undef(),
        0.25,
        ">",
        "Stock-based compensation exceeds 25% of operating cash flow (non-cash add-back).",
    ),
    DiagnosticDef(
        "operating_margin_compression",
        "(OperatingIncome/Revenue - OperatingIncome[t-1]/Revenue[t-1]) * 10000  [bps]",
        {
            "operating_income": ("OperatingIncome", False),
            "revenue": ("Revenue", False),
            "operating_income_prior": ("OperatingIncome", True),
            "revenue_prior": ("Revenue", True),
        },
        lambda v: (div(v["operating_income"], v["revenue"]) - div(v["operating_income_prior"], v["revenue_prior"])) * 10_000,
        -200.0,
        "<",
        "Operating margin down more than 200 bps year over year.",
    ),
)  # fmt: skip


def _undef() -> float:
    raise Undefined("precondition of the diagnostic not met")


def run_diagnostics(
    company_id: str,
    statements: dict[date, StatementPeriod],
    diagnostics: tuple[DiagnosticDef, ...] = DIAGNOSTICS,
) -> list[DiagnosticResult]:
    results: list[DiagnosticResult] = []
    for end in sorted(statements):
        current, prior = statements[end], prior_period(statements, end)
        for d in diagnostics:
            components: dict[str, float | None] = {}
            for label, (concept, use_prior) in d.concepts.items():
                period = prior if use_prior else current
                fact = period.facts.get(concept) if period else None
                components[label] = fact.value if fact else None
            base = {
                "diagnostic_id": f"d:{company_id}:{d.name}:{end.isoformat()}",
                "company_id": company_id,
                "name": d.name,
                "period_end": end,
                "formula": d.formula,
                "components": components,
                "threshold": d.threshold,
                "comparison": d.comparison,
                "explanation": d.explanation,
                "calc_version": CALC_VERSION,
            }
            if any(v is None for v in components.values()):
                results.append(
                    DiagnosticResult(**base, value=None, status=MetricStatus.MISSING_INPUT)
                )
                continue
            try:
                value = float(d.compute({k: v for k, v in components.items() if v is not None}))
            except Undefined:
                results.append(DiagnosticResult(**base, value=None, status=MetricStatus.UNDEFINED))
                continue
            triggered = value > d.threshold if d.comparison == ">" else value < d.threshold
            results.append(
                DiagnosticResult(**base, value=value, triggered=triggered, status=MetricStatus.OK)
            )
    return results
