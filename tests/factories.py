"""Hand-built domain object factories shared by tests."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from equitydesk.domain.companies import Company, Security
from equitydesk.domain.documents import SourceDocument, SourceType
from equitydesk.domain.financials import FinancialFact, PeriodType
from equitydesk.domain.research import (
    ClaimType,
    EvidenceSpan,
    FindingCategory,
    ResearchFinding,
    ResearchRun,
    SupportStatus,
)
from equitydesk.domain.valuation import ValuationMethod, ValuationRun

T0 = datetime(2024, 6, 30, 12, 0, tzinfo=UTC)
HASH = "a" * 64


def dt(y: int, m: int, d: int, h: int = 0, mi: int = 0) -> datetime:
    return datetime(y, m, d, h, mi, tzinfo=UTC)


def company(**kw: Any) -> Company:
    data: dict[str, Any] = {
        "company_id": "co_apple",
        "name": "Apple Inc.",
        "country": "us",
        "cik": "0000320193",
        "aliases": ("Apple",),
    }
    data.update(kw)
    return Company(**data)


def security(**kw: Any) -> Security:
    data: dict[str, Any] = {
        "security_id": "sec_aapl_xnas",
        "company_id": "co_apple",
        "ticker": "aapl",
        "exchange": "xnas",
        "currency": "usd",
        "isin": "US0378331005",
        "is_primary_listing": True,
    }
    data.update(kw)
    return Security(**data)


def run(**kw: Any) -> ResearchRun:
    data: dict[str, Any] = {
        "research_run_id": "run_1",
        "company_id": "co_apple",
        "security_id": "sec_aapl_xnas",
        "as_of": dt(2024, 6, 1),
        "created_at": T0,
        "config_version": "1",
    }
    data.update(kw)
    return ResearchRun(**data)


def document(**kw: Any) -> SourceDocument:
    data: dict[str, Any] = {
        "document_id": "doc_10k_2023",
        "company_id": "co_apple",
        "source_type": SourceType.REGULATORY_FILING,
        "document_type": "10-K",
        "title": "Apple 10-K FY2023",
        "source_url": "https://www.sec.gov/Archives/edgar/data/320193/000032019323000106/aapl-20230930.htm",
        "official_id": "0000320193-23-000106",
        "published_at": dt(2023, 11, 3, 10, 4),
        "retrieved_at": dt(2024, 6, 1, 9),
        "period_end": date(2023, 9, 30),
        "content_hash": HASH,
    }
    data.update(kw)
    return SourceDocument(**data)


def finding(**kw: Any) -> ResearchFinding:
    data: dict[str, Any] = {
        "finding_id": "fnd_1",
        "research_run_id": "run_1",
        "category": FindingCategory.RISK,
        "claim_type": ClaimType.FACT,
        "statement": "The company depends on outsourcing partners in Asia.",
        "evidence": (
            EvidenceSpan(
                document_id="doc_10k_2023",
                chunk_id="doc_10k_2023:s3:p1",
                quote="manufacturing is performed by outsourcing partners",
            ),
        ),
        "confidence": 0.9,
        "support_status": SupportStatus.SUPPORTED,
        "created_at": T0,
    }
    data.update(kw)
    return ResearchFinding(**data)


def fact(**kw: Any) -> FinancialFact:
    data: dict[str, Any] = {
        "fact_id": "fct_rev_fy23",
        "company_id": "co_apple",
        "concept": "Revenue",
        "value": 383_285_000_000.0,
        "unit": "USD",
        "period_type": PeriodType.DURATION,
        "period_start": date(2022, 9, 25),
        "period_end": date(2023, 9, 30),
        "fiscal_year": 2023,
        "fiscal_period": "FY",
        "source_accession": "0000320193-23-000106",
        "source_locator": "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax",
        "available_at": dt(2023, 11, 3, 10, 4),
    }
    data.update(kw)
    return FinancialFact(**data)


def valuation(**kw: Any) -> ValuationRun:
    data: dict[str, Any] = {
        "valuation_id": "val_1",
        "research_run_id": "run_1",
        "method": ValuationMethod.MULTIPLES,
        "valuation_date": dt(2024, 6, 1),
        "formula": "EV = multiple * EBITDA",
        "inputs": {"ebitda": 125.0},
        "assumptions": {"multiple": 20.0},
        "outputs": {"ev": 2500.0},
        "version": "1",
        "created_at": T0,
    }
    data.update(kw)
    return ValuationRun(**data)
