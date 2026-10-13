"""Research runs, their lifecycle, and evidence-backed research findings."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import Field, model_validator

from equitydesk.domain.common import DomainError, DomainModel, UtcDateTime, utc_now


class ResearchStatus(StrEnum):
    CREATED = "Created"
    COLLECTING = "Collecting"
    PARSING = "Parsing"
    ANALYZING = "Analyzing"
    VALUING = "Valuing"
    REVIEWING = "Reviewing"
    COMPLETED = "Completed"
    NEEDS_INPUT = "NeedsInput"
    BLOCKED_BY_SOURCE = "BlockedBySource"
    FAILED = "Failed"


TERMINAL_STATUSES = frozenset({ResearchStatus.COMPLETED, ResearchStatus.FAILED})

# Mirrors docs/DIAGRAMS.md §3. Additionally any non-terminal state may move to Failed.
ALLOWED_TRANSITIONS: dict[ResearchStatus, frozenset[ResearchStatus]] = {
    ResearchStatus.CREATED: frozenset({ResearchStatus.COLLECTING}),
    ResearchStatus.COLLECTING: frozenset(
        {ResearchStatus.PARSING, ResearchStatus.BLOCKED_BY_SOURCE}
    ),
    ResearchStatus.PARSING: frozenset({ResearchStatus.ANALYZING}),
    ResearchStatus.ANALYZING: frozenset({ResearchStatus.VALUING, ResearchStatus.NEEDS_INPUT}),
    ResearchStatus.VALUING: frozenset({ResearchStatus.REVIEWING, ResearchStatus.NEEDS_INPUT}),
    ResearchStatus.REVIEWING: frozenset({ResearchStatus.COMPLETED, ResearchStatus.NEEDS_INPUT}),
    ResearchStatus.NEEDS_INPUT: frozenset({ResearchStatus.ANALYZING, ResearchStatus.VALUING}),
    ResearchStatus.BLOCKED_BY_SOURCE: frozenset({ResearchStatus.COLLECTING}),
    ResearchStatus.COMPLETED: frozenset(),
    ResearchStatus.FAILED: frozenset(),
}


class InvalidTransitionError(DomainError):
    pass


class StatusChange(DomainModel):
    status: ResearchStatus
    at: UtcDateTime
    reason: str | None = None


class ResearchRun(DomainModel):
    research_run_id: str = Field(min_length=1)
    company_id: str = Field(min_length=1)
    security_id: str | None = None
    as_of: UtcDateTime
    created_at: UtcDateTime
    status: ResearchStatus = ResearchStatus.CREATED
    status_history: tuple[StatusChange, ...] = ()
    config_version: str = Field(min_length=1)
    model_version: str | None = None
    prompt_versions: dict[str, str] = Field(default_factory=dict)
    snapshot_id: str | None = None

    @model_validator(mode="after")
    def _no_future_as_of(self) -> ResearchRun:
        if self.as_of > self.created_at:
            raise ValueError("as_of cannot be later than the run creation time")
        return self

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES

    def can_transition(self, target: ResearchStatus) -> bool:
        if target is ResearchStatus.FAILED:
            return not self.is_terminal
        return target in ALLOWED_TRANSITIONS[self.status]

    def transition(
        self, target: ResearchStatus, reason: str | None = None, at: datetime | None = None
    ) -> ResearchRun:
        """Return a copy in the target status; the original object is never mutated."""
        if not self.can_transition(target):
            raise InvalidTransitionError(f"{self.status} -> {target} is not allowed")
        change = StatusChange(status=target, at=at or utc_now(), reason=reason)
        return self.model_copy(
            update={"status": target, "status_history": (*self.status_history, change)}
        )


class FindingCategory(StrEnum):
    BUSINESS_MODEL = "business_model"
    SEGMENT = "segment"
    REVENUE_DRIVER = "revenue_driver"
    CUSTOMER = "customer"
    GEOGRAPHY = "geography"
    STRATEGY_CHANGE = "strategy_change"
    GUIDANCE_CHANGE = "guidance_change"
    MANAGEMENT_TONE = "management_tone"
    RISK = "risk"
    CATALYST = "catalyst"
    OPEN_QUESTION = "open_question"


class ClaimType(StrEnum):
    """Fact / inference / assumption / unknown separation (CLAUDE.md)."""

    FACT = "fact"
    INFERENCE = "inference"
    ASSUMPTION = "assumption"
    UNKNOWN = "unknown"


class SupportStatus(StrEnum):
    SUPPORTED = "supported"
    UNCERTAIN = "uncertain"
    NOT_SUPPORTED = "not_supported"


class EvidenceSpan(DomainModel):
    document_id: str = Field(min_length=1)
    chunk_id: str = Field(min_length=1, description="normalized-document locator")
    quote: str = Field(min_length=1)
    section: str | None = None
    page: int | None = Field(default=None, ge=1)


class ResearchFinding(DomainModel):
    finding_id: str = Field(min_length=1)
    research_run_id: str = Field(min_length=1)
    category: FindingCategory
    claim_type: ClaimType
    statement: str = Field(min_length=1)
    evidence: tuple[EvidenceSpan, ...] = ()
    confidence: float = Field(ge=0.0, le=1.0)
    support_status: SupportStatus
    attributes: dict[str, Any] = Field(default_factory=dict)
    model_version: str | None = None
    prompt_version: str | None = None
    schema_version: str | None = None
    created_at: UtcDateTime

    @model_validator(mode="after")
    def _evidence_rules(self) -> ResearchFinding:
        if self.support_status is SupportStatus.SUPPORTED:
            if self.claim_type in (ClaimType.FACT, ClaimType.INFERENCE) and not self.evidence:
                raise ValueError("a supported fact/inference requires at least one evidence span")
            if self.claim_type is ClaimType.UNKNOWN:
                raise ValueError("an unknown claim cannot be marked supported")
        return self
