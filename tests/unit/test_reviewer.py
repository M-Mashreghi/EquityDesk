"""Independent reviewer: each blocking/warning check, LLM pass and the completion gate."""

from __future__ import annotations

import pytest

import factories as f
import report_fixtures as rf
from equitydesk.application.llm import StructuredLLMClient
from equitydesk.domain.report import ReportClaim, SectionKey
from equitydesk.domain.research import ClaimType, ResearchStatus, SupportStatus
from equitydesk.domain.review import ReviewGateError, ReviewSeverity
from equitydesk.infrastructure.llm.fake import ScriptedLLMProvider
from equitydesk.reporting.composer import compose_report
from equitydesk.reporting.reviewer import LlmContradictionReviewer, apply_review, review_report


@pytest.fixture(scope="module")
def data():
    return rf.inputs()


@pytest.fixture(scope="module")
def report(data):
    return compose_report(**data)


def review(report, data, **kw):
    args = {"documents": data["documents"], "metrics": data["metrics"], "valuations": data["valuations"],
            "snapshot_document_ids": [d.document_id for d in data["documents"]]} | kw  # fmt: skip
    return review_report(report, **args)


def codes(result, severity=None) -> set[str]:
    return {x.code for x in result.findings if severity is None or x.severity is severity}


def replace_claims(report, key: SectionKey, claims) -> object:
    sections = tuple(
        s.model_copy(update={"claims": tuple(claims)}) if s.key is key else s
        for s in report.sections
    )
    return report.model_copy(update={"sections": sections})


def claim(text: str, section=SectionKey.FINANCIAL_ANALYSIS, **kw) -> ReportClaim:
    data = dict(claim_id=f"cl_{abs(hash(text))}", section=section, text=text, claim_type=ClaimType.FACT,
                support_status=SupportStatus.SUPPORTED)  # fmt: skip
    data.update(kw)
    return ReportClaim(**data)


def test_clean_report_passes_with_only_warnings(report, data) -> None:
    result = review(report, data)
    assert result.passed, [x.message for x in result.blocking]
    assert "uncertain_claim" in codes(result, ReviewSeverity.WARNING)  # uncertain guidance finding
    assert result.review_id == review(report, data).review_id  # deterministic


def test_missing_critical_section_blocks(report, data) -> None:
    broken = replace_claims(report, SectionKey.VALUATION, [])
    result = review(broken, data)
    assert (
        "missing_critical_section" in codes(result, ReviewSeverity.BLOCKING) and not result.passed
    )


def test_unsupported_claim_and_consensus_block(report, data) -> None:
    bad = [
        claim("Margins will expand sharply.", SectionKey.RISKS, claim_type=ClaimType.INFERENCE,
              support_status=SupportStatus.UNCERTAIN),
        claim("Revenue beat consensus.", metric_ids=("m:co_apple:gross_margin:2023-09-30",), values={}),
    ]  # fmt: skip
    result = review(replace_claims(report, SectionKey.RISKS, bad), data)
    assert {"unsupported_claim", "unsupported_consensus"} <= codes(result, ReviewSeverity.BLOCKING)


def test_calculation_text_mismatch_blocks(report, data) -> None:
    gm = "m:co_apple:gross_margin:2023-09-30"
    wrong_value = claim("Gross margin: 50.0%.", metric_ids=(gm,), values={"gross_margin": 0.5})
    wrong_text = claim("Gross margin: 39.0%.", metric_ids=(gm,), values={"gross_margin": 0.441})
    ghost = claim("Something: 1.00.", metric_ids=("m:ghost",), values={})
    result = review(
        replace_claims(report, SectionKey.FINANCIAL_ANALYSIS, [wrong_value, wrong_text, ghost]),
        data,
    )
    blocking = codes(result, ReviewSeverity.BLOCKING)
    assert {"calculation_mismatch", "text_calculation_mismatch", "untraceable_metric"} <= blocking
    assert "contradictory_values" in blocking  # same metric stated as 0.5 and 0.441


def test_valuation_values_must_come_from_outputs(report, data) -> None:
    dcf_id = data["valuations"][1].valuation_id
    fake = claim(
        "DCF value 99.00 per share.",
        SectionKey.VALUATION,
        valuation_ids=(dcf_id,),
        values={"value_per_share": 99.0},
    )
    missing = claim("Value 1.00.", SectionKey.VALUATION, valuation_ids=("val_ghost",), values={})
    result = review(replace_claims(report, SectionKey.VALUATION, [fake, missing]), data)
    assert {"calculation_mismatch", "untraceable_valuation"} <= codes(
        result, ReviewSeverity.BLOCKING
    )


