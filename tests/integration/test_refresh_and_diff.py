"""Refresh creates a new immutable run for a later as_of; the diff shows what changed."""

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
from equitydesk.domain.research import FindingCategory
from equitydesk.reporting.composer import ThesisPoint, compose_report

pytestmark = pytest.mark.integration
NOW = f.dt(2025, 1, 15, 12)


@pytest.fixture
def ctx(tmp_path: Path):
    container = build_container(Settings(), db_path=tmp_path / "hist.db")
    container.research.clock = lambda: NOW
    return container, TestClient(create_app(container.research))


def seed_run(container, run_id: str, *, risks: list[str], dcf_growth: float, thesis: str) -> None:
    repos = container.repos
    run = repos.runs.get_run(run_id)
    findings = [
        f.finding(finding_id=f"{run_id}:{i}", research_run_id=run_id, category=FindingCategory.RISK,
                  attributes={"title": t}, statement=f"{t} could hurt results.")
        for i, t in enumerate(risks)
    ]  # fmt: skip
    repos.findings.add_findings(findings)
    vals = rf.valuations(run_id)
    dcf = vals[1]
    if dcf_growth != 0.10:
        from equitydesk.analytics.dcf import run_dcf

        a = rf.assumptions("base", dcf_growth, 0.20)
        dcf = run_dcf(rf.SCEN_BASE, a).to_valuation_run(run_id, run.as_of, f.T0)
        vals[1] = dcf
    for v in vals:
        repos.valuations.add_valuation(v)
    report = compose_report(
        run=run, company=f.company(), documents=[rf.DOC, rf.Q2], findings=findings,
        valuations=vals, thesis=[ThesisPoint(statement=thesis, stance="for", valuation_ids=(dcf.valuation_id,))],
    )  # fmt: skip
    repos.reports.add_report(report)


def test_refresh_keeps_previous_run_and_diff_reports_changes(ctx) -> None:
    container, client = ctx
    repos = container.repos
    repos.companies.save_company(f.company())
    repos.documents.add_document(rf.DOC)
    repos.documents.add_document(rf.Q2)
    later_10k = f.document(document_id="doc_10k_2024", title="Apple 10-K FY2024", official_id="0000320193-24-000123",
                           published_at=f.dt(2024, 11, 1, 10), period_end=date(2024, 9, 28), content_hash="c" * 64)  # fmt: skip
    repos.documents.add_document(later_10k)
    repos.facts.add_facts([
        f.fact(fact_id="rev22", value=394.0, period_start=date(2021, 9, 26), period_end=date(2022, 9, 24),
               fiscal_year=2022, available_at=f.dt(2022, 10, 28)),
        f.fact(fact_id="rev23", value=383.0),
        f.fact(fact_id="rev24", value=391.0, period_start=date(2023, 10, 1), period_end=date(2024, 9, 28),
               fiscal_year=2024, available_at=f.dt(2024, 11, 1, 10)),
    ])  # fmt: skip

    old = client.post(
        "/research-runs", json={"query": "AAPL", "as_of": "2024-06-01T00:00:00Z"}
    ).json()
    old_id = old["research_run_id"]
    seed_run(container, old_id, risks=["Supply chain concentration", "FX headwinds"], dcf_growth=0.10,
             thesis="Services offset hardware.")  # fmt: skip
    before = repos.runs.get_run(old_id)

    resp = client.post(f"/research-runs/{old_id}/refresh", json={"as_of": "2024-12-01T00:00:00Z"})
    assert resp.status_code == 201
    new = resp.json()
    new_id = new["research_run_id"]
    assert new_id != old_id and new["refreshed_from"] == old_id and new["status"] == "Created"
    assert new["company_id"] == "co_apple" and new["security_id"] == old["security_id"]
    seed_run(container, new_id, risks=["Supply chain concentration", "Regulatory pressure on App Store"],
             dcf_growth=0.12, thesis="Services offset hardware; AI cycle adds upside.")  # fmt: skip

    assert repos.runs.get_run(old_id) == before  # previous run untouched
    assert [d["document_id"] for d in client.get(f"/research-runs/{old_id}/documents").json()] == [
        "doc_10k_2023", "doc_10q_2024q2",
    ]  # fmt: skip

    diff = client.get(f"/research-runs/{new_id}/diff", params={"against": old_id}).json()
    assert diff["old_run_id"] == old_id and diff["new_run_id"] == new_id
    assert diff["sources"]["added"] == ["2024-11-01 10-K Apple 10-K FY2024 [doc_10k_2024]"]
    assert diff["sources"]["removed"] == []
    growth = next(k for k in diff["kpis"] if k["key"] == "revenue_growth")
    assert (growth["old_period"], growth["new_period"]) == ("2023-09-30", "2024-09-28")
    assert growth["old"] == pytest.approx(383 / 394 - 1) and growth["new"] == pytest.approx(
        391 / 383 - 1
    )
    assert growth["delta"] == pytest.approx(growth["new"] - growth["old"])
    assert diff["risks"] == {
        "added": ["Regulatory pressure on App Store"],
        "removed": ["FX headwinds"],
    }
    assert diff["thesis"]["added"] == [
        "Evidence for: Services offset hardware; AI cycle adds upside."
    ]
    dcf = next(v for v in diff["valuation"] if v["key"] == "dcf:value_per_share")
    assert dcf["new"] > dcf["old"] and diff["assumptions_changed"] == ["dcf"]
    # order-independent: diffing the other way round gives the same old/new sides
    assert client.get(f"/research-runs/{old_id}/diff", params={"against": new_id}).json() == diff


