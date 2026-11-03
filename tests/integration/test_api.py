"""FastAPI research API against a real SQLite store and the curated security master."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import factories as f
import report_fixtures as rf
from equitydesk.api.app import create_app
from equitydesk.bootstrap import build_container
from equitydesk.config import Settings
from equitydesk.reporting.composer import compose_report
from equitydesk.reporting.reviewer import review_report

pytestmark = pytest.mark.integration
NOW = f.dt(2024, 7, 1, 12)


@pytest.fixture
def ctx(tmp_path: Path):
    container = build_container(Settings(), db_path=tmp_path / "api.db")
    container.research.clock = lambda: NOW
    return container, TestClient(create_app(container.research))


def create(client, **body):
    return client.post(
        "/research-runs", json={"query": "AAPL", "as_of": "2024-06-01T00:00:00Z"} | body
    )


def test_health_and_resolve(ctx) -> None:
    _, client = ctx
    assert client.get("/healthz").json()["status"] == "ok"
    r = client.get("/companies/resolve", params={"q": "AAPL"}).json()
    assert r["status"] == "resolved" and r["company"]["company_id"] == "co_apple"
    r = client.get("/companies/resolve", params={"q": "ASML"}).json()
    assert r["status"] == "ambiguous" and len(r["candidates"]) == 2


def test_create_and_read_run(ctx) -> None:
    _, client = ctx
    resp = create(client)
    assert resp.status_code == 201
    run = resp.json()
    assert run["status"] == "Created" and run["as_of"].startswith("2024-06-01")
    assert client.get(f"/research-runs/{run['research_run_id']}").json() == run
    assert client.get("/companies/co_apple").json()["name"] == "Apple Inc."
    assert [r["research_run_id"] for r in client.get("/companies/co_apple/runs").json()] == [
        run["research_run_id"]
    ]


def test_structured_errors(ctx) -> None:
    _, client = ctx
    bad = client.post("/research-runs", json={"as_of": "2024-06-01T00:00:00Z"})
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "validation_error"
    assert bad.json()["error"]["details"]["errors"][0]["loc"] == ["body", "query"]
    future = create(client, as_of="2025-01-01T00:00:00Z")
    assert future.status_code == 422 and "future" in future.json()["error"]["message"]
    naive = create(client, as_of="2024-06-01T00:00:00")
    assert naive.status_code == 422
    ambiguous = create(client, query="ASML")
    assert ambiguous.status_code == 409 and ambiguous.json()["error"]["code"] == "ambiguous"
    assert {c["security_id"] for c in ambiguous.json()["error"]["details"]["candidates"]} == {
        "sec_asml_xams",
        "sec_asml_xnas",
    }
    assert create(client, query="ZZZZZZ").status_code == 404
    missing = client.get("/research-runs/run_missing")
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "not_found"
    extra = client.post("/research-runs", json={"query": "AAPL", "unexpected": 1})
    assert extra.status_code == 422
    assert client.get("/research-runs/x/report", params={"format": "pdf"}).status_code == 422


def test_point_in_time_documents_financials_valuations_report(ctx) -> None:
    container, client = ctx
    repos = container.repos
    run_id = create(client).json()["research_run_id"]
    run = repos.runs.get_run(run_id)
    repos.documents.add_document(rf.DOC)
    repos.documents.add_document(rf.Q2)
    repos.documents.add_document(f.document(document_id="doc_future", official_id="x-future",
                                            published_at=f.dt(2024, 8, 2), content_hash="b" * 64))  # fmt: skip
    docs = client.get(f"/research-runs/{run_id}/documents").json()
    assert [d["document_id"] for d in docs] == ["doc_10k_2023", "doc_10q_2024q2"]  # no future doc

    repos.facts.add_facts([
        f.fact(fact_id="rev23", value=383.0),
        f.fact(fact_id="rev22", value=394.0, period_start=date(2021, 9, 26), period_end=date(2022, 9, 24),
               fiscal_year=2022, available_at=f.dt(2022, 10, 28)),
        f.fact(fact_id="rev24", value=391.0, period_start=date(2023, 10, 1), period_end=date(2024, 9, 28),
               fiscal_year=2024, available_at=f.dt(2024, 11, 1)),
    ])  # fmt: skip
    fin = client.get(f"/research-runs/{run_id}/financials").json()
    assert {x["fact_id"] for x in fin["facts"]} == {"rev23", "rev22"}  # FY2024 not yet available
    growth = next(
        m for m in fin["metrics"] if m["name"] == "revenue_growth" and m["status"] == "ok"
    )
    assert growth["value"] == pytest.approx(383 / 394 - 1)

    vals = rf.valuations(run_id)
    for v in vals:
        repos.valuations.add_valuation(v)
    got = client.get(f"/research-runs/{run_id}/valuations").json()
    assert [v["method"] for v in got] == ["multiples", "dcf", "scenario"]

    assert client.get(f"/research-runs/{run_id}/report").status_code == 404
    data = rf.inputs() | {
        "run": run,
        "valuations": vals,
        "findings": [x.model_copy(update={"research_run_id": run_id}) for x in rf.findings()],
    }
    data["thesis"] = rf.thesis(vals)
    report = compose_report(**data)
    repos.reports.add_report(report)
    repos.reports.add_review(review_report(report, data["documents"], data["metrics"], vals))
    body = client.get(f"/research-runs/{run_id}/report").json()
    assert body["report"]["report_id"] == report.report_id and body["review_passed"] is True
    md = client.get(f"/research-runs/{run_id}/report", params={"format": "markdown"})
    assert md.headers["content-type"].startswith("text/markdown") and md.text.startswith(
        "# Apple Inc."
    )
