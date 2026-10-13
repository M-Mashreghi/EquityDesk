from __future__ import annotations

from pathlib import Path

import pytest

import factories as f
from equitydesk.application.research.quality_gate import EvidenceQualityGate, IssueCode, Severity
from equitydesk.domain.documents import SourceType
from equitydesk.domain.research import ClaimType, EvidenceSpan, SupportStatus
from equitydesk.domain.snapshot import ResearchSnapshot
from equitydesk.infrastructure.parsers.html import HtmlDocumentParser

FIX = Path(__file__).resolve().parents[1] / "fixtures"
AS_OF = f.dt(2024, 1, 15)


@pytest.fixture(scope="module")
def env():
    tenk_norm = HtmlDocumentParser().parse("tenk", (FIX / "sec" / "aapl-20230930.htm").read_bytes())
    news_norm = HtmlDocumentParser().parse(
        "news", (FIX / "ir" / "apple-q4-fy2023-release.html").read_bytes()
    )
    future_norm = news_norm.model_copy(update={"document_id": "future"})
    documents = {
        "tenk": f.document(document_id="tenk", published_at=f.dt(2023, 11, 3, 4)),
        "news": f.document(
            document_id="news",
            source_type=SourceType.SECONDARY,
            official_id=None,
            content_hash=None,
            source_url="https://news.example.com/a",
            published_at=f.dt(2023, 11, 3),
        ),  # fmt: skip
        "future": f.document(
            document_id="future", published_at=f.dt(2024, 2, 2), official_id="acc-f"
        ),
        "outside": f.document(
            document_id="outside", published_at=f.dt(2023, 1, 1), official_id="acc-o"
        ),
    }
    snapshot = ResearchSnapshot(
        snapshot_id="snap_x", research_run_id="run_1", company_id="co_apple", as_of=AS_OF,
        document_ids=("tenk", "news"),
    )  # fmt: skip
    normalized = {"tenk": tenk_norm, "news": news_norm, "future": future_norm, "outside": tenk_norm}
    return snapshot, documents, normalized, tenk_norm


def span(norm, needle: str, quote: str, doc: str = "tenk") -> EvidenceSpan:
    chunk = next(c for c in norm.chunks if needle in c.text)
    return EvidenceSpan(document_id=doc, chunk_id=chunk.chunk_id, quote=quote)


def gate(env, findings):
    snapshot, documents, normalized, _ = env
    return EvidenceQualityGate().validate("run_1", AS_OF, snapshot, findings, documents, normalized)


def good_span(norm) -> EvidenceSpan:
    return span(
        norm, "outsourcing partners", "performed in whole or in part by outsourcing partners"
    )


def test_valid_finding_passes_unchanged(env) -> None:
    fnd = f.finding(evidence=(good_span(env[3]),))
    report = gate(env, [fnd])
    assert report.accepted == [fnd] and not report.issues


def test_future_evidence_rejected(env) -> None:
    norm = env[3]
    future = EvidenceSpan(
        document_id="future", chunk_id=good_span(norm).chunk_id, quote=good_span(norm).quote
    )
    report = gate(env, [f.finding(evidence=(future,))])
    assert report.rejected and {i.code for i in report.issues} == {
        IssueCode.FUTURE_EVIDENCE,
        IssueCode.NO_VALID_EVIDENCE,
    }


def test_evidence_outside_snapshot_rejected(env) -> None:
    s = good_span(env[3])
    outside = EvidenceSpan(document_id="outside", chunk_id=s.chunk_id, quote=s.quote)
    report = gate(env, [f.finding(evidence=(outside,))])
    assert report.rejected and report.issues[0].code is IssueCode.NOT_IN_SNAPSHOT


@pytest.mark.parametrize(
    ("bad", "code"),
    [
        (
            EvidenceSpan(document_id="tenk", chunk_id="tenk:s99:p0", quote="whatever quote here"),
            IssueCode.UNKNOWN_LOCATOR,
        ),
        (
            EvidenceSpan(document_id="ghost", chunk_id="x", quote="whatever quote here"),
            IssueCode.UNKNOWN_DOCUMENT,
        ),
    ],
)
def test_bad_locators_rejected(env, bad: EvidenceSpan, code: IssueCode) -> None:
    report = gate(env, [f.finding(evidence=(bad,))])
    assert report.rejected and report.issues[0].code is code


def test_altered_quote_rejected(env) -> None:
    s = good_span(env[3])
    altered = EvidenceSpan(
        document_id="tenk", chunk_id=s.chunk_id, quote="performed entirely in the United States"
    )
    report = gate(env, [f.finding(evidence=(altered,))])
    assert report.rejected and report.issues[0].code is IssueCode.QUOTE_MISMATCH


def test_partial_evidence_downgrades(env) -> None:
    s = good_span(env[3])
    bad = EvidenceSpan(
        document_id="tenk", chunk_id=s.chunk_id, quote="this text is not in the chunk"
    )
    report = gate(env, [f.finding(evidence=(s, bad))])
    (acc,) = report.accepted
    assert acc.support_status is SupportStatus.UNCERTAIN and acc.evidence == (s,)
    assert "partial_evidence" in acc.attributes["quality_issues"]
    assert all(i.severity is Severity.WARNING for i in report.issues)


def test_low_confidence_downgrades(env) -> None:
    report = gate(env, [f.finding(evidence=(good_span(env[3]),), confidence=0.2)])
    assert report.accepted[0].support_status is SupportStatus.UNCERTAIN
    assert report.issues[0].code is IssueCode.LOW_CONFIDENCE


def test_secondary_only_fact_downgrades(env) -> None:
    news = env[2]["news"]
    s = span(
        news, "installed base", "installed base of active devices reached a new record", doc="news"
    )
    report = gate(env, [f.finding(evidence=(s,))])
    assert report.accepted[0].support_status is SupportStatus.UNCERTAIN
    assert report.issues[0].code is IssueCode.SECONDARY_ONLY


def test_wrong_run_and_unknown_claims(env) -> None:
    report = gate(env, [f.finding(research_run_id="other", evidence=(good_span(env[3]),))])
    assert report.rejected and report.issues[0].code is IssueCode.WRONG_RUN
    unknown = f.finding(
        claim_type=ClaimType.UNKNOWN,
        evidence=(),
        support_status=SupportStatus.NOT_SUPPORTED,
        confidence=0.0,
    )
    assert gate(env, [unknown]).accepted == [unknown]


def test_report_helpers(env) -> None:
    report = gate(
        env,
        [
            f.finding(
                finding_id="bad",
                evidence=(EvidenceSpan(document_id="ghost", chunk_id="x", quote="whatever quote"),),
            )
        ],
    )
    assert len(report.blocking) == 1 and report.issues_for("bad")