def test_refresh_rules(ctx) -> None:
    _, client = ctx
    rid = client.post(
        "/research-runs", json={"query": "AAPL", "as_of": "2024-06-01T00:00:00Z"}
    ).json()["research_run_id"]
    earlier = client.post(f"/research-runs/{rid}/refresh", json={"as_of": "2024-05-01T00:00:00Z"})
    assert earlier.status_code == 422 and "later as_of" in earlier.json()["error"]["message"]
    future = client.post(f"/research-runs/{rid}/refresh", json={"as_of": "2026-01-01T00:00:00Z"})
    assert future.status_code == 422
    assert client.post("/research-runs/run_missing/refresh", json={}).status_code == 404
    default_now = client.post(f"/research-runs/{rid}/refresh", json={})
    assert default_now.status_code == 201 and default_now.json()["as_of"].startswith("2025-01-15")
    other = client.post(
        "/research-runs", json={"query": "MSFT", "as_of": "2024-06-01T00:00:00Z"}
    ).json()["research_run_id"]
    mixed = client.get(f"/research-runs/{rid}/diff", params={"against": other})
    assert mixed.status_code == 422 and "different companies" in mixed.json()["error"]["message"]
    history = client.get("/companies/co_apple/runs").json()
    assert [r["as_of"][:10] for r in history] == ["2024-06-01", "2025-01-15"]


def test_dashboard_diff_view_shapes_payload() -> None:
    from equitydesk.dashboard import views

    diff = {
        "sources": {"added": ["d2"], "removed": []}, "guidance": {"added": [], "removed": ["g"]},
        "risks": {"added": [], "removed": []}, "catalysts": {"added": [], "removed": []},
        "thesis": {"added": ["t"], "removed": []},
        "kpis": [{"key": "gross_margin", "old": 0.4, "new": 0.45, "delta": 0.05}],
        "valuation": [], "assumptions_changed": ["dcf"],
    }  # fmt: skip
    sections = views.diff_sections(diff)
    assert sections["sources"] == [{"change": "added", "item": "d2"}]
    assert sections["guidance"] == [{"change": "removed", "item": "g"}]
    assert sections["kpis"][0]["delta"] == 0.05 and sections["assumptions"] == [
        {"changed assumption set": "dcf"}
    ]
    rows = views.history_rows([
        {"as_of": "2024-06-01", "created_at": "a", "status": "Created", "research_run_id": "r1"},
        {"as_of": "2024-12-01", "created_at": "b", "status": "Created", "research_run_id": "r2", "refreshed_from": "r1"},
    ])  # fmt: skip
    assert [r["run"] for r in rows] == ["r2", "r1"] and rows[0]["refreshed_from"] == "r1"
