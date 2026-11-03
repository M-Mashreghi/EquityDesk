"""DCF engine against a hand-worked two-year fixture.

Base revenue 1,000; growth 10%/10%; EBIT margin 20%; tax 25%; D&A 5% and capex 5% of revenue;
NWC 10% of incremental revenue; WACC 10%; terminal growth 2%; debt 300; cash 100; 100 shares.

Year 1: revenue 1,100, EBIT 220, NOPAT 165, D&A 55, capex 55, dNWC 10 -> FCFF 155; PV 140.9091
Year 2: revenue 1,210, EBIT 242, NOPAT 181.5, D&A 60.5, capex 60.5, dNWC 11 -> FCFF 170.5;
        PV 170.5 / 1.21 = 140.9091
TV = 170.5 * 1.02 / 0.08 = 2,173.875; PV = 1,796.5909
EV = 2,078.4091; equity = EV - 300 + 100 = 1,878.4091; per share 18.784091
Exit multiple 8x on year-2 EBITDA (242 + 60.5 = 302.5): TV 2,420, PV 2,000, EV 2,281.8182.
"""

from __future__ import annotations

import json
from datetime import date

import pytest
from pydantic import ValidationError

import factories as f
from equitydesk.analytics.dcf import DcfBase, assumptions_hash, dcf_with_sensitivity, run_dcf
from equitydesk.domain.valuation import DcfAssumptions, TerminalMethod, ValuationMethod

BASE = DcfBase("co_x", date(2023, 12, 31), 1000.0, 300.0, 100.0, 100.0,
               sources={"base_revenue": "m:co_x:revenue:2023-12-31"})  # fmt: skip


def assumptions(**kw) -> DcfAssumptions:
    data = dict(
        assumption_set_id="co_x:base", version=1, source="user:analyst",
        revenue_growth=(0.10, 0.10), ebit_margin=(0.20, 0.20), tax_rate=0.25,
        da_pct_revenue=0.05, capex_pct_revenue=0.05, nwc_pct_incremental_revenue=0.10,
        discount_rate=0.10, terminal_growth=0.02,
        rationale={"discount_rate": "CAPM WACC estimate"},
    )  # fmt: skip
    data.update(kw)
    return DcfAssumptions(**data)


def test_hand_worked_gordon_dcf() -> None:
    r = run_dcf(BASE, assumptions())
    y1, y2 = r.years
    assert (y1.revenue, y1.ebit, y1.nopat, y1.delta_nwc, y1.fcff) == pytest.approx(
        (1100.0, 220.0, 165.0, 10.0, 155.0)
    )
    assert (y2.revenue, y2.fcff, y2.ebitda) == pytest.approx((1210.0, 170.5, 302.5))
    assert y1.present_value == pytest.approx(140.909091, abs=1e-6)
    assert y2.present_value == pytest.approx(140.909091, abs=1e-6)
    assert r.terminal_value.value == pytest.approx(2173.875)
    assert r.pv_terminal_value == pytest.approx(1796.590909, abs=1e-6)
    assert r.enterprise_value == pytest.approx(2078.409091, abs=1e-6)
    assert r.equity_value.value == pytest.approx(1878.409091, abs=1e-6)
    assert r.value_per_share.value == pytest.approx(18.784091, abs=1e-6)
    assert r.terminal_share_of_ev == pytest.approx(1796.590909 / 2078.409091)


def test_hand_worked_exit_multiple() -> None:
    a = assumptions(terminal_method=TerminalMethod.EXIT_MULTIPLE, terminal_growth=None,
                    exit_ev_ebitda=8.0)  # fmt: skip
    r = run_dcf(BASE, a)
    assert r.terminal_value.value == pytest.approx(2420.0)
    assert r.pv_terminal_value == pytest.approx(2000.0)
    assert r.enterprise_value == pytest.approx(2281.818182, abs=1e-6)


def test_mid_year_discounts_cash_flows_half_a_year_earlier() -> None:
    end, mid = run_dcf(BASE, assumptions()), run_dcf(BASE, assumptions(mid_year=True))
    assert mid.years[0].present_value == pytest.approx(155.0 / 1.1**0.5)
    assert mid.pv_terminal_value == pytest.approx(end.pv_terminal_value)  # TV still at year n
    assert mid.enterprise_value > end.enterprise_value


@pytest.mark.parametrize(
    "kw",
    [
        {"terminal_growth": 0.10},  # g >= r
        {"terminal_growth": None},  # gordon without g
        {"terminal_method": TerminalMethod.EXIT_MULTIPLE, "exit_ev_ebitda": None},
        {"ebit_margin": (0.2,)},  # horizon mismatch
        {"source": "llm:opus"},  # a model cannot author assumptions
        {"revenue_growth": (-1.5, 0.1)},
    ],
)
def test_invalid_assumptions_rejected(kw) -> None:
    with pytest.raises(ValidationError):
        assumptions(**kw)


def test_sensitivity_grid_with_undefined_cells() -> None:
    r = dcf_with_sensitivity(BASE, assumptions(), (0.09, 0.10, 0.11), (0.01, 0.02, 0.10))
    s = r.sensitivity
    assert s is not None and s.value(0.10, 0.02) == pytest.approx(r.value_per_share.value)
    assert s.value(0.09, 0.10) is None and s.value(0.10, 0.10) is None  # r <= g
    assert s.value(0.09, 0.02) > s.value(0.11, 0.02)  # lower WACC -> higher value
    assert s.value(0.10, 0.01) < s.value(0.10, 0.02)


def test_assumptions_are_serializable_and_versioned() -> None:
    a = assumptions()
    restored = DcfAssumptions.model_validate_json(a.model_dump_json())
    assert restored == a and assumptions_hash(restored) == assumptions_hash(a)
    assert assumptions_hash(assumptions(version=2)) != assumptions_hash(a)

    r = dcf_with_sensitivity(BASE, a, (0.10,), (0.02,))
    run = r.to_valuation_run("run_1", f.dt(2024, 3, 1), f.T0)
    assert run.method is ValuationMethod.DCF
    assert run.assumptions["assumptions_hash"] == assumptions_hash(a)
    assert run.assumptions["discount_rate"] == 0.10 and run.inputs["sources"]
    assert run.outputs["value_per_share"] == pytest.approx(18.784091, abs=1e-6)
    json.dumps(run.model_dump(mode="json"))  # fully serializable
    assert run.valuation_id == r.to_valuation_run("run_1", f.dt(2024, 3, 1), f.T0).valuation_id
