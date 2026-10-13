"""Valuation primitives against hand-worked examples."""

from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError

import factories as f
from equitydesk.analytics.kpis import Undefined
from equitydesk.analytics.valuation import (
    apply_multiple,
    build_market_bridge,
    discount_factor,
    enterprise_value,
    equity_from_enterprise_value,
    exit_multiple_terminal_value,
    gordon_terminal_value,
    market_cap,
    per_share,
    present_value,
    sensitivity_table,
    valuation_multiple,
)
from equitydesk.domain.market import MarketObservation, observation_as_of
from equitydesk.domain.provenance import FutureLeakageError
from equitydesk.domain.valuation import Assumption


def obs(day: int, price: float, **kw) -> MarketObservation:
    data = dict(
        security_id="sec_aapl_xnas",
        price=price,
        currency="USD",
        observed_at=f.dt(2024, 1, day, 21),
        price_source="manual:analyst",
        shares_outstanding=15_000.0,
        shares_source="10-Q 0000320193-24-000006 cover page",
        shares_as_of=date(2024, 1, 2),
    )
    data.update(kw)
    return MarketObservation(**data)


def test_market_cap_and_ev_bridge_hand_worked() -> None:
    # price 100, 1,000 shares, debt 20,000, cash 5,000, preferred 1,000, minority 500
    mcap = market_cap(100.0, 1_000.0)
    assert mcap.value == 100_000.0 and mcap.formula == "price * shares"
    ev = enterprise_value(mcap.value, 20_000.0, 5_000.0, 1_000.0, 500.0)
    assert ev.value == 116_500.0
    eq = equity_from_enterprise_value(ev.value, 20_000.0, 5_000.0, 1_000.0, 500.0)
    assert eq.value == 100_000.0  # bridge round-trips
    assert per_share(eq.value, 1_000.0).value == 100.0


def test_multiples() -> None:
    assert valuation_multiple("EV/EBITDA", 116_500.0, 11_650.0).value == pytest.approx(10.0)
    assert apply_multiple("implied_ev", 12.0, 11_650.0).value == 139_800.0
    with pytest.raises(Undefined):
        valuation_multiple("P/E", 100.0, -2.0)
    with pytest.raises(Undefined):
        apply_multiple("x", 0.0, 10.0)
    with pytest.raises(Undefined):
        per_share(10.0, 0.0)


def test_discounting_end_and_mid_year() -> None:
    assert discount_factor(0.10, 1) == pytest.approx(1 / 1.1)
    assert discount_factor(0.10, 1, mid_year=True) == pytest.approx(1 / 1.1**0.5)
    pv = present_value([100.0, 110.0, 121.0], 0.10)
    assert pv.value == pytest.approx(100 / 1.1 + 110 / 1.21 + 121 / 1.331)
    assert pv.value == pytest.approx(272.7272727, rel=1e-9)  # = 3 * 100/1.1
    assert pv.inputs["cf_3"] == 121.0 and pv.inputs["rate"] == 0.10


def test_terminal_values() -> None:
    tv = gordon_terminal_value(100.0, 0.09, 0.02)
    assert tv.value == pytest.approx(100 * 1.02 / 0.07)  # 1457.142857
    with pytest.raises(Undefined, match="rate > terminal growth"):
        gordon_terminal_value(100.0, 0.03, 0.03)
    assert exit_multiple_terminal_value(50.0, 12.0).value == 600.0


def test_sensitivity_table_marks_undefined_cells() -> None:
    table = sensitivity_table(
        lambda r, g: gordon_terminal_value(100.0, r, g).value,
        "rate", [0.08, 0.10], "growth", [0.02, 0.09],
    )  # fmt: skip
    assert table.value(0.08, 0.02) == pytest.approx(102 / 0.06)
    assert table.value(0.10, 0.02) == pytest.approx(102 / 0.08)
    assert table.value(0.08, 0.09) is None  # r <= g
    assert table.value(0.10, 0.09) == pytest.approx(109 / 0.01)


def test_market_observation_point_in_time() -> None:
    observations = [obs(2, 185.0), obs(10, 186.0), obs(31, 184.0)]
    assert observation_as_of(observations, "sec_aapl_xnas", f.dt(2024, 1, 15)).price == 186.0
    with pytest.raises(FutureLeakageError):
        observation_as_of([obs(31, 184.0)], "sec_aapl_xnas", f.dt(2024, 1, 15))
    with pytest.raises(LookupError):
        observation_as_of(observations, "sec_other", f.dt(2024, 1, 15))


def test_observation_contract_requires_share_provenance() -> None:
    with pytest.raises(ValidationError, match="shares_source"):
        obs(2, 185.0, shares_source=None)
    with pytest.raises(ValidationError, match="after the price"):
        obs(2, 185.0, shares_as_of=date(2024, 3, 1))
    with pytest.raises(ValidationError):
        obs(2, 0.0)


def test_market_bridge_pins_observation_and_inputs() -> None:
    assumption = Assumption(name="minority_interest", value=0.0, unit="USD",
                            rationale="none reported", source="user:analyst")  # fmt: skip
    bridge = build_market_bridge(
        [obs(2, 185.0), obs(31, 190.0)], "sec_aapl_xnas", f.dt(2024, 1, 15),
        total_debt=1_000.0, cash_and_investments=600.0, balance_sheet_date="2023-12-30",
        assumptions=[assumption],
    )  # fmt: skip
    assert bridge.observation.observed_at == f.dt(2024, 1, 2, 21)
    assert bridge.market_cap.value == 185.0 * 15_000.0
    assert bridge.enterprise_value.value == 185.0 * 15_000.0 + 1_000.0 - 600.0
    assert bridge.enterprise_value.components[0].name == "market_cap"
    assert bridge.enterprise_value.as_dict()["inputs"]["total_debt"] == 1_000.0
    with pytest.raises(Undefined, match="share count"):
        build_market_bridge(
            [obs(2, 185.0, shares_outstanding=None, shares_source=None, shares_as_of=None)],
            "sec_aapl_xnas", f.dt(2024, 1, 15), 0.0, 0.0, "2023-12-30",
        )  # fmt: skip


def test_assumptions_must_be_explicit_and_not_model_generated() -> None:
    with pytest.raises(ValidationError, match="never a model"):
        Assumption(name="wacc", value=0.09, unit="ratio", rationale="x", source="llm:guess")
    with pytest.raises(ValidationError):
        Assumption(name="wacc", value=0.09, unit="ratio", rationale="", source="user:a")
