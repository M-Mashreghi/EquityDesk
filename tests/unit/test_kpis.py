"""KPI fixtures hand-checked against Apple's FY2021-FY2023 10-K figures (USD millions)."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

import factories as f
from equitydesk.analytics.accounting import StatementPeriod, annual_statements, map_reported_facts
from equitydesk.analytics.kpis import (
    METRICS,
    In,
    MetricDef,
    cagr,
    compute_kpis,
    div,
    evaluate,
    revenue_cagr,
)
from equitydesk.domain.metrics import MetricStatus
from equitydesk.infrastructure.sources.xbrl_facts import parse_company_facts

FIXTURE = (
    Path(__file__).resolve().parents[1] / "fixtures" / "sec" / "companyfacts_CIK0000320193.json"
)
M = 1_000_000
FY23 = date(2023, 9, 30)


@pytest.fixture(scope="module")
def statements() -> dict[date, StatementPeriod]:
    raw = parse_company_facts(json.loads(FIXTURE.read_text()), "co_apple")
    return annual_statements(map_reported_facts("co_apple", raw, f.dt(2024, 6, 1)).facts)


@pytest.fixture(scope="module")
def fy23(statements):
    return {r.name: r for r in compute_kpis("co_apple", statements) if r.period_end == FY23}


EXPECTED_FY23 = {
    "revenue_growth": 383285 / 394328 - 1,  # -2.80%
    "eps_diluted_growth": 6.13 / 6.11 - 1,
    "gross_margin": 169148 / 383285,  # 44.13%
    "operating_margin": 114301 / 383285,
    "net_margin": 96995 / 383285,
    "ebitda": (114301 + 11519) * M,
    "free_cash_flow": (110543 - 10959) * M,
    "fcf_margin": (110543 - 10959) / 383285,
    "fcf_growth": (110543 - 10959) / (122151 - 10708) - 1,
    "effective_tax_rate": 16741 / 113736,
    "total_debt": (95281 + 9822 + 5985) * M,
    "net_debt": (95281 + 9822 + 5985 - 29965 - 31590) * M,
    "net_debt_to_ebitda": (111088 - 61555) / 125820,
    "debt_to_equity": 111088 / 62146,
    "current_ratio": 143566 / 145308,
    "quick_ratio": (143566 - 6331) / 145308,
    "roe": 96995 / ((62146 + 50672) / 2),
    "roa": 96995 / ((352583 + 352755) / 2),
    "roic": 114301 * (1 - 16741 / 113736) / ((111679 + 122437) / 2),
    "diluted_share_change": 15812547000 / 16325819000 - 1,
    "shares_outstanding_change": 15550061000 / 15943425000 - 1,
    "days_sales_outstanding": 29508 / 383285 * 365,
    "days_inventory_outstanding": 6331 / 214137 * 365,
    "days_payables_outstanding": 62611 / 214137 * 365,
    "cash_conversion_cycle": (29508 / 383285 + 6331 / 214137 - 62611 / 214137) * 365,
    "cash_conversion": 110543 / 96995,
}


@pytest.mark.parametrize(("name", "expected"), sorted(EXPECTED_FY23.items()))
def test_fy2023_matches_hand_calculation(fy23, name: str, expected: float) -> None:
    result = fy23[name]
    assert result.status is MetricStatus.OK, (result.missing, result.note)
    assert result.value == pytest.approx(expected, rel=1e-9)


def test_rounded_sanity_values(fy23) -> None:
    assert round(fy23["gross_margin"].value, 4) == 0.4413
    assert round(fy23["revenue_growth"].value, 4) == -0.0280
    assert round(fy23["net_debt"].value / M) == 49533


def test_missing_input_is_explicit(fy23) -> None:
    ic = fy23["interest_coverage"]
    assert ic.status is MetricStatus.MISSING_INPUT and ic.value is None
    assert ic.missing == ("interest_expense",)


def test_first_year_has_no_prior_for_growth(statements) -> None:
    first = min(statements)
    results = {r.name: r for r in compute_kpis("co_apple", statements) if r.period_end == first}
    assert results["revenue_growth"].status is MetricStatus.MISSING_INPUT
    assert results["revenue_growth"].missing == ("revenue_prior",)
    assert results["gross_margin"].status is MetricStatus.OK


def test_results_carry_formula_inputs_and_fact_provenance(fy23) -> None:
    gm = fy23["gross_margin"]
    assert gm.formula == "GrossProfit / Revenue"
    assert gm.inputs == {"gross_profit": 169148 * M, "revenue": 383285 * M}
    assert len(gm.input_fact_ids) == 2 and all(i.startswith("fct_") for i in gm.input_fact_ids)
    assert gm.metric_id == "m:co_apple:gross_margin:2023-09-30"
    assert gm.calc_version


def _period(**values: float) -> StatementPeriod:
    facts = {
        concept: f.fact(fact_id=f"f_{concept}", concept=concept, value=v)
        for concept, v in values.items()
    }
    return StatementPeriod(date(2022, 1, 1), date(2022, 12, 31), facts)


def test_division_by_zero_is_undefined_not_inf() -> None:
    gm = next(m for m in METRICS if m.name == "gross_margin")
    r = evaluate(gm, "c", _period(GrossProfit=10.0, Revenue=0.0), None)
    assert r.status is MetricStatus.UNDEFINED and r.value is None and "zero" in (r.note or "")


def test_growth_from_negative_base_is_undefined() -> None:
    m = next(m for m in METRICS if m.name == "revenue_growth")
    r = evaluate(m, "c", _period(Revenue=10.0), _period(Revenue=-5.0))
    assert r.status is MetricStatus.UNDEFINED


def test_optional_components_are_listed_when_absent() -> None:
    m = next(m for m in METRICS if m.name == "total_debt")
    r = evaluate(m, "c", _period(LongTermDebt=100.0), None)
    assert r.value == 100.0 and r.missing == ("current_debt", "commercial_paper")
    assert r.note and "not reported" in r.note


def test_custom_metric_definition() -> None:
    metric = MetricDef("x", "ratio", "A / B", (In("a", "Revenue"), In("b", "NetIncome")),
                       lambda v: div(v["a"], v["b"]))  # fmt: skip
    assert evaluate(metric, "c", _period(Revenue=10.0, NetIncome=4.0), None).value == 2.5


def test_cagr(statements) -> None:
    assert cagr(100.0, 121.0, 2) == pytest.approx(0.1)
    r = revenue_cagr("co_apple", statements)
    years = r.inputs["years"]
    assert r.status is MetricStatus.OK and years == 2.0
    assert r.value == pytest.approx((383285 / 365817) ** 0.5 - 1)
    assert revenue_cagr("c", {}).status is MetricStatus.MISSING_INPUT
