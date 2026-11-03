"""Workflow source ports implemented on the SEC EDGAR adapter."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime

from equitydesk.application.workflow import Collected
from equitydesk.domain.companies import Company
from equitydesk.domain.financials import RawReportedFact
from equitydesk.infrastructure.sources.http import SourceUnavailableError
from equitydesk.infrastructure.sources.sec_edgar import SecEdgarAdapter
from equitydesk.infrastructure.sources.xbrl_facts import parse_company_facts

RESEARCH_FORMS = ("10-K", "10-K/A", "10-Q", "10-Q/A")


class SecFilingSource:
    """Primary filings public at `as_of`; a document that cannot be fetched is reported."""

    def __init__(
        self, adapter: SecEdgarAdapter, forms: Iterable[str] = RESEARCH_FORMS, limit: int = 8
    ) -> None:
        self.adapter = adapter
        self.forms = tuple(forms)
        self.limit = limit

    def collect(self, company: Company, as_of: datetime) -> Collected:
        out = Collected()
        if not company.cik:
            out.failures.append(f"{company.company_id} has no CIK; SEC filings unavailable")
            return out
        try:
            refs = self.adapter.list_filings(company.cik, self.forms, as_of=as_of)
        except SourceUnavailableError as exc:
            out.failures.append(f"filing index unavailable: {exc}")
            return out
        for ref in refs[-self.limit :]:
            try:
                out.documents.append(self.adapter.fetch_filing(ref, company.company_id))
            except SourceUnavailableError as exc:
                out.failures.append(f"{ref.form} {ref.accession_number}: {exc}")
        return out


class SecReportedFacts:
    """XBRL company facts, made available at the filing's acceptance time (point-in-time)."""

    def __init__(self, adapter: SecEdgarAdapter) -> None:
        self.adapter = adapter

    def reported_facts(self, company: Company) -> list[RawReportedFact]:
        if not company.cik:
            return []
        accepted = {
            r.accession_number: r.accepted_at
            for r in self.adapter.list_filings(company.cik, forms=None)
        }
        return parse_company_facts(
            self.adapter.company_facts(company.cik), company.company_id, accepted
        )
