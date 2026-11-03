"""Dashboard: client + views against the real API, and the Streamlit workflow headless."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from streamlit.testing.v1 import AppTest

import factories as f
import report_fixtures as rf
from equitydesk.api.app import create_app
from equitydesk.bootstrap import build_container
from equitydesk.config import Settings
from equitydesk.dashboard import views
from equitydesk.dashboard.client import ApiClient, ApiError
from equitydesk.reporting.composer import compose_report
from equitydesk.reporting.reviewer import review_report

pytestmark = pytest.mark.integration
APP = Path(__file__).resolve().parents[2] / "src" / "equitydesk" / "dashboard" / "app.py"


@pytest.fixture
def api(tmp_path: Path):
    container = build_container(Settings(), db_path=tmp_path / "dash.db")
    container.research.clock = lambda: f.dt(2024, 7, 1, 12)
    return container, ApiClient(http=TestClient(create_app(container.research)))


def populate(container, run_id: str) -> None:
    repos = container.repos
    run = repos.runs.get_run(run_id)
    repos.documents.add_document(rf.DOC)
    repos.documents.add_document(rf.Q2)
    vals = rf.valuations(run_id)
    for v in vals:
        repos.valuations.add_valuation(v)
    data = rf.inputs() | {"run": run, "valuations": vals, "thesis": rf.thesis(vals),
                          "findings": [x.model_copy(update={"research_run_id": run_id}) for x in rf.findings()]}  # fmt: skip
    report = compose_report(**data)
    repos.reports.add_report(report)
    repos.reports.add_review(review_report(report, data["documents"], data["metrics"], vals))


def test_client_and_views(api) -> None:
    container, client = api
    assert client.health()["status"] == "ok"
    with pytest.raises(ApiError) as exc:
        client.create_run("ASML")
    assert exc.value.status == 409 and exc.value.details["candidates"]
    run = client.create_run("AAPL", "2024-06-01T00:00:00Z")
    populate(container, run["research_run_id"])
    rid = run["research_run_id"]
    assert views.run_progress(client.run(rid)) == (0.0, "Created")
    docs = views.document_rows(client.documents(rid))
    assert [d["type"] for d in docs] == ["10-Q", "10-K"]  # newest first
    rows = views.valuation_rows(client.valuations(rid))
    assert {r["method"] for r in rows} == {"multiples:ev_sales", "dcf"}
    scen = views.scenario_rows(client.valuations(rid))
    assert [r["scenario"] for r in scen] == ["bull", "base", "bear"]
    assert scen[1]["value / share"] == pytest.approx(18.784091, abs=1e-6)
    report = client.report(rid)
    assert report["review_passed"] is True and views.review_summary(report["review"])
    assert client.report_markdown(rid).startswith("# Apple Inc.")


def test_progress_for_blocked_run() -> None:
    run = {"status": "NeedsInput", "status_history": [
        {"status": "Collecting"}, {"status": "Parsing"}, {"status": "Analyzing"}, {"status": "Valuing"},
        {"status": "Reviewing"}, {"status": "NeedsInput", "reason": "review blocked: missing_critical_section"}]}  # fmt: skip
    fraction, label = views.run_progress(run)
    assert fraction == pytest.approx(5 / 6) and "missing_critical_section" in label


def test_metric_series_only_uses_server_values() -> None:
    fin = {"metrics": [
        {"name": "gross_margin", "period_end": "2023-09-30", "value": 0.44, "status": "ok", "unit": "ratio", "formula": "g", "missing": []},
        {"name": "gross_margin", "period_end": "2022-09-24", "value": 0.43, "status": "ok", "unit": "ratio", "formula": "g", "missing": []},
        {"name": "net_margin", "period_end": "2023-09-30", "value": None, "status": "missing_input", "unit": "ratio", "formula": "n", "missing": ["x"]},
    ]}  # fmt: skip
    assert views.metric_series(fin, ("gross_margin", "net_margin")) == {
        "gross_margin": {"2022-09-24": 0.43, "2023-09-30": 0.44}
    }
    latest = {r["metric"]: r for r in views.metric_rows(fin)}
    assert latest["gross_margin"]["value"] == 0.44 and latest["net_margin"]["missing"] == "x"


def test_streamlit_primary_workflow(api) -> None:
    container, client = api
    at = AppTest.from_file(str(APP), default_timeout=30)
    at.session_state["api_client"] = client
    at.run()
    assert not at.exception
    assert "Search for a company" in at.info[0].value

    at.sidebar.text_input(key="query").input("AAPL")
    at.sidebar.button(key="resolve").click()
    at.run()
    assert "Apple Inc." in at.sidebar.success[0].value

    at.sidebar.date_input(key="as_of").set_value(f.dt(2024, 6, 1).date())
    at.sidebar.button(key="create").click()
    at.run()
    assert not at.exception
    run_id = at.session_state["run_id"]
    populate(container, run_id)

    at.run()
    assert not at.exception
    assert any(run_id in s.value for s in at.subheader)
    assert any("review passed" in s.value for s in at.success)
    md = "\n".join(m.value for m in at.markdown)
    assert "Investment Thesis" in md and "Sources" in md
    assert len(at.dataframe) >= 4  # sources, valuation, scenarios, assumptions, review


def test_streamlit_shows_structured_errors(api) -> None:
    _, client = api
    at = AppTest.from_file(str(APP), default_timeout=30)
    at.session_state["api_client"] = client
    at.run()
    at.sidebar.text_input(key="query").input("ASML")
    at.sidebar.button(key="create").click()
    at.run()
    assert not at.exception and "ambiguous" in at.error[0].value
