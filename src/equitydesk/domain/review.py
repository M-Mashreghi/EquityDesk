"""Independent report review: findings with severity, and the review verdict."""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field

from equitydesk.domain.common import DomainModel, UtcDateTime


class ReviewGateError(ValueError):
    """Raised when a run would be completed without a passing review."""


class ReviewSeverity(StrEnum):
    BLOCKING = "blocking"  # prevents the research run from reaching Completed
    WARNING = "warning"  # shown with the report; needs analyst attention
    INFO = "info"


class ReviewFinding(DomainModel):
    review_finding_id: str = Field(min_length=1)
    code: str = Field(min_length=1)
    severity: ReviewSeverity
    message: str = Field(min_length=1)
    claim_ids: tuple[str, ...] = ()
    citation_ids: tuple[str, ...] = ()
    section: str | None = None
    origin: str = Field(default="deterministic", description="deterministic | llm:<prompt@version>")


class ReviewResult(DomainModel):
    review_id: str = Field(min_length=1)
    research_run_id: str = Field(min_length=1)
    report_id: str = Field(min_length=1)
    reviewer_version: str = Field(min_length=1)
    findings: tuple[ReviewFinding, ...] = ()
    reviewed_at: UtcDateTime
    model_version: str | None = None

    @property
    def blocking(self) -> tuple[ReviewFinding, ...]:
        return tuple(f for f in self.findings if f.severity is ReviewSeverity.BLOCKING)

    @property
    def passed(self) -> bool:
        return not self.blocking
