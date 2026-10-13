from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

import factories as f
from equitydesk.domain.financials import FactOrigin, PeriodType
from equitydesk.domain.research import (
    ClaimType,
    InvalidTransitionError,
    ResearchStatus,
    SupportStatus,
)


def test_naive_timestamps_are_rejected() -> None:
    with pytest.raises(ValidationError):
        f.run(as_of=datetime(2024, 1, 1))  # noqa: DTZ001
    with pytest.raises(ValidationError):
        f.document(published_at=datetime(2024, 1, 1))  # noqa: DTZ001


def test_aware_timestamps_normalized_to_utc() -> None:
    cet = timezone(timedelta(hours=2))
    r = f.run(as_of=datetime(2024, 6, 1, 14, 0, tzinfo=cet))
    assert r.as_of == f.dt(2024, 6, 1, 12)
    assert r.as_of.utcoffset() == timedelta(0)


def test_run_as_of_cannot_be_in_future_of_creation() -> None:
    with pytest.raises(ValidationError, match="as_of"):
        f.run(as_of=f.T0 + timedelta(days=1))


def test_models_are_immutable() -> None:
    r = f.run()
    with pytest.raises(ValidationError):
        r.status = ResearchStatus.COMPLETED  # type: ignore[misc]


def test_security_normalizes_codes_and_validates_isin() -> None:
    s = f.security()
    assert (s.ticker, s.exchange, s.currency) == ("AAPL", "XNAS", "USD")
    with pytest.raises(ValidationError):
        f.security(isin="BAD")


def test_lifecycle_happy_path_and_history() -> None:
    r = f.run()
    for status in [
        ResearchStatus.COLLECTING,
        ResearchStatus.PARSING,
        ResearchStatus.ANALYZING,
        ResearchStatus.VALUING,
        ResearchStatus.REVIEWING,
        ResearchStatus.COMPLETED,
    ]:
        r = r.transition(status, at=f.T0)
    assert r.status is ResearchStatus.COMPLETED
    assert [c.status for c in r.status_history][-1] is ResearchStatus.COMPLETED
    assert r.is_terminal


@pytest.mark.parametrize(
    ("start_path", "target"),
    [
        ([], ResearchStatus.COMPLETED),
        ([ResearchStatus.COLLECTING], ResearchStatus.VALUING),
    ],
)
def test_illegal_transitions_raise(
    start_path: list[ResearchStatus], target: ResearchStatus
) -> None:
    r = f.run()
    for s in start_path:
        r = r.transition(s)
    with pytest.raises(InvalidTransitionError):
        r.transition(target)


def test_terminal_runs_cannot_fail_again_but_any_active_can_fail() -> None:
    r = f.run().transition(ResearchStatus.COLLECTING).transition(ResearchStatus.FAILED, "boom")
    assert r.status_history[-1].reason == "boom"
    with pytest.raises(InvalidTransitionError):
        r.transition(ResearchStatus.FAILED)


def test_supported_fact_requires_evidence() -> None:
    with pytest.raises(ValidationError, match="evidence"):
        f.finding(evidence=())
    # an uncertain finding without evidence is allowed (explicitly unsupported)
    u = f.finding(evidence=(), support_status=SupportStatus.UNCERTAIN, confidence=0.2)
    assert u.support_status is SupportStatus.UNCERTAIN


def test_unknown_claim_cannot_be_supported() -> None:
    with pytest.raises(ValidationError, match="unknown"):
        f.finding(claim_type=ClaimType.UNKNOWN)


def test_fact_period_rules() -> None:
    with pytest.raises(ValidationError):
        f.fact(period_start=date(2024, 1, 1), period_end=date(2023, 1, 1))
    with pytest.raises(ValidationError):
        f.fact(period_type=PeriodType.INSTANT)  # instant with a start date
    inst = f.fact(period_type=PeriodType.INSTANT, period_start=None, concept="TotalAssets")
    assert inst.period_start is None


def test_fact_provenance_required() -> None:
    with pytest.raises(ValidationError, match="source"):
        f.fact(source_accession=None)
    with pytest.raises(ValidationError, match="derived_from"):
        f.fact(origin=FactOrigin.DERIVED)
    derived = f.fact(origin=FactOrigin.DERIVED, derived_from=("a", "b"))
    assert derived.derived_from == ("a", "b")


def test_valuation_requires_traceable_inputs() -> None:
    with pytest.raises(ValidationError, match="traceable"):
        f.valuation(inputs={})
