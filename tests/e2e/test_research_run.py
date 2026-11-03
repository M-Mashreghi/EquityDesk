"""End-to-end autonomous research run: ticker -> sources -> financials -> LLM findings ->
valuation -> reviewer -> report, with SEC HTTP, prices and the LLM all faked deterministically."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

import factories as f
from equitydesk.api.app import create_app
from equitydesk.application.llm import LLMRequest, StructuredLLMClient
from equitydesk.application.research.grounding import collect_chunks
from equitydesk.application.workflow import Collected, ResearchInputs
from equitydesk.bootstrap import build_container, build_workflow
from equitydesk.config import Settings
from equitydesk.domain.market import MarketObservation
from equitydesk.domain.report import SectionKey
from equitydesk.domain.research import ClaimType, ResearchStatus, SupportStatus
from equitydesk.domain.valuation import DcfAssumptions, ScenarioCase, ScenarioName
from equitydesk.domain.watchlist import AlertKind
from equitydesk.infrastructure.llm.fake import ScriptedLLMProvider
from equitydesk.infrastructure.market_data.static import StaticMarketData
from equitydesk.infrastructure.parsers.html import HtmlDocumentParser
from equitydesk.infrastructure.sources.http import HttpFetcher
from equitydesk.infrastructure.sources.sec_edgar import SecEdgarAdapter
from sec_mocks import FIXTURES, sec_transport

pytestmark = pytest.mark.e2e

AS_OF = "2024-01-15T00:00:00Z"
TENK = "sec_0000320193-23-000106"
CHUNKS = collect_chunks(
    [HtmlDocumentParser().parse(TENK, (FIXTURES / "aapl-20230930.htm").read_bytes())]
)


def cid(needle: str) -> str:
    return next(c.chunk.chunk_id for c in CHUNKS if needle in c.chunk.text)


def ev(needle: str, quote: str) -> list[dict[str, str]]:
    return [{"chunk_id": cid(needle), "quote": quote}]


BUSINESS = {
    "business_model": {"statement": "Apple designs, manufactures and markets smartphones, computers, tablets and wearables and sells related services.",
                       "claim_type": "fact", "evidence": ev("designs, manufactures", "designs, manufactures and markets smartphones, personal computers, tablets"),
                       "confidence": 0.95},
    "segments": [{"name": "Greater China", "statement": "Greater China is one of the geographic reportable segments.", "claim_type": "fact",
                  "evidence": ev("reportable segments", "reportable segments consist of the Americas, Europe, Greater China, Japan and Rest of Asia Pacific"),
                  "confidence": 0.9}],
    "revenue_drivers": [{"statement": "Services such as advertising, AppleCare and cloud services contribute revenue.", "claim_type": "fact",
                         "evidence": ev("Services include", "Services include advertising, AppleCare, cloud services"), "confidence": 0.85}],
    "customers": [],
    "geography": [],
    "not_found": ["customers", "geography"],
}  # fmt: skip
RISKS = {
    "risks": [
        {"title": "Supply chain concentration", "statement": "Manufacturing depends on outsourcing partners concentrated in Asia.",
         "category": "supply_chain", "horizon": "medium", "claim_type": "inference",
         "evidence": ev("outsourcing partners", "manufacturing is performed in whole or in part by outsourcing partners"), "confidence": 0.85},
        {"title": "Price competition", "statement": "Markets are characterized by aggressive price competition.",
         "category": "competition", "horizon": "near", "claim_type": "fact",
         "evidence": ev("substantial competition", "aggressive price competition and rapid technological change"), "confidence": 0.8},
        {"title": "Invented risk", "statement": "A regulator will ban the iPhone.", "category": "regulatory", "horizon": "near",
         "claim_type": "fact", "evidence": [{"chunk_id": cid("outsourcing partners"), "quote": "a regulator will ban the iPhone next year"}],
         "confidence": 0.9},
    ],
    "catalysts": [
        {"title": "Services growth", "statement": "Services revenue grew while iPhone revenue declined.", "category": "product",
         "horizon": "near", "claim_type": "inference",
         "evidence": ev("Services", "Services include advertising, AppleCare, cloud services"), "confidence": 0.7},
    ],
    "open_questions": [
        {"question": "How durable is Services growth?", "why_it_matters": "Services carry higher margins.",
         "evidence": ev("Services include", "Services include advertising, AppleCare, cloud services")},
    ],
}  # fmt: skip


def llm_router(request: LLMRequest) -> Any:
    if "makes money" in request.system:
        return BUSINESS
    if "investment risks" in request.system:
        return RISKS
    if "contradict" in request.system:
        return {"contradictions": []}
    raise AssertionError(f"unexpected prompt: {request.system[:60]}")


def assumptions(name: str, g: float, m: float) -> DcfAssumptions:
    return DcfAssumptions(
        assumption_set_id=f"co_apple:{name}", version=1, source=f"scenario:{name}",
        revenue_growth=(g,) * 5, ebit_margin=(m,) * 5, tax_rate=0.16, da_pct_revenue=0.03,
        capex_pct_revenue=0.03, nwc_pct_incremental_revenue=0.0, discount_rate=0.09, terminal_growth=0.03,
        rationale={"revenue_growth": "analyst", "ebit_margin": "analyst", "discount_rate": "CAPM", "terminal_growth": "nominal GDP"},
    )  # fmt: skip


INPUTS = ResearchInputs(scenarios=(
    ScenarioCase(name=ScenarioName.BULL, assumptions=assumptions("bull", 0.08, 0.33), narrative="Services mix lifts margins", probability=0.25),
    ScenarioCase(name=ScenarioName.BASE, assumptions=assumptions("base", 0.04, 0.30), narrative="Steady growth", probability=0.5),
    ScenarioCase(name=ScenarioName.BEAR, assumptions=assumptions("bear", -0.02, 0.26), narrative="Hardware decline", probability=0.25),
))  # fmt: skip
PRICES = [
    MarketObservation(security_id="sec_aapl_xnas", price=185.0, currency="USD", observed_at=f.dt(2024, 1, 12, 21),
                      price_source="manual:analyst", shares_outstanding=15_552_752_000.0,
                      shares_source="10-K cover 0000320193-23-000106", shares_as_of=date(2023, 10, 20)),
    MarketObservation(security_id="sec_aapl_xnas", price=99.0, currency="USD", observed_at=f.dt(2024, 3, 1, 21),
                      price_source="manual:analyst"),  # after as_of: must never be used
]  # fmt: skip


def sec(transport: httpx.BaseTransport | None = None) -> SecEdgarAdapter:
    fetcher = HttpFetcher("EquityDesk tests research@example.com", 1000, max_retries=1,
                          backoff_seconds=0, transport=transport or sec_transport(), sleep=lambda _s: None)  # fmt: skip
    return SecEdgarAdapter(fetcher, clock=lambda: f.dt(2024, 1, 16))


@pytest.fixture
def env(tmp_path: Path):
    settings = Settings(data_dir=tmp_path / "data")
    container = build_container(settings, db_path=tmp_path / "e2e.db")
    container.research.clock = lambda: f.dt(2024, 1, 16)
    container.watchlist.clock = lambda: f.dt(2024, 1, 16)

    def make(
        script: Any = None, transport: httpx.BaseTransport | None = None, llm_review: bool = True
    ):
        provider = ScriptedLLMProvider([llm_router] * 10 if script is None else script)
        wf = build_workflow(container, StaticMarketData(PRICES), sec(transport), provider)
        wf.llm = StructuredLLMClient(provider, max_retries=0, sleep=lambda _s: None)
        wf.llm_review = llm_review
        wf.clock = lambda: f.dt(2024, 1, 16)
        return wf, provider

    return container, make


def test_happy_path_ticker_to_cited_report(env) -> None:
    container, make = env
    workflow, provider = make()
    client = TestClient(create_app(container.research, container.watchlist, workflow))
    container.repos.companies.save_company(f.company())
    client.put("/watchlist/co_apple", json={"refresh_interval_hours": 24})

    run_id = client.post("/research-runs", json={"query": "AAPL", "as_of": AS_OF}).json()[
        "research_run_id"
    ]
    resp = client.post(f"/research-runs/{run_id}/execute", json=INPUTS.model_dump(mode="json"))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["run"]["status"] == "Completed", body
    assert body["review_passed"] is True
    history = [h["status"] for h in body["run"]["status_history"]]
    assert history == ["Collecting", "Parsing", "Analyzing", "Valuing", "Reviewing", "Completed"]
    assert any(
        "10-K 0000320193-22-000108" in n for n in body["notes"]
    )  # partial collection reported

    repos = container.repos
    run = repos.runs.get_run(run_id)
    assert run.snapshot_id and run.model_version == "fake-model-1"
    assert set(run.prompt_versions.values()) >= {
        "business_model_extractor@1.0.0",
        "risk_catalyst_open_questions@1.0.0",
    }

    # point-in-time invariants
    as_of = run.as_of
    snapshot_docs = [repos.documents.get_document(d) for d in repos.snapshots.member_ids(run_id)]
    assert [d.document_id for d in snapshot_docs] == [TENK]
    assert all(d.published_at <= as_of for d in snapshot_docs)
    assert all(fct.available_at <= as_of for fct in repos.facts.facts_for_company("co_apple"))
    scen = next(
        v for v in repos.valuations.valuations_for_run(run_id) if v.method.value == "scenario"
    )
    assert scen.inputs["price"]["value"] == 185.0  # the later 99.0 print is never used
    assert scen.inputs["facts"]["base_revenue"]["value"] == 383_285_000_000.0

    # citation invariants
    report = repos.reports.reports_for_run(run_id)[-1]
    assert report.as_of == as_of and all(c.published_at <= as_of for c in report.citations)
    assert {c.document_id for c in report.citations} == {TENK}
    for claim in report.claims:
        if (
            claim.claim_type in (ClaimType.FACT, ClaimType.INFERENCE)
            and claim.support_status is SupportStatus.SUPPORTED
        ):
            assert claim.has_support, claim.text
    risks = [c.text for c in report.section(SectionKey.RISKS).claims]
    assert any("Supply chain concentration" in t for t in risks)
    assert not any(
        "ban the iPhone" in t for t in risks
    )  # ungrounded claim never reaches the report
    thesis = report.section(SectionKey.INVESTMENT_THESIS).claims
    assert any("Catalyst: Services growth" in c.text for c in thesis)
    assert any("base-case scenario value" in c.text for c in thesis)
    fin = [
        c
        for c in report.section(SectionKey.FINANCIAL_ANALYSIS).claims
        if c.claim_type is ClaimType.FACT
    ]
    assert fin and all(c.metric_ids for c in fin)

    # API views over the finished run
    md = client.get(f"/research-runs/{run_id}/report", params={"format": "markdown"}).text
    assert "Supply chain concentration" in md and "0000320193-23-000106" in md
    assert client.get(f"/research-runs/{run_id}/report").json()["review_passed"] is True
    alerts = client.get("/alerts", params={"company_id": "co_apple"}).json()
    done = next(a for a in alerts if a["kind"] == AlertKind.RUN_COMPLETED.value)
    assert done["research_run_id"] == run_id and done["report_id"] == report.report_id
    assert done["sources"][0]["official_id"] == "0000320193-23-000106"
    assert (
        client.post(f"/research-runs/{run_id}/execute", json={}).status_code == 422
    )  # not re-runnable


def test_missing_assumptions_needs_input(env) -> None:
    container, make = env
    workflow, _ = make()
    run = container.research.create_run("AAPL", f.dt(2024, 1, 15))
    outcome = workflow.execute(run.research_run_id, ResearchInputs())
    assert outcome.run.status is ResearchStatus.NEEDS_INPUT
    assert "scenario assumptions" in outcome.run.status_history[-1].reason
    assert container.repos.reports.reports_for_run(run.research_run_id) == []


def test_source_outage_blocks_run(env) -> None:
    container, make = env
    workflow, _ = make(transport=httpx.MockTransport(lambda r: httpx.Response(503)))
    run = container.research.create_run("AAPL", f.dt(2024, 1, 15))
    outcome = workflow.execute(run.research_run_id, INPUTS)
    assert outcome.run.status is ResearchStatus.BLOCKED_BY_SOURCE
    assert "filing index unavailable" in outcome.run.status_history[-1].reason


def test_llm_failure_never_invents_and_blocks_completion(env) -> None:
    container, make = env
    workflow, _ = make(script=["not json"] * 10, llm_review=False)
    run = container.research.create_run("AAPL", f.dt(2024, 1, 15))
    outcome = workflow.execute(run.research_run_id, INPUTS)
    assert outcome.run.status is ResearchStatus.NEEDS_INPUT and outcome.review_passed is False
    assert "missing_critical_section" in outcome.run.status_history[-1].reason
    findings = container.repos.findings.findings_for_run(run.research_run_id)
    assert findings and all(x.claim_type is ClaimType.UNKNOWN for x in findings)
    assert any("llm business model extraction failed" in n for n in outcome.notes)


def test_future_document_from_a_source_fails_the_run(env) -> None:
    container, make = env
    workflow, _ = make()

    class LeakySource:
        def collect(self, company, as_of):
            doc = f.document(
                document_id="leak", published_at=f.dt(2024, 2, 1), retrieved_at=f.dt(2024, 2, 2)
            )
            return Collected(documents=[(doc, b"future")])

    workflow.filings = LeakySource()
    run = container.research.create_run("AAPL", f.dt(2024, 1, 15))
    outcome = workflow.execute(run.research_run_id, INPUTS)
    assert outcome.run.status is ResearchStatus.FAILED
    assert "FutureLeakageError" in outcome.run.status_history[-1].reason
    assert container.repos.documents.documents_for_company("co_apple") == []


def test_identical_inputs_reproduce_identical_results(env) -> None:
    container, make = env
    ids = []
    for _ in range(2):
        workflow, _ = make()
        run = container.research.create_run("AAPL", f.dt(2024, 1, 15))
        assert workflow.execute(run.research_run_id, INPUTS).run.status is ResearchStatus.COMPLETED
        ids.append(run.research_run_id)
    a, b = (container.research.run_state(i) for i in ids)
    assert a.run.snapshot_id == b.run.snapshot_id
    diff = container.research.diff(*ids)
    assert not (
        diff.sources.changed
        or diff.kpis
        or diff.valuation
        or diff.risks.changed
        or diff.thesis.changed
    )
