"""Risk, catalyst and open-question extractor with duplicate normalization."""

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
    Claim,
    EvidenceRef,
    ExtractionContext,
    SourceChunk,
    claim_to_finding,
    render_chunks,
)
from equitydesk.domain.research import ClaimType, FindingCategory, ResearchFinding, SupportStatus

SCHEMA_VERSION = "risk_catalyst_extraction.v1"
DUPLICATE_THRESHOLD = 0.6

PROMPT = PromptTemplate(
    prompt_id="risk_catalyst_open_questions",
    version="1.0.0",
    system=(
        "You identify investment risks, potential catalysts and unresolved questions for a "
        "company from its official documents. " + INJECTION_GUARD + " A catalyst must be a "
        "specific event or development named in the sources. Open questions must be prompted by "
        "cited source text, not general curiosity."
    ),
    user_template=(
        "Company: $company\n\nReturn risks, catalysts and open_questions, each with horizon "
        "(near < 12 months, medium 1-3 years, long > 3 years, unspecified), a short title, a "
        "classification category and evidence.\n\n$chunks"
    ),
)

RiskCategory = Literal[
    "supply_chain", "competition", "regulatory", "legal", "macro", "geopolitical",
    "product", "technology", "financial", "customer", "operational", "other",
]  # fmt: skip
Horizon = Literal["near", "medium", "long", "unspecified"]


class Item(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str
    statement: str
    category: RiskCategory
    horizon: Horizon
    claim_type: Literal["fact", "inference"]
    evidence: list[EvidenceRef]
    confidence: float = Field(ge=0.0, le=1.0)


class OpenQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str
    why_it_matters: str
    evidence: list[EvidenceRef]


class RiskCatalystExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    risks: list[Item]
    catalysts: list[Item]
    open_questions: list[OpenQuestion]


_STOP = frozenset("a an and the of in on for to from by with its our their risk risks".split())


def title_tokens(title: str) -> frozenset[str]:
    words = re.findall(r"[a-z0-9]+", title.casefold())
    return frozenset(w.rstrip("s") for w in words if w not in _STOP)


def similarity(a: str, b: str) -> float:
    ta, tb = title_tokens(a), title_tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


@dataclass
class RiskOutcome:
    findings: list[ResearchFinding]
    rejected: list[str] = field(default_factory=list)
    merged: list[tuple[str, str]] = field(default_factory=list)  # (kept title, merged title)
    failure: StructuredFailure | None = None


class RiskCatalystExtractor:
    def __init__(self, llm: StructuredLLMClient) -> None:
        self.llm = llm

    def extract(
        self,
        research_run_id: str,
        company_name: str,
        chunks: Sequence[SourceChunk],
        created_at: datetime,
    ) -> RiskOutcome:
        rendered, used = render_chunks(chunks)
        index = {sc.chunk.chunk_id: sc for sc in used}
        result = self.llm.generate(
            PROMPT,
            {"company": company_name, "chunks": rendered},
            RiskCatalystExtraction,
            SCHEMA_VERSION,
        )
        if result.value is None:
            return RiskOutcome([], failure=result.failure)
        ctx = ExtractionContext(
            research_run_id, created_at, result.model, PROMPT.ref, SCHEMA_VERSION
        )
        outcome = RiskOutcome([])
        for kind, category in (
            ("risks", FindingCategory.RISK),
            ("catalysts", FindingCategory.CATALYST),
        ):
            kept: list[tuple[Item, ResearchFinding]] = []
            for item in getattr(result.value, kind):
                claim = Claim(statement=item.statement, claim_type=item.claim_type,
                              evidence=item.evidence, confidence=item.confidence)  # fmt: skip
                attrs = {
                    "title": item.title,
                    "classification": item.category,
                    "horizon": item.horizon,
                }
                finding, problems = claim_to_finding(claim, category, ctx, index, attrs)
                if finding is None:
                    outcome.rejected.extend(problems)
                    continue
                dup = next(
                    (
                        i
                        for i, (k, _) in enumerate(kept)
                        if similarity(k.title, item.title) >= DUPLICATE_THRESHOLD
                    ),
                    None,
                )
                if dup is None:
                    kept.append((item, finding))
                    continue
                prev_item, prev = kept[dup]
                outcome.merged.append((prev_item.title, item.title))
                kept[dup] = (prev_item, _merge(prev, finding))
            outcome.findings.extend(f for _, f in kept)

        for q in result.value.open_questions:
            claim = Claim(
                statement=q.question, claim_type="inference", evidence=q.evidence, confidence=1.0
            )
            finding, problems = claim_to_finding(
                claim,
                FindingCategory.OPEN_QUESTION,
                ctx,
                index,
                {"why_it_matters": q.why_it_matters},
            )
            if finding is None:
                outcome.rejected.extend(problems)
            else:
                # An open question is by definition unresolved: never a supported fact.
                outcome.findings.append(
                    finding.model_copy(
                        update={
                            "support_status": SupportStatus.UNCERTAIN,
                            "claim_type": ClaimType.INFERENCE,
                        }
                    )
                )
        return outcome


def _merge(kept: ResearchFinding, dup: ResearchFinding) -> ResearchFinding:
    seen = {(e.chunk_id, e.quote) for e in kept.evidence}
    evidence = kept.evidence + tuple(e for e in dup.evidence if (e.chunk_id, e.quote) not in seen)
    merged_titles = [*kept.attributes.get("merged_titles", []), dup.attributes.get("title")]
    support = kept.support_status
    if SupportStatus.SUPPORTED in (kept.support_status, dup.support_status):
        support = SupportStatus.SUPPORTED if kept.evidence else support
    return ResearchFinding.model_validate(
        {
            **kept.model_dump(),
            "evidence": evidence,
            "confidence": max(kept.confidence, dup.confidence),
            "support_status": support,
            "attributes": {**kept.attributes, "merged_titles": merged_titles},
        }
    )
