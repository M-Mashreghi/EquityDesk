from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

import factories as f
from equitydesk.analytics.accounting import (
    CONCEPTS,
    IssueCode,
    annual_statements,
    classify_duration,
    map_reported_facts,
)
from equitydesk.domain.financials import FactOrigin, PeriodType, RawReportedFact
from equitydesk.infrastructure.sources.xbrl_facts import parse_company_facts

FIXTURE = (
    Path(__file__).resolve().parents[1] / "fixtures" / "sec" / "companyfacts_CIK0000320193.json"
)
M = 1_000_000


@pytest.fixture(scope="module")
def raw() -> list[RawReportedFact]:
    return parse_company_facts(json.loads(FIXTURE.read_text()), "co_apple")


def _by(facts, concept, end, unit="USD"):
    hits = [x for x in facts if x.concept == concept and x.period_end == end and x.unit == unit]
    assert len(hits) == 1, (concept, end, hits)
    return hits[0]


def test_annual_income_statement_maps_with_provenance(raw) -> None:
    result = map_reported_facts("co_apple", raw, f.dt(2024, 6, 1))
    rev = _by(result.facts, "Revenue", date(2023, 9, 30))
    assert rev.value == 383_285 * M
    assert rev.period_start == date(2022, 9, 25) and rev.fiscal_period == "FY"
    assert rev.fiscal_year == 2023
    assert rev.source_accession == "0000320193-23-000106"
    assert rev.source_locator == "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax"
    assert rev.origin is FactOrigin.REPORTED
    eps = _by(result.facts, "EPSDiluted", date(2023, 9, 30), "USD/shares")
    assert eps.value == 6.13
    shares = _by(result.facts, "WeightedSharesDiluted", date(2023, 9, 30), "shares")
    assert shares.value == 15_812_547_000


def test_balance_sheet_instants(raw) -> None:
    result = map_reported_facts("co_apple", raw, f.dt(2024, 6, 1))
    eq = _by(result.facts, "StockholdersEquity", date(2022, 9, 24))
    assert eq.period_type is PeriodType.INSTANT and eq.period_start is None
    assert eq.value == 50_672 * M
    # comparative re-reported in the FY2023 10-K with same value -> latest filing is the source
    assert eq.source_accession == "0000320193-23-000106"


def test_quarterly_kept_separate_and_ytd_skipped(raw) -> None:
    result = map_reported_facts("co_apple", raw, f.dt(2024, 6, 1))
    q = _by(result.facts, "Revenue", date(2023, 12, 30))
    assert q.value == 119_575 * M and q.fiscal_period is None
    # the 9-month YTD net income is not mapped as any period
    assert not [
        x for x in result.facts if x.concept == "NetIncome" and x.period_end == date(2023, 7, 1)
    ]
    assert result.skipped >= 1


def test_point_in_time_excludes_later_filings(raw) -> None:
    result = map_reported_facts("co_apple", raw, f.dt(2023, 6, 1))
    ends = {x.period_end for x in result.facts if x.concept == "Revenue"}
    assert date(2023, 9, 30) not in ends and date(2022, 9, 24) in ends
    eq = _by(result.facts, "StockholdersEquity", date(2022, 9, 24))
    assert eq.source_accession == "0000320193-22-000108"  # FY23 10-K not yet public


def test_lower_priority_tag_conflict_is_reported_not_merged(raw) -> None:
    result = map_reported_facts("co_apple", raw, f.dt(2024, 6, 1))
    assert _by(result.facts, "Revenue", date(2021, 9, 25)).value == 365_817 * M
    conflicts = [i for i in result.issues if i.code is IssueCode.TAG_CONFLICT]
    assert conflicts and "us-gaap:Revenues" in conflicts[0].detail


def _raw(value: float, accn: str, filed: date, unit: str = "USD", tag: str = "NetIncomeLoss", **kw):
    data = dict(
        company_id="co_x",
        taxonomy="us-gaap",
        tag=tag,
        unit=unit,
        value=value,
        period_start=date(2022, 1, 1),
        period_end=date(2022, 12, 31),
        accession=accn,
        form="10-K",
        filed=filed,
        available_at=f.dt(filed.year, filed.month, filed.day, 22),
    )
    data.update(kw)
    return RawReportedFact(**data)


def test_restatement_applies_only_once_public() -> None:
    original = _raw(100.0, "a1", date(2023, 2, 1))
    restated = _raw(90.0, "a2", date(2024, 2, 1))
    before = map_reported_facts("co_x", [original, restated], f.dt(2023, 6, 1))
    assert [x.value for x in before.facts] == [100.0]
    after = map_reported_facts("co_x", [original, restated], f.dt(2024, 6, 1))
    assert [x.value for x in after.facts] == [90.0]
    assert after.issues[0].code is IssueCode.RESTATED


def test_incompatible_units_are_not_merged() -> None:
    usd = _raw(100.0, "a1", date(2023, 2, 1))
    eur = _raw(92.0, "a1", date(2023, 2, 1), unit="EUR")
    shares = _raw(5.0, "a1", date(2023, 2, 1), unit="shares")
    result = map_reported_facts("co_x", [usd, eur, shares], f.dt(2024, 1, 1))
    assert sorted(x.unit for x in result.facts) == ["EUR", "USD"]
    codes = {i.code for i in result.issues}
    assert {IssueCode.MULTIPLE_UNITS, IssueCode.UNIT_MISMATCH} <= codes
    statements = annual_statements(result.facts, currency="USD")
    assert statements[date(2022, 12, 31)].value("NetIncome") == 100.0


def test_gross_profit_derived_only_when_missing() -> None:
    rev = _raw(1000.0, "a1", date(2023, 2, 1), tag="Revenues")
    cogs = _raw(600.0, "a1", date(2023, 2, 1), tag="CostOfRevenue")
    result = map_reported_facts("co_x", [rev, cogs], f.dt(2024, 1, 1))
    gp = [x for x in result.facts if x.concept == "GrossProfit"]
    assert len(gp) == 1 and gp[0].value == 400.0 and gp[0].origin is FactOrigin.DERIVED
    assert len(gp[0].derived_from) == 2


def test_missing_start_on_duration_is_an_issue() -> None:
    bad = _raw(1.0, "a1", date(2023, 2, 1), period_start=None)
    result = map_reported_facts("co_x", [bad], f.dt(2024, 1, 1))
    assert not result.facts and result.issues[0].code is IssueCode.MISSING_START


def test_annual_statement_view(raw) -> None:
    result = map_reported_facts("co_apple", raw, f.dt(2024, 6, 1))
    st = annual_statements(result.facts)
    assert list(st) == [
        date(2020, 9, 26),
        date(2021, 9, 25),
        date(2022, 9, 24),
        date(2023, 9, 30),
    ] or list(st) == [date(2021, 9, 25), date(2022, 9, 24), date(2023, 9, 30)]
    fy23 = st[date(2023, 9, 30)]
    assert fy23.value("OperatingIncome") == 114_301 * M
    assert fy23.value("TotalAssets") == 352_583 * M
    assert fy23.value("SharesOutstanding") == 15_550_061_000


def test_duration_classification() -> None:
    assert classify_duration(date(2022, 9, 25), date(2023, 9, 30)) == "FY"
    assert classify_duration(date(2023, 10, 1), date(2023, 12, 30)) == "Q"
    assert classify_duration(date(2022, 9, 25), date(2023, 7, 1)) is None


def test_every_concept_has_tags() -> None:
    assert all(spec.tags for spec in CONCEPTS.values())
    assert map_reported_facts("co_apple", [], f.dt(2024, 1, 1)).facts == []
