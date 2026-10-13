"""Business model, segment, revenue-driver, customer and geography evidence extractor."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from equitydesk.application.llm import PromptTemplate, StructuredFailure, StructuredLLMClient
from equitydesk.application.research.grounding import (
    INJECTION_GUARD,
    Claim,
    EvidenceRef,
    ExtractionContext,
    SourceChunk,
    claim_to_finding,
    render_chunks,
    unknown_finding,
)
from equitydesk.domain.research import FindingCategory, ResearchFinding

SCHEMA_VERSION = "business_extraction.v1"

PROMPT = PromptTemplate(
    prompt_id="business_model_extractor",
    version="1.0.0",
    system=(
        "You are an equity research analyst extracting how a company makes money from its "
        "official filings and investor materials. " + INJECTION_GUARD
    ),
    user_template=(
        "Company: $company\n\nExtract, with evidence:\n"
        "- business_model: one claim describing what the company sells and how it earns revenue\n"
        "- segments: reportable/operating segments named in the sources\n"
        "- revenue_drivers: products/services/pricing factors the sources tie to revenue\n"
        "- customers: customer types or concentrations stated in the sources\n"
        "- geography: geographic markets stated in the sources\n"
        "Use claim_type 'fact' for direct restatements and 'inference' for interpretations.\n\n"
        "$chunks"
    ),
)

FIELDS = {
    "business_model": FindingCategory.BUSINESS_MODEL,
    "segments": FindingCategory.SEGMENT,
    "revenue_drivers": FindingCategory.REVENUE_DRIVER,
    "customers": FindingCategory.CUSTOMER,
    "geography": FindingCategory.GEOGRAPHY,
}


class SegmentClaim(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    statement: str
    claim_type: Literal["fact", "inference"]
    evidence: list[EvidenceRef]
    confidence: float = Field(ge=0.0, le=1.0)


class BusinessExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    business_model: Claim | None
    segments: list[SegmentClaim]
    revenue_drivers: list[Claim]
    customers: list[Claim]
    geography: list[Claim]
    not_found: list[str]


@dataclass
class ExtractionOutcome:
    findings: list[ResearchFinding]
    rejected: list[str] = field(default_factory=list)
    failure: StructuredFailure | None = None
    chunks_used: int = 0


class BusinessModelExtractor:
    def __init__(self, llm: StructuredLLMClient) -> None:
        self.llm = llm

    def extract(
        self,
        research_run_id: str,
        company_name: str,
        chunks: Sequence[SourceChunk],
        created_at: datetime,
    ) -> ExtractionOutcome:
        rendered, used = render_chunks(chunks)
        index = {sc.chunk.chunk_id: sc for sc in used}
        result = self.llm.generate(
            PROMPT,
            {"company": company_name, "chunks": rendered},
            BusinessExtraction,
            SCHEMA_VERSION,
        )
        ctx = ExtractionContext(
            research_run_id, created_at, result.model, PROMPT.ref, SCHEMA_VERSION
        )
        if result.value is None:
            # Safe failure: every field stays unknown; nothing is invented.
            reason = f"llm failure: {result.failure.code.value if result.failure else 'unknown'}"
            return ExtractionOutcome(
                [unknown_finding(cat, name, ctx, reason) for name, cat in FIELDS.items()],
                failure=result.failure,
                chunks_used=len(used),
            )
        data = result.value
        findings: list[ResearchFinding] = []
        rejected: list[str] = []
        accepted_per_field: dict[str, int] = dict.fromkeys(FIELDS, 0)

        def accept(name: str, claim: Claim, attributes: dict[str, str] | None = None) -> None:
            finding, problems = claim_to_finding(claim, FIELDS[name], ctx, index, attributes)
            if finding is None:
                rejected.extend(problems)
            elif finding.finding_id not in {f.finding_id for f in findings}:
                findings.append(finding)
                accepted_per_field[name] += 1

        if data.business_model is not None:
            accept("business_model", data.business_model)
        for seg in data.segments:
            claim = Claim(statement=seg.statement, claim_type=seg.claim_type, evidence=seg.evidence,
                          confidence=seg.confidence)  # fmt: skip
            accept("segments", claim, {"segment_name": seg.name})
        for name in ("revenue_drivers", "customers", "geography"):
            for claim in getattr(data, name):
                accept(name, claim)

        for name, cat in FIELDS.items():
            if accepted_per_field[name] == 0:
                reason = (
                    "listed as not_found by model"
                    if name in data.not_found
                    else "no grounded evidence"
                )
                findings.append(unknown_finding(cat, name, ctx, reason))
        return ExtractionOutcome(findings, rejected, None, len(used))
