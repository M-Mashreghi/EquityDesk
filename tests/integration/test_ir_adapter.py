from __future__ import annotations

from pathlib import Path

import httpx
import pytest

import factories as f
from equitydesk.domain.documents import SourceType
from equitydesk.domain.provenance import verify_content
from equitydesk.infrastructure.sources.http import HttpFetcher
from equitydesk.infrastructure.sources.investor_relations import (
    IssuerIRAdapter,
    ManualSourceRegistration,
    UnapprovedSourceError,
    host_is_approved,
    load_ir_configs,
)

pytestmark = pytest.mark.integration

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "ir" / "apple-q4-fy2023-release.html"
APPLE_URL = "https://www.apple.com/newsroom/2023/11/apple-reports-fourth-quarter-results/"
PUBLISHED = f.dt(2023, 11, 2, 20, 30)


def transport(redirect_to: str | None = None) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if redirect_to and str(request.url) == APPLE_URL:
            return httpx.Response(302, headers={"Location": redirect_to})
        return httpx.Response(
            200, content=FIXTURE.read_bytes(), headers={"Content-Type": "text/html; charset=utf-8"}
        )

    return httpx.MockTransport(handler)


def adapter(allow_unapproved: bool = False, redirect_to: str | None = None) -> IssuerIRAdapter:
    fetcher = HttpFetcher(
        "EquityDesk tests research@example.com",
        max_requests_per_second=1000,
        transport=transport(redirect_to),
    )
    return IssuerIRAdapter(
        fetcher,
        load_ir_configs(),
        clock=lambda: f.dt(2026, 10, 13),
        allow_unapproved=allow_unapproved,
    )


def test_real_issuer_pattern_registers_earnings_release() -> None:
    doc, content = adapter().register(
        ManualSourceRegistration(company_id="co_apple", url=APPLE_URL, published_at=PUBLISHED)
    )
    assert doc.source_type is SourceType.ISSUER_IR
    assert doc.document_type == "earnings_release"
    assert doc.published_at == PUBLISHED
    assert doc.source_url == APPLE_URL
    assert doc.mime_type == "text/html"
    verify_content(doc, content)


def test_document_id_is_stable_for_same_url_and_content() -> None:
    reg = ManualSourceRegistration(company_id="co_apple", url=APPLE_URL, published_at=PUBLISHED)
    assert adapter().register(reg)[0].document_id == adapter().register(reg)[0].document_id


@pytest.mark.parametrize(
    "url",
    [
        "https://www.apple-investor-news.com/newsroom/2023/11/apple-reports-results/",
        "http://www.apple.com/newsroom/2023/11/apple-reports-fourth-quarter-results/",
        "https://user:pw@www.apple.com/newsroom/2023/11/apple-reports-x-results/",
        "https://www.apple.com:8443/newsroom/2023/11/apple-reports-x-results/",
        "https://www.microsoft.com/en-us/investor/earnings/fy-2024-q4/press-release-webcast",
    ],
)
def test_unapproved_domains_rejected_by_default(url: str) -> None:
    with pytest.raises(UnapprovedSourceError):
        adapter().register(
            ManualSourceRegistration(
                company_id="co_apple", url=url, published_at=PUBLISHED, document_type="x"
            )
        )


def test_unapproved_source_downgraded_to_secondary_when_explicitly_allowed() -> None:
    doc, _ = adapter(allow_unapproved=True).register(
        ManualSourceRegistration(
            company_id="co_apple",
            url="https://news.example.com/apple-results",
            published_at=PUBLISHED,
            document_type="news_article",
        )
    )
    assert doc.source_type is SourceType.SECONDARY
    assert not doc.is_primary


def test_redirect_off_official_domain_is_rejected() -> None:
    with pytest.raises(UnapprovedSourceError, match="redirected"):
        adapter(redirect_to="https://evil.example.com/fake").register(
            ManualSourceRegistration(company_id="co_apple", url=APPLE_URL, published_at=PUBLISHED)
        )


def test_unknown_url_on_approved_domain_requires_document_type() -> None:
    url = "https://investor.apple.com/sec-filings/default.aspx"
    with pytest.raises(ValueError, match="document_type"):
        adapter().register(
            ManualSourceRegistration(company_id="co_apple", url=url, published_at=PUBLISHED)
        )
    doc, _ = adapter().register(
        ManualSourceRegistration(
            company_id="co_apple", url=url, published_at=PUBLISHED, document_type="presentation"
        )
    )
    assert doc.source_type is SourceType.ISSUER_IR


def test_publication_before_url_date_is_rejected() -> None:
    with pytest.raises(ValueError, match="precedes"):
        adapter().register(
            ManualSourceRegistration(
                company_id="co_apple", url=APPLE_URL, published_at=f.dt(2023, 10, 1)
            )
        )


def test_naive_publication_time_rejected() -> None:
    from datetime import datetime

    with pytest.raises(ValueError):
        ManualSourceRegistration(
            company_id="co_apple",
            url=APPLE_URL,
            published_at=datetime(2023, 11, 2),  # noqa: DTZ001
        )


def test_host_matching() -> None:
    assert host_is_approved("www.apple.com", ("apple.com",))
    assert host_is_approved("apple.com", ("apple.com",))
    assert not host_is_approved("notapple.com", ("apple.com",))
    assert not host_is_approved("apple.com.evil.io", ("apple.com",))