def test_invented_number_in_prose_blocks(report, data) -> None:
    cit = report.citations[0].citation_id
    c = claim("Supply chain risk affects 85% of units.", SectionKey.RISKS, claim_type=ClaimType.INFERENCE,
              citation_ids=(cit,))  # fmt: skip
    result = review(replace_claims(report, SectionKey.RISKS, [c]), data)
    assert "number_not_in_evidence" in codes(result, ReviewSeverity.BLOCKING)


def test_future_unknown_snapshot_and_superseded_sources(report, data) -> None:
    q2 = data["documents"][1]
    future_docs = [data["documents"][0], q2.model_copy(update={"published_at": f.dt(2024, 7, 1)})]
    assert "future_source" in codes(
        review(report, data, documents=future_docs), ReviewSeverity.BLOCKING
    )
    result = review(report, data, documents=data["documents"][:1])
    assert "unknown_source" in codes(result, ReviewSeverity.BLOCKING)
    result = review(report, data, snapshot_document_ids=["doc_10k_2023"])
    assert "citation_outside_snapshot" in codes(result, ReviewSeverity.BLOCKING)
    amendment = f.document(document_id="doc_10k_a", document_type="10-K/A", amends_document_id="doc_10k_2023",
                           published_at=f.dt(2024, 2, 1), official_id="0000320193-24-000001")  # fmt: skip
    result = review(report, data, documents=[*data["documents"], amendment])
    assert "superseded_source" in codes(result, ReviewSeverity.WARNING)


def test_stale_sources_warn(data) -> None:
    late = data | {
        "run": f.run(as_of=f.dt(2025, 12, 1), created_at=f.dt(2026, 1, 1), snapshot_id="snap_1")
    }
    rpt = compose_report(**late)
    assert "stale_sources" in codes(review(rpt, data), ReviewSeverity.WARNING)


def test_inconsistent_scenario_valuation_blocks(report, data) -> None:
    scen = data["valuations"][2]
    outputs = scen.outputs | {
        "issues": [{"code": "value_ordering", "blocking": True, "message": "x", "scenario": None}]
    }
    vals = [*data["valuations"][:2], scen.model_copy(update={"outputs": outputs})]
    assert "scenario_inconsistent" in codes(
        review(report, data, valuations=vals), ReviewSeverity.BLOCKING
    )


def test_llm_contradictions_only_flag(report, data) -> None:
    known = [c for c in report.claims if c.claim_type is not ClaimType.UNKNOWN]
    a, b = known[0].claim_id, known[1].claim_id
    provider = ScriptedLLMProvider([{"contradictions": [
        {"claim_a": a, "claim_b": b, "explanation": "conflict", "confidence": 0.9},
        {"claim_a": a, "claim_b": "cl_ghost", "explanation": "?", "confidence": 0.9},
        {"claim_a": a, "claim_b": b, "explanation": "weak", "confidence": 0.2},
    ]}])  # fmt: skip
    llm = LlmContradictionReviewer(StructuredLLMClient(provider, sleep=lambda _: None))
    result = review(report, data, llm_reviewer=llm)
    flagged = [x for x in result.findings if x.code == "llm_contradiction"]
    assert len(flagged) == 1 and flagged[0].severity is ReviewSeverity.WARNING
    assert flagged[0].origin == "llm:report_contradiction_review@1.0.0" and set(
        flagged[0].claim_ids
    ) == {a, b}
    assert "llm_invalid_reference" in codes(result) and result.passed
    assert result.model_version == "fake-model-1"


def test_llm_failure_is_reported_not_fatal(report, data) -> None:
    llm = LlmContradictionReviewer(
        StructuredLLMClient(ScriptedLLMProvider(["not json"] * 3), sleep=lambda _: None)
    )
    result = review(report, data, llm_reviewer=llm)
    assert "llm_review_unavailable" in codes(result, ReviewSeverity.INFO) and result.passed


def test_blocking_review_prevents_completion(report, data) -> None:
    run = f.run(as_of=rf.AS_OF)
    for status in (ResearchStatus.COLLECTING, ResearchStatus.PARSING, ResearchStatus.ANALYZING,
                   ResearchStatus.VALUING, ResearchStatus.REVIEWING):  # fmt: skip
        run = run.transition(status, at=f.T0)
    blocked = review(replace_claims(report, SectionKey.VALUATION, []), data)
    after = apply_review(run, blocked, at=f.T0)
    assert (
        after.status is ResearchStatus.NEEDS_INPUT
        and "missing_critical_section" in after.status_history[-1].reason
    )
    assert apply_review(run, review(report, data), at=f.T0).status is ResearchStatus.COMPLETED
    with pytest.raises(ReviewGateError):
        apply_review(f.run(), review(report, data))  # not in Reviewing
