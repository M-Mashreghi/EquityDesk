"""Bull / base / bear scenarios: labeling, consistency checks, weighting."""

from __future__ import annotations

from datetime import date

import pytest

import factories as f
from equitydesk.analytics.dcf import DcfBase, run_dcf
from equitydesk.analytics.scenarios import ValueKind, build_scenarios
from equitydesk.domain.market import MarketObservation
from equitydesk.domain.valuation import DcfAssumptions, ScenarioCase, ScenarioName, ValuationMethod

BASE = DcfBase("co_x", date(2023, 12, 31), 1000.0, 300.0, 100.0, 100.0,
               sources={"base_revenue": "m:co_x:revenue:2023-12-31", "total_debt": "m:co_x:total_debt",
                        "cash_and_investments": "f:cash", "diluted_shares": "f:shares"})  # fmt: skip
RATIONALE = {
    "revenue_growth": "segment build",
    "ebit_margin": "operating leverage",
    "discount_rate": "WACC",
    "terminal_growth": "nominal GDP",
}
PRICE = MarketObservation(security_id="sec_x", price=16.0, currency="USD",
                          observed_at=f.dt(2024, 3, 1, 21), price_source="manual:analyst")  # fmt: skip


def case(
    name: ScenarioName, growth: float, margin: float, p: float | None = None, **kw
) -> ScenarioCase:
    data = dict(
        assumption_set_id=f"co_x:{name.value}", version=1, source=f"scenario:{name.value}",
        revenue_growth=(growth, growth), ebit_margin=(margin, margin), tax_rate=0.25,
        da_pct_revenue=0.05, capex_pct_revenue=0.05, nwc_pct_incremental_revenue=0.10,
        discount_rate=0.10, terminal_growth=0.02, rationale=RATIONALE,
    )  # fmt: skip
    data.update(kw)
    return ScenarioCase(name=name, assumptions=DcfAssumptions(**data),
                        narrative=f"{name.value} narrative", probability=p)  # fmt: skip


def cases(p=(0.25, 0.5, 0.25)) -> list[ScenarioCase]:
    return [
        case(ScenarioName.BULL, 0.15, 0.25, p[0]),
        case(ScenarioName.BASE, 0.10, 0.20, p[1]),
        case(ScenarioName.BEAR, 0.00, 0.15, p[2]),
    ]


def test_consistent_set_values_and_weighting() -> None:
    a = build_scenarios(BASE, cases(), PRICE)
    assert a.is_consistent, a.issues
    base = a.outputs[ScenarioName.BASE]
    assert base.dcf.value_per_share.value == pytest.approx(18.784091, abs=1e-6)  # Task 5.2 fixture
    assert base.upside == pytest.approx(18.784091 / 16.0 - 1, abs=1e-6)
    v = {n: o.dcf.value_per_share.value for n, o in a.outputs.items()}
    assert v[ScenarioName.BULL] > v[ScenarioName.BASE] > v[ScenarioName.BEAR]
    expected = (
        0.25 * v[ScenarioName.BULL] + 0.5 * v[ScenarioName.BASE] + 0.25 * v[ScenarioName.BEAR]
    )
    assert a.probability_weighted_value == pytest.approx(expected)
    # operating outputs
    assert base.value("final_year_revenue").value == pytest.approx(1210.0)
    assert base.value("revenue_cagr").value == pytest.approx(0.10)
    assert a.outputs[ScenarioName.BEAR].value("final_year_revenue").value == pytest.approx(1000.0)


def test_every_value_is_labeled_fact_assumption_or_calculation() -> None:
    out = build_scenarios(BASE, cases()).outputs[ScenarioName.BULL]
    kinds = {v.name: v.kind for v in out.values}
    assert kinds["base_revenue"] is ValueKind.FACT
    assert out.value("base_revenue").provenance == "m:co_x:revenue:2023-12-31"
    assert kinds["revenue_growth_y1"] is ValueKind.ASSUMPTION
    assert out.value("discount_rate").provenance == "scenario:bull"
    assert kinds["value_per_share"] is ValueKind.CALCULATION
    # every assumption field the DCF consumes is surfaced (no hidden assumption)
    assumed = {n for n, k in kinds.items() if k is ValueKind.ASSUMPTION}
    assert {"tax_rate", "da_pct_revenue", "capex_pct_revenue", "nwc_pct_incremental_revenue",
            "discount_rate", "terminal_growth", "revenue_growth_y2", "ebit_margin_y2"} <= assumed  # fmt: skip


def codes(analysis) -> set[str]:
    return {i.code for i in analysis.issues}


def test_missing_case_and_bad_probabilities_block() -> None:
    a = build_scenarios(BASE, cases()[:2])
    assert not a.is_consistent and "scenario_set_incomplete" in codes(a)
    a = build_scenarios(BASE, cases(p=(0.3, 0.5, 0.3)))
    assert "probabilities_do_not_sum_to_one" in codes(a) and a.probability_weighted_value is None
    a = build_scenarios(BASE, cases(p=(0.5, 0.5, None)))
    assert "partial_probabilities" in codes(a)


def test_inverted_scenarios_block() -> None:
    c = cases()
    c[0], c[2] = (
        case(ScenarioName.BULL, 0.0, 0.15, 0.25),
        case(ScenarioName.BEAR, 0.15, 0.25, 0.25),
    )
    a = build_scenarios(BASE, c)
    assert {"value_ordering", "driver_ordering"} <= codes(a) and not a.is_consistent


def test_hidden_assumption_blocks() -> None:
    c = cases()
    c[1] = case(ScenarioName.BASE, 0.10, 0.20, 0.5, rationale={"revenue_growth": "x"})
    a = build_scenarios(BASE, c)
    undocumented = [i for i in a.issues if i.code == "undocumented_assumption"]
    assert {i.scenario for i in undocumented} == {"base"} and not a.is_consistent
    assert any("discount_rate" in i.message for i in undocumented)


def test_horizon_mismatch_and_warnings() -> None:
    c = cases()
    c[0] = case(ScenarioName.BULL, 0.15, 0.25, 0.25, revenue_growth=(0.15,), ebit_margin=(0.25,))
    assert "horizon_mismatch" in codes(build_scenarios(BASE, c))
    c = cases()
    c[0] = case(ScenarioName.BULL, 0.15, 0.25, 0.25, terminal_growth=0.06)
    a = build_scenarios(BASE, c)
    assert {"aggressive_terminal_growth", "terminal_value_dominates"} <= codes(a)
    assert a.is_consistent  # warnings only


def test_scenario_valuation_run_is_traceable() -> None:
    a = build_scenarios(BASE, cases(), PRICE)
    run = a.to_valuation_run("run_1", f.dt(2024, 3, 1, 22), f.T0)
    assert run.method is ValuationMethod.SCENARIO
    assert run.inputs["facts"]["base_revenue"]["source"] == "m:co_x:revenue:2023-12-31"
    assert run.inputs["price"]["value"] == 16.0
    assert (
        run.assumptions["bull"]["probability"] == 0.25
        and run.assumptions["bear"]["assumptions_hash"]
    )
    assert run.outputs["base"]["values"][0]["kind"] == "fact"
    assert run.outputs["probability_weighted_value"] == pytest.approx(a.probability_weighted_value)
    assert run_dcf(BASE, cases()[1].assumptions).value_per_share.value == pytest.approx(
        a.outputs[ScenarioName.BASE].dcf.value_per_share.value
    )
