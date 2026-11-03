"""Multiples valuation against a hand-worked peer table."""

from __future__ import annotations

from datetime import date

import pytest

import factories as f
from equitydesk.analytics.multiples import CompanyFundamentals, multiples_valuation
from equitydesk.domain.market import MarketObservation
from equitydesk.domain.provenance import FutureLeakageError
from equitydesk.domain.valuation import ValuationMethod

AS_OF = f.dt(2024, 3, 1, 22)
END = date(2023, 12, 31)


def obs(
    sec: str, price: float, day: int = 1, month: int = 3, shares: float | None = 100.0
) -> MarketObservation:
    return MarketObservation(
        security_id=sec, price=price, currency="USD", observed_at=f.dt(2024, month, day, 21),
        price_source="manual:analyst", shares_outstanding=shares,
        shares_source="10-K cover" if shares else None, shares_as_of=date(2024, 1, 1) if shares else None,
    )  # fmt: skip


def fund(cid: str, rev, ebitda, ni, fcf, debt, cash, shares=None) -> CompanyFundamentals:
    return CompanyFundamentals(cid, f"sec_{cid}", END, rev, ebitda, ni, fcf, debt, cash, shares,
                               sources={"revenue": f"m:{cid}:revenue"})  # fmt: skip


TARGET = fund("t", 2000.0, 500.0, 250.0, 300.0, 1000.0, 500.0, shares=100.0)
PEERS = [
    fund("a", 1000.0, 200.0, 100.0, 80.0, 200.0, 200.0),
    fund("b", 1000.0, 250.0, 150.0, 120.0, 600.0, 100.0),
    fund("c", 1000.0, -50.0, 40.0, 10.0, 0.0, 0.0),
]
OBS = [obs("sec_t", 50.0), obs("sec_a", 20.0), obs("sec_b", 30.0), obs("sec_c", 10.0),
       obs("sec_a", 99.0, day=15)]  # later price must not be used  # fmt: skip


@pytest.fixture(scope="module")
def result():
    return multiples_valuation(TARGET, PEERS, OBS, AS_OF, "semis:v1")


def test_peer_table_hand_worked(result) -> None:
    rows = {r.company_id: r for r in result.peers}
    assert rows["a"].observation.price == 20.0  # point-in-time price, not the later 99
    assert (rows["b"].market_cap, rows["b"].enterprise_value) == (3000.0, 3500.0)
    assert rows["b"].multiples == {
        "ev_sales": 3.5,
        "ev_ebitda": 14.0,
        "pe": 20.0,
        "fcf_yield": 0.04,
    }
    assert rows["c"].multiples["ev_ebitda"] is None
    assert any("non-positive ebitda" in n for n in rows["c"].notes)
    assert result.target.enterprise_value == 5500.0


def test_stats_and_implied_ranges(result) -> None:
    ev_s = result.stats["ev_sales"]
    assert (ev_s.p25, ev_s.median, ev_s.p75) == (1.5, 2.0, 2.75)
    implied = {r.multiple: r for r in result.implied}
    assert (implied["ev_sales"].low, implied["ev_sales"].mid, implied["ev_sales"].high) == (
        25.0,
        35.0,
        50.0,
    )
    assert result.stats["ev_ebitda"].n == 2 and result.stats["ev_ebitda"].excluded_missing == ("c",)
    assert (implied["ev_ebitda"].low, implied["ev_ebitda"].mid, implied["ev_ebitda"].high) == (
        50.0,
        55.0,
        60.0,
    )
    assert (implied["pe"].mid, implied["pe"].high) == (50.0, 56.25)


def test_missing_data_is_not_imputed() -> None:
    target = fund("t", 2000.0, None, 250.0, 300.0, 1000.0, 500.0, shares=100.0)
    peers = [fund("a", 1000.0, 200.0, 100.0, 80.0, None, 200.0), PEERS[1]]
    result = multiples_valuation(target, peers, OBS, AS_OF, "semis:v1")
    assert "ev_sales" not in {r.multiple for r in result.implied}  # only 1 peer has EV
    assert any("ev_ebitda: target ebitda missing" in n for n in result.notes)
    assert any("ev_sales: only 1 peer" in n for n in result.notes)


def test_future_only_prices_raise() -> None:
    with pytest.raises(FutureLeakageError):
        multiples_valuation(TARGET, PEERS, [obs("sec_t", 50.0, month=4)], AS_OF, "x")


def test_stale_and_misaligned_observations_noted() -> None:
    observations = [
        obs("sec_t", 50.0),
        obs("sec_a", 20.0, month=1),
        obs("sec_b", 30.0),
        obs("sec_c", 10.0),
    ]
    result = multiples_valuation(TARGET, PEERS, observations, AS_OF, "x")
    assert any("a: price observation is" in n for n in result.notes)
    assert any("a: price observed" in n for n in result.notes)


def test_valuation_run_record_is_traceable(result) -> None:
    run = result.to_valuation_run("run_1", f.T0)
    assert run.method is ValuationMethod.MULTIPLES and run.valuation_date == AS_OF
    assert run.inputs["peer_set_id"] == "semis:v1"
    assert run.inputs["peers"][0]["observed_at"].startswith("2024-03-01")
    assert run.outputs["ev_sales"]["mid"] == 35.0 and run.outputs["ev_sales"]["peers_used"] == [
        "a",
        "b",
        "c",
    ]
    assert run.valuation_id == result.to_valuation_run("run_1", f.T0).valuation_id
