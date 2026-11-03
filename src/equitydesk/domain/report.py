"""Cited research report schema (`report.v1`).

A report is a list of sections made of claims. Every claim is labeled fact / inference /
assumption / unknown and links to what supports it: citations into snapshot documents,
deterministic metric ids, or persisted valuation ids.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field, model_validator

from equitydesk.domain.common import DomainModel, UtcDateTime
from equitydesk.domain.documents import SourceType
from equitydesk.domain.research import ClaimType, SupportStatus

REPORT_SCHEMA_VERSION = "report.v1"


class SectionKey(StrEnum):
    COMPANY_OVERVIEW = "company_overview"
    FINANCIAL_ANALYSIS = "financial_analysis"
    GUIDANCE_AND_STRATEGY = "guidance_and_strategy"
    VALUATION = "valuation"
    INVESTMENT_THESIS = "investment_thesis"
    RISKS = "risks"
    CATALYSTS = "catalysts"
    SCENARIOS = "scenarios"
    OPEN_QUESTIONS = "open_questions"


SECTION_ORDER: tuple[SectionKey, ...] = tuple(SectionKey)
SECTION_TITLES: dict[SectionKey, str] = {
    SectionKey.COMPANY_OVERVIEW: "Company Overview",
    SectionKey.FINANCIAL_ANALYSIS: "Financial Analysis",
    SectionKey.GUIDANCE_AND_STRATEGY: "Guidance, Strategy & Management Tone",
    SectionKey.VALUATION: "Valuation",
    SectionKey.INVESTMENT_THESIS: "Investment Thesis",
    SectionKey.RISKS: "Risks",
    SectionKey.CATALYSTS: "Catalysts",
    SectionKey.SCENARIOS: "Bull / Base / Bear Scenarios",
    SectionKey.OPEN_QUESTIONS: "Open Questions",
}


class Citation(DomainModel):
    citation_id: str = Field(min_length=1)
    document_id: str = Field(min_length=1)
    source_type: SourceType
    document_type: str
    title: str = ""
    source_url: str | None = None
    official_id: str | None = None
    published_at: UtcDateTime
    retrieved_at: UtcDateTime
    content_hash: str | None = None
    chunk_id: str = Field(min_length=1)
    section: str | None = None
    page: int | None = None
    quote: str = Field(min_length=1)


class ReportClaim(DomainModel):
    claim_id: str = Field(min_length=1)
    section: SectionKey
    text: str = Field(min_length=1)
    claim_type: ClaimType
    support_status: SupportStatus
    citation_ids: tuple[str, ...] = ()
    metric_ids: tuple[str, ...] = ()
    valuation_ids: tuple[str, ...] = ()
    finding_ids: tuple[str, ...] = ()
    assumption_source: str | None = None
    values: dict[str, float | None] = Field(
        default_factory=dict, description="numbers quoted in `text`, as computed by code"
    )

    @property
    def has_support(self) -> bool:
        return bool(self.citation_ids or self.metric_ids or self.valuation_ids)

    @model_validator(mode="after")
    def _provenance(self) -> ReportClaim:
        if self.support_status is SupportStatus.SUPPORTED:
            if self.claim_type in (ClaimType.FACT, ClaimType.INFERENCE) and not self.has_support:
                raise ValueError(
                    "a supported fact/inference claim needs a citation, metric or valuation id"
                )
            if self.claim_type is ClaimType.UNKNOWN:
                raise ValueError("an unknown claim cannot be supported")
        if self.claim_type is ClaimType.ASSUMPTION and not self.assumption_source:
            raise ValueError("an assumption claim must name its assumption source")
        return self


class ReportSection(DomainModel):
    key: SectionKey
    title: str
    claims: tuple[ReportClaim, ...] = ()
    notes: tuple[str, ...] = ()


class ResearchReport(DomainModel):
    report_id: str = Field(min_length=1)
    research_run_id: str = Field(min_length=1)
    company_id: str = Field(min_length=1)
    company_name: str
    as_of: UtcDateTime
    snapshot_id: str | None = None
    schema_version: str = REPORT_SCHEMA_VERSION
    composer_version: str
    sections: tuple[ReportSection, ...]
    citations: tuple[Citation, ...] = ()
    generated_at: UtcDateTime

    @model_validator(mode="after")
    def _integrity(self) -> ResearchReport:
        if tuple(s.key for s in self.sections) != SECTION_ORDER:
            raise ValueError("report sections must follow the stable report.v1 order")
        known = {c.citation_id for c in self.citations}
        for c in self.claims:
            missing = set(c.citation_ids) - known
            if missing:
                raise ValueError(f"claim {c.claim_id} cites unknown citations {sorted(missing)}")
        for cit in self.citations:
            if cit.published_at > self.as_of:
                raise ValueError(f"citation {cit.citation_id} was published after as_of")
        return self

    @property
    def claims(self) -> tuple[ReportClaim, ...]:
        return tuple(c for s in self.sections for c in s.claims)

    def section(self, key: SectionKey) -> ReportSection:
        return next(s for s in self.sections if s.key is key)
