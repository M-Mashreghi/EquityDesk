from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

import factories as f
from equitydesk.analytics.accounting import StatementPeriod, annual_statements, map_reported_facts
from equitydesk.analytics.diagnostics import margin_deltas_bps, run_diagnostics, trend
from equitydesk.analytics.kpis import compute_kpis
from equitydesk.domain.metrics import MetricStatus
from equitydesk.infrastructure.sources.xbrl_facts import parse_company_facts

FIXTURE = (
    Path(__file__).resolve().parents[1] / "fixtures" / "sec" / "companyfacts_CIK0000320193.json"
)
FY23, FY22 = date(2023, 9, 30), date(2022, 9, 24)


@pytest.fixture(scope="module")
def statements():
    raw = parse_company_facts(json.loads(FIXTURE.read_text()), "co_apple")
    return annual_statements(map_reported_facts("co_apple", raw, f.dt(2024, 6, 1)).facts)


@pytest.fixture(scope="module")
def kpis(statements):
    return compute_kpis("co_apple", statements)


def test_gross_margin_trend(kpis) -> None:
    t = trend(kpis, "gross_margin")
    assert t.direction == "up"
    assert [p[0] for p in t.points] == [date(2021, 9, 25), FY22, FY23]
    assert t.change == pytest.approx((169148 / 383285 - 152836 / 365817) * 10_000)
    assert t.change_unit == "bps" and t.consecutive_increases == 2 and t.consecutive_declines == 0


def test_revenue_growth_trend_and_insufficient_data(kpis) -> None:
    t = trend(kpis, "revenue_growth")  # only FY22 and FY23 have a prior year
    assert t.direction == "down" and t.consecutive_declines == 1
    assert trend(kpis, "interest_coverage").direction == "insufficient_data"


def test_margin_deltas_in_bps(kpis) -> None:
    deltas = dict(margin_deltas_bps(kpis)["operating_margin"])
    assert deltas[FY23] == pytest.approx((114301 / 383285 - 119437 / 394328) * 10_000)
    assert round(deltas[FY23], 1) == -46.7


def test_fy2023_diagnostics_expose_formula_and_components(statements) -> None:
    diags = {d.name: d for d in run_diagnostics("co_apple", statements) if d.period_end == FY23}
    inv = diags["inventory_outpacing_revenue"]
    assert inv.triggered is True
    assert inv.value == pytest.approx((6331 / 4946 - 1) - (383285 / 394328 - 1))
    assert inv.components["inventory_prior"] == 4946e6
    assert inv.formula == "growth(Inventory) - growth(Revenue)"
    assert inv.threshold == 0.10 and inv.comparison == ">"

    cc = diags["low_cash_conversion"]
    assert cc.value == pytest.approx(110543 / 96995) and cc.triggered is False
    acc = diags["high_accruals"]
    assert acc.value == pytest.approx((96995 - 110543) / ((352583 + 352755) / 2))
    assert acc.triggered is False
    assert diags["receivables_outpacing_revenue"].value == pytest.approx(
        (29508 / 28184 - 1) - (383285 / 394328 - 1)
    )
    assert diags["share_dilution"].triggered is False
    assert diags["high_sbc_share_of_ocf"].value == pytest.approx(10833 / 110543)
    assert diags["operating_margin_compression"].triggered is False
    assert diags["negative_fcf_with_profit"].triggered is False


def test_first_year_diagnostics_needing_prior_are_missing(statements) -> None:
    first = min(statements)
    diags = {d.name: d for d in run_diagnostics("co_apple", statements) if d.period_end == first}
    assert diags["high_accruals"].status is MetricStatus.MISSING_INPUT
    assert diags["high_accruals"].triggered is None
    assert diags["low_cash_conversion"].status is MetricStatus.OK


def _period(end: date, **values: float) -> StatementPeriod:
    facts = {
        c: f.fact(
            fact_id=f"{c}{end}",
            concept=c,
            value=v,
            period_end=end,
            period_start=date(end.year, 1, 1),
        )
        for c, v in values.items()
    }
    return StatementPeriod(date(end.year, 1, 1), end, facts)


def test_red_flags_trigger_on_constructed_case() -> None:
    p0 = _period(date(2022, 12, 31), Revenue=100.0, AccountsReceivable=10.0, OperatingIncome=20.0,
                 TotalAssets=200.0, WeightedSharesDiluted=100.0)  # fmt: skip
    p1 = _period(date(2023, 12, 31), Revenue=105.0, AccountsReceivable=14.0, OperatingIncome=15.0,
                 NetIncome=40.0, OperatingCashFlow=10.0, CapitalExpenditure=25.0, TotalAssets=220.0,
                 WeightedSharesDiluted=105.0, StockBasedCompensation=4.0)  # fmt: skip
    diags = {d.name: d for d in run_diagnostics("x", {p0.period_end: p0, p1.period_end: p1})
             if d.period_end == p1.period_end}  # fmt: skip
    for name in (
        "low_cash_conversion",
        "high_accruals",
        "receivables_outpacing_revenue",
        "negative_fcf_with_profit",
        "share_dilution",
        "high_sbc_share_of_ocf",
        "operating_margin_compression",
    ):
        assert diags[name].triggered is True, name
    assert diags["high_accruals"].value == pytest.approx((40 - 10) / 210)


def test_loss_making_company_cash_conversion_is_undefined() -> None:
    p = _period(date(2023, 12, 31), NetIncome=-5.0, OperatingCashFlow=3.0)
    diags = {d.name: d for d in run_diagnostics("x", {p.period_end: p})}
    assert diags["low_cash_conversion"].status is MetricStatus.UNDEFINED
    assert diags["low_cash_conversion"].triggered is None
