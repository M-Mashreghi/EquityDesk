"""Evidence validator and research-object quality gate.

Independent of how a finding was produced, it is re-checked against the run's point-in-time
snapshot and the normalized source text before it may be used in a report.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from equitydesk.application.research.grounding import normalize_text
from equitydesk.domain.documents import SourceDocument
from equitydesk.domain.normalized import NormalizedDocument
from equitydesk.domain.research import ClaimType, EvidenceSpan, ResearchFinding, SupportStatus
from equitydesk.domain.snapshot import ResearchSnapshot


class IssueCode(StrEnum):
    WRONG_RUN = "wrong_run"
    NOT_IN_SNAPSHOT = "evidence_not_in_snapshot"
    FUTURE_EVIDENCE = "future_evidence"
    UNKNOWN_DOCUMENT = "unknown_document"
    UNKNOWN_LOCATOR = "unknown_locator"
    QUOTE_MISMATCH = "quote_mismatch"
    NO_VALID_EVIDENCE = "no_valid_evidence"
    PARTIAL_EVIDENCE = "partial_evidence"
    LOW_CONFIDENCE = "low_confidence"
    SECONDARY_ONLY = "secondary_source_only"


class Severity(StrEnum):
    BLOCKING = "blocking"  # the finding is rejected
    WARNING = "warning"  # the finding is downgraded / annotated


@dataclass(frozen=True)
class QualityIssue:
    code: IssueCode
    severity: Severity
    finding_id: str
    detail: str


@dataclass
class QualityReport:
    accepted: list[ResearchFinding] = field(default_factory=list)
    rejected: list[ResearchFinding] = field(default_factory=list)
    issues: list[QualityIssue] = field(default_factory=list)

    @property
    def blocking(self) -> list[QualityIssue]:
        return [i for i in self.issues if i.severity is Severity.BLOCKING]

    def issues_for(self, finding_id: str) -> list[QualityIssue]:
        return [i for i in self.issues if i.finding_id == finding_id]


@dataclass(frozen=True)
class GateRules:
    confidence_floor: float = 0.5
    require_primary_for_facts: bool = True


class EvidenceQualityGate:
    def __init__(self, rules: GateRules | None = None) -> None:
        self.rules = rules or GateRules()

    def validate(
        self,
        research_run_id: str,
        as_of: datetime,
        snapshot: ResearchSnapshot,
        findings: Iterable[ResearchFinding],
        documents: Mapping[str, SourceDocument],
        normalized: Mapping[str, NormalizedDocument],
    ) -> QualityReport:
        report = QualityReport()
        for finding in findings:
            issues: list[QualityIssue] = []

            fid = finding.finding_id

            def add(
                code: IssueCode,
                severity: Severity,
                detail: str,
                sink: list[QualityIssue] = issues,
                fid: str = fid,
            ) -> None:
                sink.append(QualityIssue(code, severity, fid, detail))

            if finding.research_run_id != research_run_id:
                add(IssueCode.WRONG_RUN, Severity.BLOCKING, finding.research_run_id)
                report.issues.extend(issues)
                report.rejected.append(finding)
                continue

            valid: list[EvidenceSpan] = []
            for span in finding.evidence:
                problem = self._check_span(span, as_of, snapshot, documents, normalized)
                if problem is None:
                    valid.append(span)
                else:
                    add(problem[0], Severity.WARNING, problem[1])

            needs_evidence = finding.claim_type in (ClaimType.FACT, ClaimType.INFERENCE)
            if needs_evidence and not valid:
                add(
                    IssueCode.NO_VALID_EVIDENCE,
                    Severity.BLOCKING,
                    "no evidence survived validation",
                )
                report.issues.extend(issues)
                report.rejected.append(finding)
                continue

            support = finding.support_status
            if valid and len(valid) < len(finding.evidence) and support is SupportStatus.SUPPORTED:
                add(
                    IssueCode.PARTIAL_EVIDENCE, Severity.WARNING, "some evidence spans were invalid"
                )
                support = SupportStatus.UNCERTAIN
            if (
                support is SupportStatus.SUPPORTED
                and finding.confidence < self.rules.confidence_floor
            ):
                add(IssueCode.LOW_CONFIDENCE, Severity.WARNING, f"confidence {finding.confidence}")
                support = SupportStatus.UNCERTAIN
            if (
                self.rules.require_primary_for_facts
                and finding.claim_type is ClaimType.FACT
                and support is SupportStatus.SUPPORTED
                and valid
                and not any(documents[s.document_id].is_primary for s in valid)
            ):
                add(
                    IssueCode.SECONDARY_ONLY,
                    Severity.WARNING,
                    "fact supported only by secondary sources",
                )
                support = SupportStatus.UNCERTAIN

            updated = ResearchFinding.model_validate(
                {
                    **finding.model_dump(),
                    "evidence": tuple(valid),
                    "support_status": support,
                    "attributes": {
                        **finding.attributes,
                        **({"quality_issues": [i.code.value for i in issues]} if issues else {}),
                    },
                }
            )
            report.issues.extend(issues)
            report.accepted.append(updated)
        return report

    @staticmethod
    def _check_span(
        span: EvidenceSpan,
        as_of: datetime,
        snapshot: ResearchSnapshot,
        documents: Mapping[str, SourceDocument],
        normalized: Mapping[str, NormalizedDocument],
    ) -> tuple[IssueCode, str] | None:
        doc = documents.get(span.document_id)
        if doc is None:
            return IssueCode.UNKNOWN_DOCUMENT, span.document_id
        if doc.published_at > as_of:
            return (
                IssueCode.FUTURE_EVIDENCE,
                f"{span.document_id} published {doc.published_at.isoformat()}",
            )
        if not snapshot.contains(span.document_id):
            return IssueCode.NOT_IN_SNAPSHOT, span.document_id
        norm = normalized.get(span.document_id)
        chunk = norm.chunk(span.chunk_id) if norm else None
        if chunk is None:
            return IssueCode.UNKNOWN_LOCATOR, span.chunk_id
        if normalize_text(span.quote) not in normalize_text(chunk.text):
            return IssueCode.QUOTE_MISMATCH, span.chunk_id
        return None
