from __future__ import annotations

from datetime import UTC, date, datetime

import httpx
import pytest

import factories as f
from equitydesk.domain.documents import SourceType
from equitydesk.domain.provenance import verify_content
from equitydesk.infrastructure.sources.http import HttpFetcher, RateLimiter, SourceUnavailableError
from equitydesk.infrastructure.sources.sec_edgar import (
    SecEdgarAdapter,
    availability_time,
    normalize_cik,
)
from sec_mocks import sec_transport

pytestmark = pytest.mark.integration

UA = "EquityDesk tests research@example.com"


def make_adapter(
    transport: httpx.BaseTransport | None = None, sleeps: list[float] | None = None
) -> SecEdgarAdapter:
    fetcher = HttpFetcher(
        UA,
        max_requests_per_second=1000,
        transport=transport or sec_transport(),
        sleep=(sleeps.append if sleeps is not None else lambda _s: None),
        backoff_seconds=0.5,
    )
    return SecEdgarAdapter(fetcher, clock=lambda: f.dt(2026, 10, 13))


def test_enumerates_filings_by_type_sorted_by_availability() -> None:
    refs = make_adapter().list_filings(320193, forms={"10-K", "10-Q"})
    assert [r.form for r in refs] == ["10-K", "10-Q", "10-Q", "10-Q", "10-K", "10-Q"]
    assert all(a.accepted_at <= b.accepted_at for a, b in zip(refs, refs[1:], strict=False))
    assert refs[0].url == (
        "https://www.sec.gov/Archives/edgar/data/320193/000032019322000108/aapl-20220924.htm"
    )


def test_default_forms_exclude_insider_forms() -> None:
    forms = {r.form for r in make_adapter().list_filings("0000320193")}
    assert "4" not in forms and {"10-K", "10-Q", "8-K", "10-K/A"} <= forms


def test_as_of_filter_is_enforced() -> None:
    adapter = make_adapter()
    # FY2023 10-K: accepted 2023-11-02 18:08 New York, official filing date 2023-11-03,
    # so it is treated as public from 2023-11-03 00:00 New York (= 04:00 UTC).
    before = adapter.list_filings(
        320193, forms={"10-K"}, as_of=datetime(2023, 11, 3, 3, 59, 59, tzinfo=UTC)
    )
    assert [r.accession_number for r in before] == ["0000320193-22-000108"]
    at = adapter.list_filings(320193, forms={"10-K"}, as_of=datetime(2023, 11, 3, 4, 0, tzinfo=UTC))
    assert [r.accession_number for r in at][-1] == "0000320193-23-000106"
    with pytest.raises(ValueError):
        adapter.list_filings(320193, as_of=datetime(2024, 1, 1))  # noqa: DTZ001


def test_amendment_flags() -> None:
    refs = make_adapter().list_filings(320193, forms={"10-K/A"})
    assert refs[0].is_amendment and refs[0].base_form == "10-K"


def test_history_pages_are_followed() -> None:
    refs = make_adapter().list_filings(320193, forms={"10-K"}, include_history=True)
    assert refs[0].filing_date == date(2009, 10, 27)


def test_fetch_filing_builds_provenance() -> None:
    adapter = make_adapter()
    ref = next(
        r
        for r in adapter.list_filings(320193, forms={"10-K"})
        if r.report_date == date(2023, 9, 30)
    )
    doc, content = adapter.fetch_filing(ref, "co_apple")
    assert doc.document_id == "sec_0000320193-23-000106"
    assert doc.source_type is SourceType.REGULATORY_FILING
    assert doc.official_id == ref.accession_number and doc.source_url == ref.url
    assert doc.published_at == datetime(2023, 11, 3, 4, 0, tzinfo=UTC)
    assert doc.retrieved_at == f.dt(2026, 10, 13)
    assert doc.period_end == date(2023, 9, 30)
    assert doc.mime_type == "text/html"
    verify_content(doc, content)


def test_user_agent_is_sent_and_required() -> None:
    seen: list[httpx.Request] = []
    make_adapter(sec_transport(seen=seen)).list_filings(320193)
    assert seen[0].headers["User-Agent"] == UA
    with pytest.raises(ValueError):
        HttpFetcher("  ")


def test_retries_on_429_then_succeeds_respecting_retry_after() -> None:
    calls = {"n": 0}
    base = sec_transport()

    def flaky(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "2"})
        return base.handle_request(request)

    url = "https://data.sec.gov/submissions/CIK0000320193.json"
    sleeps: list[float] = []
    refs = make_adapter(sec_transport({url: flaky}), sleeps).list_filings(320193)
    assert refs and calls["n"] == 2
    assert 2.0 in sleeps


def test_gives_up_after_bounded_retries_with_backoff() -> None:
    url = "https://data.sec.gov/submissions/CIK0000320193.json"
    sleeps: list[float] = []
    adapter = make_adapter(sec_transport({url: lambda r: httpx.Response(503)}), sleeps)
    with pytest.raises(SourceUnavailableError) as exc:
        adapter.list_filings(320193)
    assert exc.value.status == 503
    assert [s for s in sleeps if s >= 0.1] == [0.5, 1.0, 2.0]


def test_hard_errors_are_not_retried() -> None:
    adapter = make_adapter()
    with pytest.raises(SourceUnavailableError) as exc:
        adapter.list_filings(1234567)  # fixture missing -> 404
    assert exc.value.status == 404
    assert adapter.fetcher.request_count == 1


def test_rate_limiter_spaces_requests() -> None:
    now = [0.0]
    sleeps: list[float] = []

    def sleep(s: float) -> None:
        sleeps.append(s)
        now[0] += s

    limiter = RateLimiter(5, clock=lambda: now[0], sleep=sleep)
    for _ in range(3):
        limiter.wait()
    assert sleeps == pytest.approx([0.2, 0.2])


def test_availability_time_is_conservative() -> None:
    # interpreted as New York wall-clock (EDT, UTC-4)
    assert availability_time("2023-11-02T16:30:51.000Z", date(2023, 11, 2)) == datetime(
        2023, 11, 2, 20, 30, 51, tzinfo=UTC
    )
    # never before the start of the official filing date
    assert availability_time("2023-11-01T09:00:00.000Z", date(2023, 11, 3)) == datetime(
        2023, 11, 3, 4, 0, tzinfo=UTC
    )
    # missing acceptance -> end of filing day
    assert availability_time(None, date(2024, 1, 5)) == datetime(2024, 1, 6, 4, 59, 59, tzinfo=UTC)


@pytest.mark.parametrize(("raw", "norm"), [(320193, "0000320193"), ("CIK0000320193", "0000320193")])
def test_normalize_cik(raw: object, norm: str) -> None:
    assert normalize_cik(raw) == norm  # type: ignore[arg-type]


def test_normalize_cik_rejects_garbage() -> None:
    with pytest.raises(ValueError):
        normalize_cik("abc")
