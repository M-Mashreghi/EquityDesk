from __future__ import annotations

from typing import Any

import pytest

import factories as f
from equitydesk.application.llm import StructuredLLMClient
from equitydesk.application.research.changes import ChangeAnalyzer, DocumentChunks
from equitydesk.application.research.grounding import collect_chunks
from equitydesk.domain.research import ClaimType, FindingCategory, SupportStatus
from equitydesk.infrastructure.llm.fake import ScriptedLLMProvider
from equitydesk.infrastructure.parsers.text import PlainTextParser

PRIOR_TEXT = b"""# Q3 outlook
Management expects revenue for the December quarter to grow low single digits year over year.

# Strategy
We will continue to prioritize hardware unit growth in emerging markets.

# Tone
We are confident in our product pipeline."""

CURRENT_TEXT = b"""# Q4 outlook
Management expects revenue for the March quarter to be similar to the year-ago quarter.

# Strategy
Our priority is growing the installed base and expanding services revenue.

# Tone
We remain cautious given the macroeconomic environment.

# Notes
Services revenue reached an all-time high."""


@pytest.fixture(scope="module")
def docs():
    prior = PlainTextParser().parse("ir_prior", PRIOR_TEXT)
    current = PlainTextParser().parse("ir_current", CURRENT_TEXT)
    return (
        DocumentChunks("ir_prior", f.dt(2023, 8, 3), collect_chunks([prior])),
        DocumentChunks("ir_current", f.dt(2023, 11, 2), collect_chunks([current])),
    )


def cid(doc: DocumentChunks, needle: str) -> str:
    return next(c.chunk.chunk_id for c in doc.chunks if needle in c.chunk.text)


def analyze(docs, payload: Any):
    provider = ScriptedLLMProvider([payload])
    analyzer = ChangeAnalyzer(StructuredLLMClient(provider, max_retries=0, sleep=lambda s: None))
    return analyzer.analyze("run_1", "Apple", docs[0], docs[1], f.T0)


def change(docs, **kw) -> dict[str, Any]:
    prior, current = docs
    data = {
        "change_type": "guidance",
        "topic": "next-quarter revenue",
        "direction": "lowered",
        "before": {
            "statement": "Low single-digit growth expected.",
            "evidence": [
                {
                    "chunk_id": cid(prior, "December quarter"),
                    "quote": "expects revenue for the December quarter to grow low single digits",
                }
            ],
        },
        "after": {
            "statement": "Revenue expected to be flat year over year.",
            "evidence": [
                {
                    "chunk_id": cid(current, "March quarter"),
                    "quote": "expects revenue for the March quarter to be similar to the year-ago quarter",
                }
            ],
        },
        "summary": "Next-quarter revenue guidance moved from low single-digit growth to flat.",
        "confidence": 0.85,
    }
    data.update(kw)
    return data


def test_guidance_change_with_before_after_evidence(docs) -> None:
    out = analyze(docs, {"changes": [change(docs)], "not_found": []})
    (fnd,) = out.findings
    assert fnd.category is FindingCategory.GUIDANCE_CHANGE and fnd.claim_type is ClaimType.INFERENCE
    assert fnd.support_status is SupportStatus.SUPPORTED
    assert fnd.attributes["direction"] == "lowered"
    assert fnd.attributes["before_document_id"] == "ir_prior"
    assert fnd.attributes["after_chunk_ids"] == [cid(docs[1], "March quarter")]
    assert {e.document_id for e in fnd.evidence} == {"ir_prior", "ir_current"}


def test_invented_guidance_is_rejected(docs) -> None:
    invented = change(docs, after={"statement": "Guidance raised to 10% growth.",
                                   "evidence": [{"chunk_id": cid(docs[1], "March quarter"), "quote": "expects revenue to grow 10% next quarter"}]})  # fmt: skip
    out = analyze(docs, {"changes": [invented], "not_found": []})
    assert out.findings == []
    assert any("no grounded 'after' evidence" in r for r in out.rejected)


def test_before_evidence_must_come_from_prior_document(docs) -> None:
    wrong_side = change(
        docs,
        before={
            "statement": "x",
            "evidence": [
                {
                    "chunk_id": cid(docs[1], "March quarter"),
                    "quote": "expects revenue for the March quarter",
                }
            ],
        },
    )
    out = analyze(docs, {"changes": [wrong_side], "not_found": []})
    assert out.findings == [] and any("'before'" in r for r in out.rejected)


def test_ambiguous_change_is_uncertain(docs) -> None:
    tone = change(
        docs,
        change_type="tone",
        topic="outlook confidence",
        direction="unclear",
        before={
            "statement": "Confident",
            "evidence": [
                {
                    "chunk_id": cid(docs[0], "confident"),
                    "quote": "We are confident in our product pipeline",
                }
            ],
        },
        after={
            "statement": "Cautious",
            "evidence": [
                {
                    "chunk_id": cid(docs[1], "cautious"),
                    "quote": "We remain cautious given the macroeconomic environment",
                }
            ],
        },
        summary="Management tone shifted from confident to cautious.",
    )
    out = analyze(docs, {"changes": [tone], "not_found": []})
    (fnd,) = out.findings
    assert fnd.category is FindingCategory.MANAGEMENT_TONE
    assert fnd.support_status is SupportStatus.UNCERTAIN


def test_guidance_from_non_forward_looking_text_is_uncertain(docs) -> None:
    g = change(docs, direction="introduced", before=None,
               after={"statement": "Services at a record.", "evidence": [{"chunk_id": cid(docs[1], "all-time high"), "quote": "Services revenue reached an all-time high"}]})  # fmt: skip
    out = analyze(docs, {"changes": [g], "not_found": []})
    (fnd,) = out.findings
    assert fnd.support_status is SupportStatus.UNCERTAIN
    assert "forward-looking" in str(fnd.attributes["grounding_problems"])


def test_introduced_strategy_needs_only_after(docs) -> None:
    s = change(docs, change_type="strategy", topic="services focus", direction="shifted",
               before={"statement": "Hardware units", "evidence": [{"chunk_id": cid(docs[0], "emerging markets"), "quote": "prioritize hardware unit growth in emerging markets"}]},
               after={"statement": "Installed base", "evidence": [{"chunk_id": cid(docs[1], "installed base"), "quote": "growing the installed base and expanding services revenue"}]},
               summary="Strategic priority shifted from hardware units to installed base and services.")  # fmt: skip
    out = analyze(docs, {"changes": [s], "not_found": []})
    assert out.findings[0].support_status is SupportStatus.SUPPORTED


def test_invented_numbers_downgrade(docs) -> None:
    g = change(docs, summary="Guidance cut by 5% from 3% growth.")
    out = analyze(docs, {"changes": [g], "not_found": []})
    assert out.findings[0].support_status is SupportStatus.UNCERTAIN


def test_document_order_enforced(docs) -> None:
    analyzer = ChangeAnalyzer(StructuredLLMClient(ScriptedLLMProvider([]), max_retries=0))
    with pytest.raises(ValueError):
        analyzer.analyze("r", "Apple", docs[1], docs[0], f.T0)


def test_llm_failure_returns_no_changes(docs) -> None:
    out = analyze(docs, "nope")
    assert out.findings == [] and out.failure is not None
