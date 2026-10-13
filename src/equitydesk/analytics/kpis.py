"""Historical KPI and ratio engine (deterministic; formulas documented in docs/ARCHITECTURE.md).

Every metric is declared with its formula and named inputs. A missing required input yields
`missing_input` (never an imputed value); a meaningless computation (division by zero, growth
from a non-positive base) yields `undefined`.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date

from equitydesk.analytics.accounting import StatementPeriod
from equitydesk.domain.metrics import MetricResult, MetricStatus

CALC_VERSION = "1.0.0"
DAYS_IN_YEAR = 365.0


class Undefined(ArithmeticError):
    """Raised inside a formula when the result is not meaningful."""


def div(a: float, b: float) -> float:
    if b == 0:
        raise Undefined("division by zero")
    return a / b


def growth(current: float, prior: float) -> float:
    if prior <= 0:
        raise Undefined("growth from a non-positive base")
    return current / prior - 1.0


def avg(a: float, b: float) -> float:
    return (a + b) / 2.0


def cagr(first: float, last: float, years: float) -> float:
    if first <= 0 or last <= 0 or years <= 0:
        raise Undefined("CAGR needs positive endpoints and a positive horizon")
    return float((last / first) ** (1.0 / years) - 1.0)


@dataclass(frozen=True)
class In:
    """A metric input: `concept` from the current (or prior) fiscal year."""

    label: str
    concept: str
    prior: bool = False
    optional: bool = False


Values = dict[str, float]


@dataclass(frozen=True)
class MetricDef:
    name: str
    unit: str
    formula: str
    inputs: tuple[In, ...]
    fn: Callable[[Values], float]
    note: str | None = None


def _total_debt(v: Values) -> float:
    return v["long_term_debt"] + v.get("current_debt", 0.0) + v.get("commercial_paper", 0.0)


def _cash_like(v: Values) -> float:
    return v["cash"] + v.get("short_term_investments", 0.0)


def _tax_rate(v: Values) -> float:
    return div(v["income_tax"], v["pretax_income"])


DEBT_INPUTS = (
    In("long_term_debt", "LongTermDebt"),
    In("current_debt", "CurrentDebt", optional=True),
    In("commercial_paper", "CommercialPaper", optional=True),
)
CASH_INPUTS = (
    In("cash", "CashAndEquivalents"),
    In("short_term_investments", "ShortTermInvestments", optional=True),
)
OPTIONAL_NOTE = "optional debt/cash components absent from the filing are treated as not reported (0) and listed in `missing`"

METRICS: tuple[MetricDef, ...] = (
    MetricDef("revenue_growth", "ratio", "Revenue / Revenue[t-1] - 1",
              (In("revenue", "Revenue"), In("revenue_prior", "Revenue", prior=True)),
              lambda v: growth(v["revenue"], v["revenue_prior"])),
    MetricDef("eps_diluted_growth", "ratio", "EPSDiluted / EPSDiluted[t-1] - 1",
              (In("eps", "EPSDiluted"), In("eps_prior", "EPSDiluted", prior=True)),
              lambda v: growth(v["eps"], v["eps_prior"])),
    MetricDef("gross_margin", "ratio", "GrossProfit / Revenue",
              (In("gross_profit", "GrossProfit"), In("revenue", "Revenue")),
              lambda v: div(v["gross_profit"], v["revenue"])),
    MetricDef("operating_margin", "ratio", "OperatingIncome / Revenue",
              (In("operating_income", "OperatingIncome"), In("revenue", "Revenue")),
              lambda v: div(v["operating_income"], v["revenue"])),
    MetricDef("net_margin", "ratio", "NetIncome / Revenue",
              (In("net_income", "NetIncome"), In("revenue", "Revenue")),
              lambda v: div(v["net_income"], v["revenue"])),
    MetricDef("ebitda", "USD", "OperatingIncome + DepreciationAmortization",
              (In("operating_income", "OperatingIncome"), In("d_and_a", "DepreciationAmortization")),
              lambda v: v["operating_income"] + v["d_and_a"]),
    MetricDef("free_cash_flow", "USD", "OperatingCashFlow - CapitalExpenditure",
              (In("operating_cash_flow", "OperatingCashFlow"), In("capex", "CapitalExpenditure")),
              lambda v: v["operating_cash_flow"] - v["capex"]),
    MetricDef("fcf_margin", "ratio", "(OperatingCashFlow - CapitalExpenditure) / Revenue",
              (In("operating_cash_flow", "OperatingCashFlow"), In("capex", "CapitalExpenditure"), In("revenue", "Revenue")),
              lambda v: div(v["operating_cash_flow"] - v["capex"], v["revenue"])),
    MetricDef("fcf_growth", "ratio", "FCF / FCF[t-1] - 1",
              (In("ocf", "OperatingCashFlow"), In("capex", "CapitalExpenditure"),
               In("ocf_prior", "OperatingCashFlow", prior=True), In("capex_prior", "CapitalExpenditure", prior=True)),
              lambda v: growth(v["ocf"] - v["capex"], v["ocf_prior"] - v["capex_prior"])),
    MetricDef("effective_tax_rate", "ratio", "IncomeTaxExpense / PretaxIncome",
              (In("income_tax", "IncomeTaxExpense"), In("pretax_income", "PretaxIncome")), _tax_rate),
    MetricDef("total_debt", "USD", "LongTermDebt + CurrentDebt + CommercialPaper",
              DEBT_INPUTS, _total_debt, OPTIONAL_NOTE),
    MetricDef("net_debt", "USD", "TotalDebt - CashAndEquivalents - ShortTermInvestments",
              DEBT_INPUTS + CASH_INPUTS, lambda v: _total_debt(v) - _cash_like(v), OPTIONAL_NOTE),
    MetricDef("net_debt_to_ebitda", "x", "NetDebt / (OperatingIncome + DepreciationAmortization)",
              DEBT_INPUTS + CASH_INPUTS + (In("operating_income", "OperatingIncome"), In("d_and_a", "DepreciationAmortization")),
              lambda v: div(_total_debt(v) - _cash_like(v), v["operating_income"] + v["d_and_a"]), OPTIONAL_NOTE),
    MetricDef("debt_to_equity", "x", "TotalDebt / StockholdersEquity",
              DEBT_INPUTS + (In("equity", "StockholdersEquity"),),
              lambda v: div(_total_debt(v), v["equity"]), OPTIONAL_NOTE),
    MetricDef("current_ratio", "x", "CurrentAssets / CurrentLiabilities",
              (In("current_assets", "CurrentAssets"), In("current_liabilities", "CurrentLiabilities")),
              lambda v: div(v["current_assets"], v["current_liabilities"])),
    MetricDef("quick_ratio", "x", "(CurrentAssets - Inventory) / CurrentLiabilities",
              (In("current_assets", "CurrentAssets"), In("inventory", "Inventory"), In("current_liabilities", "CurrentLiabilities")),
              lambda v: div(v["current_assets"] - v["inventory"], v["current_liabilities"])),
    MetricDef("interest_coverage", "x", "OperatingIncome / InterestExpense",
              (In("operating_income", "OperatingIncome"), In("interest_expense", "InterestExpense")),
              lambda v: div(v["operating_income"], v["interest_expense"])),
    MetricDef("roe", "ratio", "NetIncome / avg(StockholdersEquity[t], StockholdersEquity[t-1])",
              (In("net_income", "NetIncome"), In("equity", "StockholdersEquity"), In("equity_prior", "StockholdersEquity", prior=True)),
              lambda v: div(v["net_income"], avg(v["equity"], v["equity_prior"]))),
    MetricDef("roa", "ratio", "NetIncome / avg(TotalAssets[t], TotalAssets[t-1])",
              (In("net_income", "NetIncome"), In("assets", "TotalAssets"), In("assets_prior", "TotalAssets", prior=True)),
              lambda v: div(v["net_income"], avg(v["assets"], v["assets_prior"]))),
    MetricDef(
        "roic", "ratio",
        "OperatingIncome * (1 - IncomeTaxExpense/PretaxIncome) / avg(InvestedCapital[t], InvestedCapital[t-1]); "
        "InvestedCapital = StockholdersEquity + LongTermDebt + CurrentDebt + CommercialPaper - CashAndEquivalents - ShortTermInvestments",
        (In("operating_income", "OperatingIncome"), In("income_tax", "IncomeTaxExpense"), In("pretax_income", "PretaxIncome"),
         In("equity", "StockholdersEquity"), In("long_term_debt", "LongTermDebt"), In("current_debt", "CurrentDebt", optional=True),
         In("commercial_paper", "CommercialPaper", optional=True), In("cash", "CashAndEquivalents"),
         In("short_term_investments", "ShortTermInvestments", optional=True),
         In("equity_p", "StockholdersEquity", prior=True), In("long_term_debt_p", "LongTermDebt", prior=True),
         In("current_debt_p", "CurrentDebt", prior=True, optional=True),
         In("commercial_paper_p", "CommercialPaper", prior=True, optional=True),
         In("cash_p", "CashAndEquivalents", prior=True), In("short_term_investments_p", "ShortTermInvestments", prior=True, optional=True)),
        lambda v: div(
            v["operating_income"] * (1 - _tax_rate(v)),
            avg(
                v["equity"] + _total_debt(v) - _cash_like(v),
                v["equity_p"] + v["long_term_debt_p"] + v.get("current_debt_p", 0.0) + v.get("commercial_paper_p", 0.0)
                - v["cash_p"] - v.get("short_term_investments_p", 0.0),
            ),
        ),
        OPTIONAL_NOTE,
    ),
    MetricDef("diluted_share_change", "ratio", "WeightedSharesDiluted / WeightedSharesDiluted[t-1] - 1",
              (In("shares", "WeightedSharesDiluted"), In("shares_prior", "WeightedSharesDiluted", prior=True)),
              lambda v: growth(v["shares"], v["shares_prior"])),
    MetricDef("shares_outstanding_change", "ratio", "SharesOutstanding / SharesOutstanding[t-1] - 1",
              (In("shares", "SharesOutstanding"), In("shares_prior", "SharesOutstanding", prior=True)),
              lambda v: growth(v["shares"], v["shares_prior"])),
    MetricDef("days_sales_outstanding", "days", "AccountsReceivable / Revenue * 365",
              (In("receivables", "AccountsReceivable"), In("revenue", "Revenue")),
              lambda v: div(v["receivables"], v["revenue"]) * DAYS_IN_YEAR),
    MetricDef("days_inventory_outstanding", "days", "Inventory / CostOfRevenue * 365",
              (In("inventory", "Inventory"), In("cost_of_revenue", "CostOfRevenue")),
              lambda v: div(v["inventory"], v["cost_of_revenue"]) * DAYS_IN_YEAR),
    MetricDef("days_payables_outstanding", "days", "AccountsPayable / CostOfRevenue * 365",
              (In("payables", "AccountsPayable"), In("cost_of_revenue", "CostOfRevenue")),
              lambda v: div(v["payables"], v["cost_of_revenue"]) * DAYS_IN_YEAR),
    MetricDef("cash_conversion_cycle", "days", "DSO + DIO - DPO",
              (In("receivables", "AccountsReceivable"), In("revenue", "Revenue"), In("inventory", "Inventory"),
               In("payables", "AccountsPayable"), In("cost_of_revenue", "CostOfRevenue")),
              lambda v: (div(v["receivables"], v["revenue"]) + div(v["inventory"] - v["payables"], v["cost_of_revenue"])) * DAYS_IN_YEAR),
    MetricDef("cash_conversion", "ratio", "OperatingCashFlow / NetIncome",
              (In("operating_cash_flow", "OperatingCashFlow"), In("net_income", "NetIncome")),
              lambda v: div(v["operating_cash_flow"], v["net_income"])),
)  # fmt: skip

METRIC_INDEX = {m.name: m for m in METRICS}


def metric_id(company_id: str, name: str, period_end: date) -> str:
    return f"m:{company_id}:{name}:{period_end.isoformat()}"


def evaluate(
    metric: MetricDef,
    company_id: str,
    current: StatementPeriod,
    prior: StatementPeriod | None,
) -> MetricResult:
    values: Values = {}
    inputs: dict[str, float | None] = {}
    fact_ids: list[str] = []
    missing: list[str] = []
    missing_optional: list[str] = []
    for spec in metric.inputs:
        period = prior if spec.prior else current
        fact = period.facts.get(spec.concept) if period is not None else None
        inputs[spec.label] = fact.value if fact else None
        if fact is None:
            (missing_optional if spec.optional else missing).append(spec.label)
            continue
        values[spec.label] = fact.value
        fact_ids.append(fact.fact_id)
    base = {
        "metric_id": metric_id(company_id, metric.name, current.period_end),
        "company_id": company_id,
        "name": metric.name,
        "period_end": current.period_end,
        "unit": metric.unit,
        "formula": metric.formula,
        "inputs": inputs,
        "input_fact_ids": tuple(fact_ids),
        "calc_version": CALC_VERSION,
    }
    if missing:
        return MetricResult(
            **base,
            value=None,
            status=MetricStatus.MISSING_INPUT,
            missing=tuple(missing + missing_optional),
        )
    try:
        value = float(metric.fn(values))
    except Undefined as exc:
        return MetricResult(
            **base,
            value=None,
            status=MetricStatus.UNDEFINED,
            missing=tuple(missing_optional),
            note=str(exc),
        )
    return MetricResult(
        **base,
        value=value,
        status=MetricStatus.OK,
        missing=tuple(missing_optional),
        note=metric.note if missing_optional else None,
    )


def prior_period(
    statements: dict[date, StatementPeriod], period_end: date
) -> StatementPeriod | None:
    """The fiscal year immediately preceding `period_end` (330-380 days earlier)."""
    candidates = [p for end, p in statements.items() if 330 <= (period_end - end).days <= 380]
    return max(candidates, key=lambda p: p.period_end) if candidates else None


def compute_kpis(
    company_id: str,
    statements: dict[date, StatementPeriod],
    metrics: tuple[MetricDef, ...] = METRICS,
) -> list[MetricResult]:
    results: list[MetricResult] = []
    for end in sorted(statements):
        current = statements[end]
        prior = prior_period(statements, end)
        results.extend(evaluate(m, company_id, current, prior) for m in metrics)
    return results


def revenue_cagr(company_id: str, statements: dict[date, StatementPeriod]) -> MetricResult:
    """Revenue CAGR between the first and last fiscal years available."""
    ends = sorted(e for e, p in statements.items() if "Revenue" in p.facts)
    if len(ends) < 2:
        end = max(statements) if statements else date.min
        return MetricResult(
            metric_id=metric_id(company_id, "revenue_cagr", end), company_id=company_id,
            name="revenue_cagr", period_end=end, value=None, unit="ratio",
            formula="(Revenue[last] / Revenue[first]) ** (1 / years) - 1",
            status=MetricStatus.MISSING_INPUT, missing=("revenue_history",), calc_version=CALC_VERSION,
        )  # fmt: skip
    first, last = statements[ends[0]].facts["Revenue"], statements[ends[-1]].facts["Revenue"]
    years = round((ends[-1] - ends[0]).days / 365.25)
    base = {
        "metric_id": metric_id(company_id, "revenue_cagr", ends[-1]),
        "company_id": company_id,
        "name": "revenue_cagr",
        "period_end": ends[-1],
        "unit": "ratio",
        "formula": "(Revenue[last] / Revenue[first]) ** (1 / years) - 1",
        "inputs": {"revenue_first": first.value, "revenue_last": last.value, "years": float(years)},
        "input_fact_ids": (first.fact_id, last.fact_id),
        "calc_version": CALC_VERSION,
    }
    try:
        return MetricResult(
            **base, value=cagr(first.value, last.value, years), status=MetricStatus.OK
        )
    except Undefined as exc:
        return MetricResult(**base, value=None, status=MetricStatus.UNDEFINED, note=str(exc))
