from __future__ import annotations

import pytest

from equitydesk.application.resolver import ResolutionStatus, SecurityResolver, normalize_name
from equitydesk.infrastructure.db.sqlite import open_database
from equitydesk.infrastructure.sources.security_master import JsonSecurityMaster


@pytest.fixture(scope="module")
def resolver() -> SecurityResolver:
    return SecurityResolver(JsonSecurityMaster())


@pytest.mark.parametrize(
    ("query", "security_id", "matched_on"),
    [
        ("AAPL", "sec_aapl_xnas", "ticker"),
        ("aapl", "sec_aapl_xnas", "ticker"),
        ("  MSFT ", "sec_msft_xnas", "ticker"),
        ("US0378331005", "sec_aapl_xnas", "isin"),
        ("320193", "sec_aapl_xnas", "cik"),
        ("CIK0000320193", "sec_aapl_xnas", "cik"),
        ("Apple Inc.", "sec_aapl_xnas", "name"),
        ("microsoft corp", "sec_msft_xnas", "name"),
        ("GOOG", "sec_goog_xnas", "ticker"),
        ("Alphabet", "sec_googl_xnas", "name+primary_listing"),
        ("ASML Holding N.V.", "sec_asml_xams", "name+primary_listing"),
    ],
)
def test_known_fixtures_resolve(
    resolver: SecurityResolver, query: str, security_id: str, matched_on: str
) -> None:
    r = resolver.resolve(query)
    assert r.status is ResolutionStatus.RESOLVED, r.message
    assert r.security is not None and r.security.security_id == security_id
    assert r.matched_on == matched_on


def test_primary_listing_choice_is_recorded(resolver: SecurityResolver) -> None:
    r = resolver.resolve("Alphabet")
    assert r.notes and "primary listing" in r.notes[0]


@pytest.mark.parametrize("ticker", ["ASML", "SHOP"])
def test_ticker_alone_is_not_globally_unique(resolver: SecurityResolver, ticker: str) -> None:
    r = resolver.resolve(ticker)
    assert r.status is ResolutionStatus.AMBIGUOUS
    assert len(r.candidates) == 2
    assert {c.security.exchange for c in r.candidates if c.security} >= {"XNAS"} or ticker == "SHOP"
    assert "exchange" in r.message


@pytest.mark.parametrize(
    ("query", "exchange", "expected"),
    [
        ("ASML", "XAMS", "sec_asml_xams"),
        ("ASML", "xnas", "sec_asml_xnas"),
        ("ASML:XNAS", None, "sec_asml_xnas"),
        ("XTSE:SHOP", None, "sec_shop_xtse"),
        ("ASML Holding", "XNAS", "sec_asml_xnas"),
    ],
)
def test_exchange_disambiguates(
    resolver: SecurityResolver, query: str, exchange: str | None, expected: str
) -> None:
    r = resolver.resolve(query, exchange=exchange)
    assert r.status is ResolutionStatus.RESOLVED
    assert r.security is not None and r.security.security_id == expected


def test_country_filter(resolver: SecurityResolver) -> None:
    r = resolver.resolve("SHOP", country="ca")
    # both SHOP listings belong to a Canadian issuer: still ambiguous
    assert r.status is ResolutionStatus.AMBIGUOUS


def test_partial_name_is_never_auto_resolved(resolver: SecurityResolver) -> None:
    r = resolver.resolve("apple")  # exact alias -> Apple Inc.
    assert r.status is ResolutionStatus.RESOLVED
    r = resolver.resolve("appl")  # partial -> Apple, Apple Hospitality, Applied Materials
    assert r.status is ResolutionStatus.AMBIGUOUS
    assert {c.company.company_id for c in r.candidates} == {"co_apple", "co_aple", "co_amat"}
    r = resolver.resolve("Apple Hosp")  # a single partial match still needs confirmation
    assert r.status is ResolutionStatus.AMBIGUOUS
    assert [c.company.company_id for c in r.candidates] == ["co_aple"]


def test_missing_input_is_structured(resolver: SecurityResolver) -> None:
    r = resolver.resolve("ZZZZQ")
    assert r.status is ResolutionStatus.NOT_FOUND
    assert r.company is None and r.security is None
    r = resolver.resolve("MSFTT")
    assert r.status is ResolutionStatus.NOT_FOUND and "MSFT" in r.suggestions


@pytest.mark.parametrize("bad", ["", "   ", "x" * 201])
def test_invalid_input(resolver: SecurityResolver, bad: str) -> None:
    assert resolver.resolve(bad).status is ResolutionStatus.INVALID_INPUT


def test_invalid_exchange(resolver: SecurityResolver) -> None:
    assert resolver.resolve("AAPL", exchange="NASDAQ").status is ResolutionStatus.INVALID_INPUT


def test_ticker_on_wrong_exchange_is_not_found_with_candidates(resolver: SecurityResolver) -> None:
    r = resolver.resolve("AAPL", exchange="XNYS")
    assert r.status is ResolutionStatus.NOT_FOUND
    assert [c.security.security_id for c in r.candidates if c.security] == ["sec_aapl_xnas"]


def test_normalize_name() -> None:
    assert normalize_name("ASML Holding N.V.") == "asml"
    assert normalize_name("Applied Materials, Inc.") == "applied materials"


def test_master_seeds_repository() -> None:
    repos = open_database()
    JsonSecurityMaster().seed(repos.companies)
    assert len(repos.companies.securities_for_company("co_asml")) == 2
