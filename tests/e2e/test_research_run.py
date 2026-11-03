"""End-to-end autonomous research run: ticker -> sources -> financials -> LLM findings ->
valuation -> reviewer -> report, with SEC HTTP, prices and the LLM all faked deterministically."""

from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

import factories as f
from e2e_support import AS_OF, INPUTS, TENK
from equitydesk.api.app import create_app
from equitydesk.application.workflow import Collected, ResearchInputs
from equitydesk.domain.report import SectionKey
from equitydesk.domain.research import ClaimType, ResearchStatus, SupportStatus
from equitydesk.domain.watchlist import AlertKind

pytestmark = pytest.mark.e2e


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
