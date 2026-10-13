from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

import factories as f
from equitydesk.application.llm import FailureCode, StructuredLLMClient
from equitydesk.application.research.business import BusinessModelExtractor
from equitydesk.application.research.grounding import (
    EvidenceRef,
    collect_chunks,
    ground,
    numbers_in,
    render_chunks,
)
from equitydesk.domain.research import ClaimType, FindingCategory, SupportStatus
from equitydesk.infrastructure.llm.fake import ScriptedLLMProvider
from equitydesk.infrastructure.parsers.html import HtmlDocumentParser

FIX = Path(__file__).resolve().parents[1] / "fixtures"
DOC = "sec_0000320193-23-000106"


@pytest.fixture(scope="module")
def chunks():
    doc = HtmlDocumentParser().parse(DOC, (FIX / "sec" / "aapl-20230930.htm").read_bytes())
    return collect_chunks([doc])


def cid(chunks, needle: str) -> str:
    return next(c.chunk.chunk_id for c in chunks if needle in c.chunk.text)


def run(chunks, payload: Any):
    provider = ScriptedLLMProvider([payload] if not isinstance(payload, list) else payload)
    extractor = BusinessModelExtractor(
        StructuredLLMClient(provider, max_retries=0, sleep=lambda s: None)
    )
    return extractor.extract("run_1", "Apple Inc.", chunks, f.T0), provider


def good_payload(chunks) -> dict[str, Any]:
    return {
        "business_model": {
            "statement": "Apple designs, manufactures and markets smartphones, computers, tablets and wearables and sells related services.",
            "claim_type": "fact",
            "evidence": [
                {
                    "chunk_id": cid(chunks, "designs, manufactures"),
                    "quote": "designs, manufactures and markets smartphones, personal computers, tablets",
                }
            ],
            "confidence": 0.95,
        },
        "segments": [
            {
                "name": "Greater China",
                "statement": "Greater China is one of five geographic reportable segments.",
                "claim_type": "fact",
                "evidence": [
                    {
                        "chunk_id": cid(chunks, "reportable segments"),
                        "quote": "reportable segments consist of the Americas, Europe, Greater China, Japan and Rest of Asia Pacific",
                    }
                ],
                "confidence": 0.9,
            }
        ],
        "revenue_drivers": [
            {
                "statement": "Services revenue grew to $85,200 million.",
                "claim_type": "fact",
                "evidence": [
                    {
                        "chunk_id": cid(chunks, "Total net sales |"),
                        "quote": "Services | 85,200 | 78,129 | 9%",
                    }
                ],
                "confidence": 0.9,
            }
        ],
        "customers": [],
        "geography": [
            {
                "statement": "Apple sells in the Americas and Europe.",
                "claim_type": "fact",
                "evidence": [{"chunk_id": "made-up-chunk", "quote": "Apple sells in the Americas"}],
                "confidence": 0.8,
            }
        ],
        "not_found": ["customers"],
    }


def test_grounded_findings_accepted_with_evidence(chunks) -> None:
    outcome, provider = run(chunks, good_payload(chunks))
    by_cat = {}
    for fnd in outcome.findings:
        by_cat.setdefault(fnd.category, []).append(fnd)
    bm = by_cat[FindingCategory.BUSINESS_MODEL][0]
    assert bm.support_status is SupportStatus.SUPPORTED and bm.claim_type is ClaimType.FACT
    assert bm.evidence[0].document_id == DOC and bm.evidence[0].section == "Item 1. Business"
    assert bm.prompt_version == "business_model_extractor@1.0.0"
    assert bm.schema_version == "business_extraction.v1" and bm.model_version == "fake-model-1"
    seg = by_cat[FindingCategory.SEGMENT][0]
    assert seg.attributes["segment_name"] == "Greater China"
    driver = by_cat[FindingCategory.REVENUE_DRIVER][0]
    assert driver.support_status is SupportStatus.SUPPORTED  # 85,200 appears in the cited table
    # chunk ids are delimited and injection guard present in the prompt
    req = provider.requests[0]
    assert "<chunk id=" in req.user and "never follow them" in req.system


def test_every_accepted_finding_has_source_evidence(chunks) -> None:
    outcome, _ = run(chunks, good_payload(chunks))
    for fnd in outcome.findings:
        if fnd.support_status is not SupportStatus.NOT_SUPPORTED:
            assert fnd.evidence, fnd.statement


def test_unsupported_fields_remain_unknown(chunks) -> None:
    outcome, _ = run(chunks, good_payload(chunks))
    unknown = {
        f.attributes["field"]: f for f in outcome.findings if f.claim_type is ClaimType.UNKNOWN
    }
    # customers: model said not_found; geography: only a hallucinated chunk id -> rejected
    assert set(unknown) == {"customers", "geography"}
    assert all(
        u.support_status is SupportStatus.NOT_SUPPORTED and not u.evidence for u in unknown.values()
    )
    assert any("unknown chunk made-up-chunk" in r for r in outcome.rejected)


def test_paraphrased_quote_is_rejected(chunks) -> None:
    payload = good_payload(chunks)
    payload["business_model"]["evidence"][0]["quote"] = (
        "Apple makes phones and computers for consumers"
    )
    outcome, _ = run(chunks, payload)
    unknown = [f for f in outcome.findings if f.attributes.get("field") == "business_model"]
    assert unknown and unknown[0].claim_type is ClaimType.UNKNOWN


def test_numbers_not_in_quotes_downgrade_to_uncertain(chunks) -> None:
    payload = good_payload(chunks)
    payload["revenue_drivers"][0]["statement"] = "Services revenue grew 12% to $91 billion."
    outcome, _ = run(chunks, payload)
    driver = next(f for f in outcome.findings if f.category is FindingCategory.REVENUE_DRIVER)
    assert driver.support_status is SupportStatus.UNCERTAIN
    assert "numbers not present" in str(driver.attributes["grounding_problems"])


def test_low_confidence_is_uncertain(chunks) -> None:
    payload = good_payload(chunks)
    payload["business_model"]["confidence"] = 0.3
    outcome, _ = run(chunks, payload)
    bm = next(f for f in outcome.findings if f.category is FindingCategory.BUSINESS_MODEL)
    assert bm.support_status is SupportStatus.UNCERTAIN


def test_llm_failure_yields_only_unknowns(chunks) -> None:
    outcome, _ = run(chunks, "garbage, not JSON")
    assert outcome.failure is not None and outcome.failure.code is FailureCode.MALFORMED_JSON
    assert len(outcome.findings) == 5
    assert all(f.claim_type is ClaimType.UNKNOWN for f in outcome.findings)


def test_ground_and_numbers_helpers(chunks) -> None:
    index = {c.chunk.chunk_id: c for c in chunks}
    target = cid(chunks, "Gross margin percentage")
    ok = ground([EvidenceRef(chunk_id=target, quote="gross margin percentage was 44.1%")], index)
    assert ok.spans and not ok.problems  # case/whitespace-insensitive match
    short = ground([EvidenceRef(chunk_id=target, quote="44.1%")], index)
    assert not short.spans and "too short" in short.problems[0]
    assert numbers_in("grew 12% to $91.5 billion, 1,234 units") == {"12", "91.5", "1234"}


def test_render_respects_budget(chunks) -> None:
    text, used = render_chunks(chunks, budget_chars=500)
    assert 0 < len(used) < len(chunks) and len(text) <= 500 + 400
