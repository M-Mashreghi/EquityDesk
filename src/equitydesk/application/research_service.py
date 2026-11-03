"""Read/write application service behind the API: company lookup, runs and their results.

The service owns no research logic of its own: it resolves identities, enforces point-in-time
reads (`as_of` of the run) and delegates computation to the deterministic engines.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from equitydesk.analytics.accounting import annual_statements
from equitydesk.analytics.kpis import compute_kpis
from equitydesk.application.history import RunDiff, RunState, diff_runs
from equitydesk.application.ports import (
    CompanyRepository,
    DocumentRepository,
    FinancialFactRepository,
    FindingRepository,
    NotFoundError,
    ReportRepository,
    ResearchRunRepository,
    SnapshotReader,
    ValuationRepository,
)
from equitydesk.application.resolver import ResolutionResult, ResolutionStatus, SecurityResolver
from equitydesk.domain.common import new_id, utc_now
from equitydesk.domain.companies import Company
from equitydesk.domain.documents import SourceDocument
from equitydesk.domain.financials import FinancialFact
from equitydesk.domain.metrics import MetricResult
from equitydesk.domain.report import ResearchReport
from equitydesk.domain.research import ResearchRun
from equitydesk.domain.review import ReviewResult
from equitydesk.domain.valuation import ValuationRun
from equitydesk.reporting.render import render_markdown


class ServiceError(Exception):
    """Structured application error; the API maps `kind` to an HTTP status."""

    kind = "invalid_request"

    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}


class InvalidRequest(ServiceError):
    kind = "invalid_request"


class NotFound(ServiceError):
    kind = "not_found"


class Ambiguous(ServiceError):
    kind = "ambiguous"


@dataclass(frozen=True)
class FinancialsView:
    company_id: str
    as_of: datetime
    facts: list[FinancialFact]
    metrics: list[MetricResult]


@dataclass(frozen=True)
class ReportView:
    report: ResearchReport
    review: ReviewResult | None
    markdown: str


@dataclass
class ResearchService:
    companies: CompanyRepository
    runs: ResearchRunRepository
    documents: DocumentRepository
    facts: FinancialFactRepository
    valuations: ValuationRepository
    reports: ReportRepository
    snapshots: SnapshotReader
    resolver: SecurityResolver
    findings: FindingRepository
    config_version: str = "1"
    clock: Callable[[], datetime] = field(default=utc_now)

    # --- companies -------------------------------------------------------------------------

    def resolve(self, query: str, exchange: str | None = None) -> ResolutionResult:
        return self.resolver.resolve(query, exchange=exchange)

    def get_company(self, company_id: str) -> Company:
        try:
            return self.companies.get_company(company_id)
        except NotFoundError as exc:
            raise NotFound(f"company {company_id} not found") from exc

    # --- runs ------------------------------------------------------------------------------

    def create_run(
        self, query: str, as_of: datetime | None = None, exchange: str | None = None
    ) -> ResearchRun:
        result = self.resolve(query, exchange)
        if result.status is not ResolutionStatus.RESOLVED or result.company is None:
            details = {
                "status": result.status.value,
                "candidates": [
                    {"company_id": c.company.company_id, "name": c.company.name,
                     "security_id": c.security.security_id if c.security else None}
                    for c in result.candidates
                ],
                "suggestions": list(result.suggestions),
            }  # fmt: skip
            if result.status is ResolutionStatus.AMBIGUOUS:
                raise Ambiguous(result.message or f"'{query}' is ambiguous", details)
            if result.status is ResolutionStatus.INVALID_INPUT:
                raise InvalidRequest(result.message or "invalid company query", details)
            raise NotFound(result.message or f"no company matches '{query}'", details)
        now = self.clock()
        as_of = as_of or now
        if as_of.tzinfo is None:
            raise InvalidRequest("as_of must include a timezone")
        if as_of > now:
            raise InvalidRequest("as_of cannot be in the future", {"as_of": as_of.isoformat()})
        self.companies.save_company(result.company)
        if result.security is not None:
            self.companies.save_security(result.security)
        run = ResearchRun(
            research_run_id=new_id("run"),
            company_id=result.company.company_id,
            security_id=result.security.security_id if result.security else None,
            as_of=as_of,
            created_at=now,
            config_version=self.config_version,
        )
        self.runs.add_run(run)
        return run

    def get_run(self, research_run_id: str) -> ResearchRun:
        try:
            return self.runs.get_run(research_run_id)
        except NotFoundError as exc:
            raise NotFound(f"research run {research_run_id} not found") from exc

    def list_runs(self, company_id: str) -> list[ResearchRun]:
        self.get_company(company_id)
        return self.runs.runs_for_company(company_id)

    def refresh_run(self, research_run_id: str, as_of: datetime | None = None) -> ResearchRun:
        """Create a new run for a later `as_of`; the previous run is never modified."""
        previous = self.get_run(research_run_id)
        now = self.clock()
        as_of = as_of or now
        if as_of.tzinfo is None:
            raise InvalidRequest("as_of must include a timezone")
        if as_of > now:
            raise InvalidRequest("as_of cannot be in the future", {"as_of": as_of.isoformat()})
        if as_of <= previous.as_of:
            raise InvalidRequest(
                "a refresh must use a later as_of than the run it refreshes",
                {"previous_as_of": previous.as_of.isoformat(), "as_of": as_of.isoformat()},
            )
        run = ResearchRun(
            research_run_id=new_id("run"),
            company_id=previous.company_id,
            security_id=previous.security_id,
            as_of=as_of,
            created_at=now,
            config_version=self.config_version,
            refreshed_from=previous.research_run_id,
        )
        self.runs.add_run(run)
        return run

    def start_run_for_company(self, company_id: str, as_of: datetime | None = None) -> ResearchRun:
        """New run for a known company: a refresh of its latest run if there is one."""
        company = self.get_company(company_id)
        previous = [r for r in self.runs.runs_for_company(company.company_id)]
        if previous:
            return self.refresh_run(previous[-1].research_run_id, as_of)
        securities = self.companies.securities_for_company(company.company_id)
        primary = next((x for x in securities if x.is_primary_listing), None)
        now = self.clock()
        as_of = as_of or now
        if as_of > now:
            raise InvalidRequest("as_of cannot be in the future", {"as_of": as_of.isoformat()})
        run = ResearchRun(
            research_run_id=new_id("run"),
            company_id=company.company_id,
            security_id=primary.security_id if primary else None,
            as_of=as_of,
            created_at=now,
            config_version=self.config_version,
        )
        self.runs.add_run(run)
        return run

    def run_state(self, research_run_id: str) -> RunState:
        run = self.get_run(research_run_id)
        reports = self.reports.reports_for_run(run.research_run_id)
        return RunState(
            run=run,
            documents=self.run_documents(run.research_run_id),
            metrics=self.run_financials(run.research_run_id).metrics,
            findings=self.findings.findings_for_run(run.research_run_id),
            valuations=self.valuations.valuations_for_run(run.research_run_id),
            report=reports[-1] if reports else None,
        )

    def diff(self, run_a: str, run_b: str) -> RunDiff:
        a, b = self.run_state(run_a), self.run_state(run_b)
        if a.run.company_id != b.run.company_id:
            raise InvalidRequest("runs belong to different companies")
        return diff_runs(a, b)

    # --- point-in-time results -------------------------------------------------------------

    def run_documents(self, research_run_id: str) -> list[SourceDocument]:
        run = self.get_run(research_run_id)
        snapshot = self.snapshots.snapshot_for_run(run.research_run_id)
        if snapshot is not None:
            return [self.documents.get_document(d) for d in snapshot.document_ids]
        return self.documents.documents_for_company(run.company_id, run.as_of)

    def run_financials(self, research_run_id: str) -> FinancialsView:
        run = self.get_run(research_run_id)
        facts = self.facts.facts_for_company(run.company_id, available_at_or_before=run.as_of)
        metrics = compute_kpis(run.company_id, annual_statements(facts)) if facts else []
        return FinancialsView(run.company_id, run.as_of, facts, metrics)

    def run_valuations(self, research_run_id: str) -> list[ValuationRun]:
        run = self.get_run(research_run_id)
        return self.valuations.valuations_for_run(run.research_run_id)

    def run_report(self, research_run_id: str) -> ReportView:
        run = self.get_run(research_run_id)
        reports = self.reports.reports_for_run(run.research_run_id)
        if not reports:
            raise NotFound(f"no report has been composed for run {research_run_id}")
        report = reports[-1]
        review = self.reports.latest_review(run.research_run_id)
        if review is not None and review.report_id != report.report_id:
            review = None
        return ReportView(report, review, render_markdown(report))
