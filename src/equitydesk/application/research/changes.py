"""Guidance, strategy and management-language change analyzer (prior vs. current document).

A change is only accepted with grounded before/after evidence from the right document:
`before` quotes must come from the prior document and `after` quotes from the current one.
Guidance must be quoted from forward-looking language; ambiguous changes are uncertain.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from equitydesk.application.llm import PromptTemplate, StructuredFailure, StructuredLLMClient
from equitydesk.application.research.grounding import (
    INJECTION_GUARD,
    EvidenceRef,
    ExtractionContext,
    SourceChunk,
    finding_id,
    ground,
    numbers_in,
    render_chunks,
)
from equitydesk.domain.research import ClaimType, FindingCategory, ResearchFinding, SupportStatus

SCHEMA_VERSION = "change_analysis.v1"

PROMPT = PromptTemplate(
    prompt_id="guidance_strategy_tone_changes",
    version="1.0.0",
    system=(
        "You compare two official documents from the same company and identify changes in "
        "management guidance, strategy and tone. " + INJECTION_GUARD + " Guidance means explicit "
        "forward-looking statements by management; never treat analyst estimates or your own "
        "expectations as guidance. If a change is ambiguous, use direction 'unclear'."
    ),
    user_template=(
        "Company: $company\n\nPRIOR document ($prior_id, published $prior_date):\n$prior\n\n"
        "CURRENT document ($current_id, published $current_date):\n$current\n\n"
        "List changes. `before` cites PRIOR chunks only, `after` cites CURRENT chunks only."
    ),
)

ChangeType = Literal["guidance", "strategy", "tone"]
Direction = Literal[
    "raised", "lowered", "maintained", "introduced", "withdrawn", "shifted", "unclear"
]

CATEGORY = {
    "guidance": FindingCategory.GUIDANCE_CHANGE,
    "strategy": FindingCategory.STRATEGY_CHANGE,
    "tone": FindingCategory.MANAGEMENT_TONE,
}
_FORWARD_LOOKING = re.compile(
    r"\b(expect|expects|expected|guidance|outlook|anticipate|anticipates|forecast|project|"
    r"projects|target|targets|plan|plans|will|intend|intends|estimate)\b",
    re.IGNORECASE,
)


class Side(BaseModel):
    model_config = ConfigDict(extra="forbid")

    statement: str
    evidence: list[EvidenceRef]


class Change(BaseModel):
    model_config = ConfigDict(extra="forbid")

    change_type: ChangeType
    topic: str
    direction: Direction
    before: Side | None
    after: Side | None
    summary: str
    confidence: float = Field(ge=0.0, le=1.0)


class ChangeAnalysis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    changes: list[Change]
    not_found: list[str]


@dataclass
class ChangeOutcome:
    findings: list[ResearchFinding]
    rejected: list[str] = field(default_factory=list)
    failure: StructuredFailure | None = None


@dataclass(frozen=True)
class DocumentChunks:
    document_id: str
    published_at: datetime
    chunks: Sequence[SourceChunk]


class ChangeAnalyzer:
    def __init__(self, llm: StructuredLLMClient, confidence_floor: float = 0.5) -> None:
        self.llm = llm
        self.confidence_floor = confidence_floor

    def analyze(
        self,
        research_run_id: str,
        company_name: str,
        prior: DocumentChunks,
        current: DocumentChunks,
        created_at: datetime,
    ) -> ChangeOutcome:
        if prior.published_at >= current.published_at:
            raise ValueError("prior document must be published before the current document")
        prior_text, prior_used = render_chunks(prior.chunks, 30_000)
        current_text, current_used = render_chunks(current.chunks, 30_000)
        result = self.llm.generate(
            PROMPT,
            {
                "company": company_name,
                "prior_id": prior.document_id,
                "prior_date": prior.published_at.date().isoformat(),
                "prior": prior_text,
                "current_id": current.document_id,
                "current_date": current.published_at.date().isoformat(),
                "current": current_text,
            },
            ChangeAnalysis,
            SCHEMA_VERSION,
        )
        if result.value is None:
            return ChangeOutcome([], failure=result.failure)
        ctx = ExtractionContext(
            research_run_id,
            created_at,
            result.model,
            PROMPT.ref,
            SCHEMA_VERSION,
            self.confidence_floor,
        )
        prior_index = {sc.chunk.chunk_id: sc for sc in prior_used}
        current_index = {sc.chunk.chunk_id: sc for sc in current_used}

        findings: dict[str, ResearchFinding] = {}
        rejected: list[str] = []
        for change in result.value.changes:
            finding, problems = self._to_finding(
                change, ctx, prior_index, current_index, prior, current
            )
            if finding is None:
                rejected.extend(problems)
            else:
                findings.setdefault(finding.finding_id, finding)
        return ChangeOutcome(list(findings.values()), rejected)

    def _to_finding(
        self,
        change: Change,
        ctx: ExtractionContext,
        prior_index: dict[str, SourceChunk],
        current_index: dict[str, SourceChunk],
        prior: DocumentChunks,
        current: DocumentChunks,
    ) -> tuple[ResearchFinding | None, list[str]]:
        label = f"{change.change_type}/{change.topic}"
        before = ground(change.before.evidence, prior_index) if change.before else None
        after = ground(change.after.evidence, current_index) if change.after else None
        needs_before = change.direction != "introduced"
        needs_after = change.direction != "withdrawn"
        problems: list[str] = []
        if needs_before and (before is None or not before.spans):
            problems.append(f"rejected {label}: no grounded 'before' evidence in prior document")
        if needs_after and (after is None or not after.spans):
            problems.append(f"rejected {label}: no grounded 'after' evidence in current document")
        if problems:
            for side in (before, after):
                if side is not None:
                    problems.extend(side.problems)
            return None, problems

        spans = [*(before.spans if before else []), *(after.spans if after else [])]
        support = SupportStatus.SUPPORTED
        notes: list[str] = [
            *(before.problems if before else []),
            *(after.problems if after else []),
        ]
        if change.change_type == "guidance":
            guidance_quotes = [
                s.quote
                for s in (after.spans if after and needs_after else before.spans if before else [])
            ]
            if not any(_FORWARD_LOOKING.search(q) for q in guidance_quotes):
                problems_msg = "guidance not quoted from forward-looking language"
                notes.append(problems_msg)
                support = SupportStatus.UNCERTAIN
        if change.direction == "unclear" or change.confidence < self.confidence_floor:
            support = SupportStatus.UNCERTAIN
            notes.append("ambiguous change -> uncertain")
        quoted_numbers = set().union(*(numbers_in(s.quote) for s in spans))
        claimed = numbers_in(change.summary)
        for stated in (change.before, change.after):
            if stated is not None:
                claimed |= numbers_in(stated.statement)
        if claimed - quoted_numbers:
            support = SupportStatus.UNCERTAIN
            notes.append(f"numbers not present in cited quotes: {sorted(claimed - quoted_numbers)}")
        if notes and support is SupportStatus.SUPPORTED:
            support = SupportStatus.UNCERTAIN

        finding = ResearchFinding(
            finding_id=finding_id(
                ctx.research_run_id,
                CATEGORY[change.change_type],
                f"{change.topic}|{current.document_id}",
            ),
            research_run_id=ctx.research_run_id,
            category=CATEGORY[change.change_type],
            claim_type=ClaimType.INFERENCE,
            statement=change.summary.strip(),
            evidence=tuple(spans),
            confidence=change.confidence,
            support_status=support,
            attributes={
                "topic": change.topic,
                "direction": change.direction,
                "before_statement": change.before.statement if change.before else None,
                "after_statement": change.after.statement if change.after else None,
                "before_document_id": prior.document_id,
                "after_document_id": current.document_id,
                "before_chunk_ids": [s.chunk_id for s in before.spans] if before else [],
                "after_chunk_ids": [s.chunk_id for s in after.spans] if after else [],
                **({"grounding_problems": notes} if notes else {}),
            },
            model_version=ctx.model,
            prompt_version=ctx.prompt_ref,
            schema_version=ctx.schema_version,
            created_at=ctx.created_at,
        )
        return finding, notes
