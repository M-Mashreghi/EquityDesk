"""Report composer: citations, metric/valuation links, labels and schema stability."""

from __future__ import annotations

import pytest

import factories as f
import report_fixtures as rf
from equitydesk.domain.provenance import FutureLeakageError
from equitydesk.domain.report import (
    REPORT_SCHEMA_VERSION,
    SECTION_ORDER,
    ResearchReport,
    SectionKey,
)
from equitydesk.domain.research import ClaimType, SupportStatus
from equitydesk.reporting.composer import compose_report
from equitydesk.reporting.render import render_markdown


@pytest.fixture(scope="module")
def report() -> ResearchReport:
    return compose_report(**rf.inputs())


def test_schema_is_stable(report) -> None:
    assert report.schema_version == REPORT_SCHEMA_VERSION == "report.v1"
    assert tuple(s.key for s in report.sections) == SECTION_ORDER
    assert [k.value for k in SECTION_ORDER] == [
        "company_overview", "financial_analysis", "guidance_and_strategy", "valuation",
        "investment_thesis", "risks", "catalysts", "scenarios", "open_questions",
    ]  # fmt: skip
    claim_fields = set(ResearchReport.model_json_schema()["$defs"]["ReportClaim"]["properties"])
    assert {
        "claim_type",
        "support_status",
        "citation_ids",
        "metric_ids",
        "valuation_ids",
    } <= claim_fields
    assert ResearchReport.model_validate_json(report.model_dump_json()) == report
    assert compose_report(**rf.inputs()).report_id == report.report_id  # deterministic


def test_material_facts_are_cited_or_linked(report) -> None:
    for claim in report.claims:
        if claim.support_status is SupportStatus.SUPPORTED and claim.claim_type in (
            ClaimType.FACT, ClaimType.INFERENCE,
        ):  # fmt: skip
            assert claim.has_support, claim.text
    overview = report.section(SectionKey.COMPANY_OVERVIEW).claims
    bm = next(c for c in overview if "fnd_bm" in c.finding_ids)
    cit = next(c for c in report.citations if c.citation_id == bm.citation_ids[0])
    assert (cit.document_id, cit.official_id, cit.content_hash) == (
        "doc_10k_2023",
        "0000320193-23-000106",
        f.HASH,
    )
    assert (
        cit.quote == "designs, manufactures and markets smartphones"
        and cit.chunk_id == "doc_10k_2023:s1:p1"
    )
    unknown = next(c for c in overview if "fnd_seg_unknown" in c.finding_ids)
    assert unknown.claim_type is ClaimType.UNKNOWN and not unknown.citation_ids


def test_calculations_link_to_metric_ids(report) -> None:
    fin = report.section(SectionKey.FINANCIAL_ANALYSIS).claims
    growth = next(c for c in fin if "Revenue growth" in c.text)
    assert growth.metric_ids == ("m:co_apple:revenue_growth:2023-09-30",)  # latest period only
    assert "-2.8%" in growth.text and growth.values == {"revenue_growth": -0.028}
    assert any("44.1%" in c.text for c in fin)
    roic = next(c for c in fin if "ROIC" in c.text)
    assert roic.claim_type is ClaimType.UNKNOWN and "invested_capital" in roic.text


def test_valuation_and_scenarios_link_to_valuation_ids(report) -> None:
    vals = {v.valuation_id for v in rf.valuations()}
    val = report.section(SectionKey.VALUATION).claims
    assert any("ev_sales" in c.text and "peer set tech:v1" in c.text for c in val)
    dcf = next(c for c in val if c.text.startswith("DCF"))
    assert "18.78 per share" in dcf.text and "not a price target" in dcf.text
    assert all(set(c.valuation_ids) <= vals and c.valuation_ids for c in val)
    scn = report.section(SectionKey.SCENARIOS).claims
    bull_assumption = next(c for c in scn if c.text.startswith("Bull case assumption"))
    assert (
        bull_assumption.claim_type is ClaimType.ASSUMPTION
        and bull_assumption.assumption_source == "scenario:bull"
    )
    assert any("Probability-weighted" in c.text for c in scn)
    assert any("+17.4% vs. the observed price" in c.text for c in scn)  # 18.784 / 16 - 1


def test_thesis_points_rest_on_validated_objects(report) -> None:
    thesis = report.section(SectionKey.INVESTMENT_THESIS)
    texts = [c.text for c in thesis.claims]
    assert "Evidence for: Services growth can offset hardware cyclicality." in texts
    assert "Evidence against: Supply chain concentration is a material risk." in texts
    services = next(c for c in thesis.claims if "Services" in c.text)
    assert services.citation_ids and services.metric_ids == ("m:co_apple:gross_margin:2023-09-30",)
    # the point whose only reference does not exist is not presented as thesis
    assert not any("double" in t for t in texts)
    oq = report.section(SectionKey.OPEN_QUESTIONS).claims
    assert any(
        "Market share will double" in c.text and c.claim_type is ClaimType.UNKNOWN for c in oq
    )
    assert any("fnd_missing" in n for n in thesis.notes)


def test_future_document_cannot_be_cited() -> None:
    data = rf.inputs()
    data["documents"] = [rf.DOC, rf.Q2.model_copy(update={"published_at": f.dt(2024, 7, 1)})]
    with pytest.raises(FutureLeakageError):
        compose_report(**data)


def test_empty_sections_are_explicit() -> None:
    data = rf.inputs() | {"findings": [], "metrics": [], "valuations": [], "thesis": []}
    report = compose_report(**data)
    assert all("no validated content for this section" in s.notes for s in report.sections)


def test_markdown_render(report) -> None:
    md = render_markdown(report)
    assert md.startswith("# Apple Inc. — Research Report")
    for section in report.sections:
        assert f"## {section.title}" in md
    assert "**Fact**" in md and "**Assumption**" in md and "**Unknown**" in md
    assert "`m:co_apple:revenue_growth:2023-09-30`" in md
    assert (
        '0000320193-23-000106; Item 1, doc_10k_2023:s1:p1): "designs, manufactures and markets smartphones"'
        in md
    )
