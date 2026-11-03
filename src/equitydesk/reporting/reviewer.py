"""Independent reviewer / contradiction & completeness gate.

The reviewer re-checks a composed report against the objects it claims to rest on. Deterministic
checks may block; the optional LLM contradiction pass can only flag (warning). The reviewer never
rewrites a claim: it reports findings, and a run with blocking findings cannot reach Completed.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from equitydesk.application.llm import PromptTemplate, StructuredLLMClient
from equitydesk.application.research.grounding import INJECTION_GUARD, numbers_in
from equitydesk.domain.documents import SourceDocument
from equitydesk.domain.metrics import MetricResult
from equitydesk.domain.report import ReportClaim, ResearchReport, SectionKey
from equitydesk.domain.research import ClaimType, ResearchRun, ResearchStatus, SupportStatus
from equitydesk.domain.review import ReviewFinding, ReviewGateError, ReviewResult, ReviewSeverity
from equitydesk.domain.valuation import ValuationMethod, ValuationRun

REVIEWER_VERSION = "1.0.0"
CRITICAL_SECTIONS = (
    SectionKey.COMPANY_OVERVIEW, SectionKey.FINANCIAL_ANALYSIS, SectionKey.VALUATION,
    SectionKey.INVESTMENT_THESIS, SectionKey.RISKS,
)  # fmt: skip
STALE_AFTER = timedelta(days=400)
VALUE_REL_TOLERANCE = 1e-6
LLM_CONFIDENCE_FLOOR = 0.5
_CONSENSUS = re.compile(r"\bconsensus\b|\bstreet estimates?\b", re.IGNORECASE)
_THESIS_STANCE = re.compile(r"^Evidence (for|against): ")

B, W, INFO = ReviewSeverity.BLOCKING, ReviewSeverity.WARNING, ReviewSeverity.INFO


def _rf(code: str, severity: ReviewSeverity, message: str, claims: Iterable[ReportClaim] = (),
        citation_ids: Sequence[str] = (), section: str | None = None, origin: str = "deterministic") -> ReviewFinding:  # fmt: skip
    claim_ids = tuple(c.claim_id for c in claims)
    key = f"{code}|{message}|{','.join(claim_ids)}|{','.join(citation_ids)}"
    return ReviewFinding(
        review_finding_id="rvf_" + hashlib.sha256(key.encode()).hexdigest()[:16],
        code=code, severity=severity, message=message, claim_ids=claim_ids,
        citation_ids=tuple(citation_ids), section=section, origin=origin,
    )  # fmt: skip


def renderings(value: float) -> set[str]:
    """Text forms the composer uses for a computed number."""
    return {
        f"{value * 100:.1f}%", f"{value * 100:+.1f}%", f"{value * 100:.0f}%", f"{value:.2f}",
        f"{value:,.2f}", f"{value:,.0f}", f"{value:.2f}x", f"{value:.0f} days",
    }  # fmt: skip


def _numeric_leaves(node: Any) -> list[float]:
    if isinstance(node, bool):
        return []
    if isinstance(node, int | float):
        return [float(node)]
    if isinstance(node, Mapping):
        return [x for v in node.values() for x in _numeric_leaves(v)]
    if isinstance(node, list | tuple):
        return [x for v in node for x in _numeric_leaves(v)]
    return []


def _close(a: float, b: float) -> bool:
    return math.isclose(a, b, rel_tol=VALUE_REL_TOLERANCE, abs_tol=1e-9)


# --- deterministic checks -------------------------------------------------------------------


def check_sections(report: ResearchReport) -> list[ReviewFinding]:
    out = []
    for s in report.sections:
        substantive = [c for c in s.claims if c.claim_type is not ClaimType.UNKNOWN]
        if substantive:
            continue
        if s.key in CRITICAL_SECTIONS:
            out.append(
                _rf(
                    "missing_critical_section",
                    B,
                    f"critical section '{s.title}' has no supported content",
                    section=s.key.value,
                )
            )
        else:
            out.append(
                _rf(
                    "empty_section",
                    W,
                    f"section '{s.title}' has no substantive content",
                    section=s.key.value,
                )
            )
    return out


def check_support(report: ResearchReport) -> list[ReviewFinding]:
    out = []
    for c in report.claims:
        material = c.claim_type in (ClaimType.FACT, ClaimType.INFERENCE)
        if not material or c.section is SectionKey.OPEN_QUESTIONS:
            continue
        if not c.has_support:
            out.append(
                _rf(
                    "unsupported_claim",
                    B,
                    f"material claim has no citation, metric or valuation link: {c.text[:120]}",
                    [c],
                    section=c.section.value,
                )
            )
        elif c.support_status is not SupportStatus.SUPPORTED:
            out.append(
                _rf(
                    "uncertain_claim",
                    W,
                    f"claim is {c.support_status.value}: {c.text[:120]}",
                    [c],
                    section=c.section.value,
                )
            )
        if _CONSENSUS.search(c.text) and not c.citation_ids:
            out.append(
                _rf(
                    "unsupported_consensus",
                    B,
                    "consensus/estimate reference without a cited source",
                    [c],
                    section=c.section.value,
                )
            )
    return out


def check_sources(
    report: ResearchReport, documents: Sequence[SourceDocument], snapshot_ids: set[str] | None
) -> list[ReviewFinding]:
    out = []
    docs = {d.document_id: d for d in documents}
    for cit in report.citations:
        doc = docs.get(cit.document_id)
        if doc is None:
            out.append(
                _rf(
                    "unknown_source",
                    B,
                    f"citation {cit.citation_id} refers to unknown document {cit.document_id}",
                    citation_ids=[cit.citation_id],
                )
            )
            continue
        if doc.published_at > report.as_of or cit.published_at > report.as_of:
            out.append(
                _rf(
                    "future_source",
                    B,
                    f"{doc.document_id} was published after as_of",
                    citation_ids=[cit.citation_id],
                )
            )
        if snapshot_ids is not None and doc.document_id not in snapshot_ids:
            out.append(
                _rf(
                    "citation_outside_snapshot",
                    B,
                    f"{doc.document_id} is not a member of the run snapshot",
                    citation_ids=[cit.citation_id],
                )
            )
        if (cit.content_hash, cit.published_at, cit.official_id) != (
            doc.content_hash,
            doc.published_at,
            doc.official_id,
        ):
            out.append(
                _rf(
                    "citation_provenance_mismatch",
                    B,
                    f"citation {cit.citation_id} provenance differs from document {doc.document_id}",
                    citation_ids=[cit.citation_id],
                )
            )
        newer = [d for d in documents if d.published_at <= report.as_of
                 and doc.document_id in (d.supersedes_document_id, d.amends_document_id)]  # fmt: skip
        if newer:
            out.append(
                _rf(
                    "superseded_source",
                    W,
                    f"{doc.document_id} was amended/superseded by {newer[0].document_id} before as_of",
                    citation_ids=[cit.citation_id],
                )
            )
    primary = [c.published_at for c in report.citations if c.source_type.value != "secondary"]
    if primary and report.as_of - max(primary) > STALE_AFTER:
        out.append(
            _rf(
                "stale_sources",
                W,
                f"newest primary source is older than {STALE_AFTER.days} days at as_of",
            )
        )
    return out


def check_calculations(
    report: ResearchReport, metrics: Sequence[MetricResult], valuations: Sequence[ValuationRun]
) -> list[ReviewFinding]:
    out = []
    metric_by_id = {m.metric_id: m for m in metrics}
    val_by_id = {v.valuation_id: v for v in valuations}
    quotes = {c.citation_id: c.quote for c in report.citations}
    for c in report.claims:
        for mid in c.metric_ids:
            if mid not in metric_by_id:
                out.append(
                    _rf(
                        "untraceable_metric",
                        B,
                        f"metric {mid} is not among the run's metric results",
                        [c],
                    )
                )
        for vid in c.valuation_ids:
            if vid not in val_by_id:
                out.append(
                    _rf(
                        "untraceable_valuation",
                        B,
                        f"valuation {vid} is not among the run's valuation records",
                        [c],
                    )
                )
        linked_metrics = [metric_by_id[m] for m in c.metric_ids if m in metric_by_id]
        linked_vals = [val_by_id[v] for v in c.valuation_ids if v in val_by_id]
        leaves = [x for v in linked_vals for x in _numeric_leaves(v.outputs)]
        for name, value in c.values.items():
            if value is None:
                continue
            source = [m.value for m in linked_metrics if m.name == name and m.value is not None]
            if linked_metrics and not any(_close(value, s) for s in source):
                out.append(
                    _rf(
                        "calculation_mismatch",
                        B,
                        f"{name}={value} does not match metric results {source}",
                        [c],
                    )
                )
            elif not linked_metrics and linked_vals and not any(_close(value, x) for x in leaves):
                out.append(
                    _rf(
                        "calculation_mismatch",
                        B,
                        f"{name}={value} is not an output of the linked valuation",
                        [c],
                    )
                )
            if not renderings(value) & set(_tokens(c.text)):
                out.append(
                    _rf(
                        "text_calculation_mismatch",
                        B,
                        f"text does not state the computed {name} ({value})",
                        [c],
                    )
                )
        if not (c.metric_ids or c.valuation_ids) and c.claim_type is not ClaimType.UNKNOWN:
            cited = set().union(*(numbers_in(quotes[i]) for i in c.citation_ids if i in quotes))
            invented = numbers_in(c.text) - cited
            if invented:
                out.append(
                    _rf(
                        "number_not_in_evidence",
                        B,
                        f"numbers {sorted(invented)} are neither cited nor computed",
                        [c],
                    )
                )
    for v in valuations:
        if v.method is ValuationMethod.SCENARIO:
            blocking = [i for i in v.outputs.get("issues", []) if i.get("blocking")]
            if blocking:
                out.append(
                    _rf(
                        "scenario_inconsistent",
                        B,
                        f"scenario set {v.valuation_id} has blocking issues: {sorted({i['code'] for i in blocking})}",
                    )
                )
    return out


def _tokens(text: str) -> list[str]:
    """Number-like tokens with their unit suffix, e.g. '17.4%', '18.78', '2.00x', '30 days'."""
    return [
        m.group(0).rstrip(".,;)")
        for m in re.finditer(r"[-+]?\d[\d,]*(?:\.\d+)?(?:%|x| days)?", text)
    ]


def check_contradictions(report: ResearchReport) -> list[ReviewFinding]:
    out = []
    seen: dict[tuple[str, str], tuple[float, ReportClaim]] = {}
    for c in report.claims:
        for mid in c.metric_ids:
            for name, value in c.values.items():
                if value is None:
                    continue
                prev = seen.get((mid, name))
                if prev and not _close(prev[0], value):
                    out.append(
                        _rf(
                            "contradictory_values",
                            B,
                            f"{mid}:{name} is stated as {prev[0]} and {value}",
                            [prev[1], c],
                        )
                    )
                seen.setdefault((mid, name), (value, c))
    stance: dict[str, dict[str, ReportClaim]] = {}
    for c in report.section(SectionKey.INVESTMENT_THESIS).claims:
        m = _THESIS_STANCE.match(c.text)
        if m:
            for fid in c.finding_ids:
                stance.setdefault(fid, {})[m.group(1)] = c
    for fid, sides in stance.items():
        if len(sides) == 2:
            out.append(
                _rf(
                    "contradictory_thesis_evidence",
                    W,
                    f"finding {fid} is used as evidence both for and against the thesis",
                    sides.values(),
                )
            )
    return out


# --- LLM-assisted contradiction pass --------------------------------------------------------

CONTRADICTION_SCHEMA_VERSION = "contradiction_review.v1"
CONTRADICTION_PROMPT = PromptTemplate(
    prompt_id="report_contradiction_review",
    version="1.0.0",
    system=(
        "You review an equity research report for statements that contradict each other. "
        + INJECTION_GUARD
        + " Only compare the numbered claims given. Report a pair only when "
        "both cannot be true at the same time; do not judge style, and do not add facts."
    ),
    user_template="Claims (id: text):\n$claims\n\nReturn contradictions as pairs of claim ids.",
)


class Contradiction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim_a: str
    claim_b: str
    explanation: str
    confidence: float = Field(ge=0.0, le=1.0)


class ContradictionReview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contradictions: list[Contradiction]


@dataclass
class LlmContradictionReviewer:
    client: StructuredLLMClient

    def review(self, report: ResearchReport) -> tuple[list[ReviewFinding], str | None]:
        claims = {c.claim_id: c for c in report.claims if c.claim_type is not ClaimType.UNKNOWN}
        listing = "\n".join(f"{cid}: {c.text}" for cid, c in claims.items())
        result = self.client.generate(CONTRADICTION_PROMPT, {"claims": listing}, ContradictionReview,
                                      CONTRADICTION_SCHEMA_VERSION)  # fmt: skip
        origin = f"llm:{CONTRADICTION_PROMPT.ref}"
        if not result.ok or result.value is None:
            detail = result.failure.code if result.failure else "unknown"
            return [
                _rf(
                    "llm_review_unavailable",
                    INFO,
                    f"LLM contradiction pass failed ({detail})",
                    origin=origin,
                )
            ], result.model
        out = []
        for item in result.value.contradictions:
            a, b = claims.get(item.claim_a), claims.get(item.claim_b)
            if a is None or b is None or a is b:
                out.append(
                    _rf(
                        "llm_invalid_reference",
                        INFO,
                        f"LLM referenced unknown claims {item.claim_a}/{item.claim_b}",
                        origin=origin,
                    )
                )
                continue
            if item.confidence < LLM_CONFIDENCE_FLOOR:
                continue
            out.append(_rf("llm_contradiction", W, item.explanation, [a, b], origin=origin))
        return out, result.model


# --- orchestration --------------------------------------------------------------------------


def review_report(
    report: ResearchReport,
    documents: Sequence[SourceDocument],
    metrics: Sequence[MetricResult] = (),
    valuations: Sequence[ValuationRun] = (),
    snapshot_document_ids: Sequence[str] | None = None,
    llm_reviewer: LlmContradictionReviewer | None = None,
    reviewed_at: datetime | None = None,
) -> ReviewResult:
    run_valuations = [v for v in valuations if v.research_run_id == report.research_run_id]
    findings = [
        *check_sections(report),
        *check_support(report),
        *check_sources(
            report,
            documents,
            set(snapshot_document_ids) if snapshot_document_ids is not None else None,
        ),
        *check_calculations(report, metrics, run_valuations),
        *check_contradictions(report),
    ]
    model = None
    if llm_reviewer is not None:
        llm_findings, model = llm_reviewer.review(report)
        findings.extend(llm_findings)
    unique = list({f.review_finding_id: f for f in findings}.values())
    digest = hashlib.sha256(
        json.dumps(
            [report.report_id, REVIEWER_VERSION, sorted(f.review_finding_id for f in unique)]
        ).encode()
    ).hexdigest()
    return ReviewResult(
        review_id=f"rev_{digest[:20]}", research_run_id=report.research_run_id, report_id=report.report_id,
        reviewer_version=REVIEWER_VERSION, findings=tuple(unique),
        reviewed_at=reviewed_at or report.generated_at, model_version=model,
    )  # fmt: skip


def apply_review(run: ResearchRun, review: ReviewResult, at: datetime | None = None) -> ResearchRun:
    """Move a Reviewing run to Completed (passed) or NeedsInput (blocking findings)."""
    if review.research_run_id != run.research_run_id:
        raise ReviewGateError("review belongs to another research run")
    if run.status is not ResearchStatus.REVIEWING:
        raise ReviewGateError(f"run is {run.status}, expected Reviewing")
    if review.passed:
        return run.transition(ResearchStatus.COMPLETED, f"review {review.review_id} passed", at)
    codes = sorted({f.code for f in review.blocking})
    return run.transition(
        ResearchStatus.NEEDS_INPUT, f"review {review.review_id} blocked: {', '.join(codes)}", at
    )
