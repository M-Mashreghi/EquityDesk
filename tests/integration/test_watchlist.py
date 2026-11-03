"""Watchlist: due refreshes, duplicate-refresh guard, filing/run alerts and webhook secrets."""

from __future__ import annotations

import json
import threading
from datetime import timedelta
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

import factories as f
import report_fixtures as rf
from equitydesk.api.app import create_app
from equitydesk.application.watchlist import WatchlistService
from equitydesk.bootstrap import build_container
from equitydesk.config import Settings
from equitydesk.domain.research import ResearchStatus
from equitydesk.domain.watchlist import AlertKind
from equitydesk.infrastructure.jobs.alerts import MemoryAlertSink, WebhookAlertSink
from equitydesk.infrastructure.jobs.scheduler import run_scheduler
from equitydesk.reporting.composer import compose_report
from equitydesk.reporting.reviewer import apply_review, review_report

pytestmark = pytest.mark.integration


class Clock:
    def __init__(self) -> None:
        self.now = f.dt(2024, 6, 1, 12)

    def __call__(self):
        return self.now


@pytest.fixture
def ctx(tmp_path: Path):
    clock = Clock()
    container = build_container(Settings(), db_path=tmp_path / "watch.db")
    container.research.clock = clock
    sink = MemoryAlertSink()
    watch = WatchlistService(container.repos.watchlist, container.research, sink, clock)
    client = TestClient(create_app(container.research, watch))
    client.post(
        "/research-runs", json={"query": "AAPL", "as_of": "2024-06-01T00:00:00Z"}
    )  # persists the company
    return container, watch, sink, clock, client


def test_due_refresh_and_duplicate_guard(ctx) -> None:
    container, watch, _, clock, client = ctx
    assert client.put("/watchlist/co_apple", json={"refresh_interval_hours": 24}).status_code == 200
    first = client.post("/watchlist/tick").json()["refreshes"]
    assert first[0]["action"] == "started"
    new_run = container.repos.runs.get_run(first[0]["research_run_id"])
    assert (
        new_run.as_of == clock.now and new_run.refreshed_from is not None
    )  # refresh of latest run

    # same moment again (e.g. a second scheduler process): not due, and the slot is taken
    assert client.post("/watchlist/tick").json()["refreshes"][0]["action"] == "skipped_not_due"
    entry = container.repos.watchlist.get_entry("co_apple")
    stale = entry.model_copy(update={"last_refresh_at": None})  # simulate a racing process
    container.repos.watchlist.save_entry(stale)
    assert watch.tick().refreshes[0].action == "skipped_duplicate"
    assert len(container.repos.runs.runs_for_company("co_apple")) == 2  # only one refresh created

    clock.now += timedelta(hours=25)
    container.repos.watchlist.save_entry(entry)
    assert watch.tick().refreshes[0].action == "started"
    assert len(container.repos.runs.runs_for_company("co_apple")) == 3


def test_concurrent_claims_create_one_refresh(ctx) -> None:
    container, watch, *_ = ctx
    watch.watch("co_apple", 24)
    results = [watch.repo.claim_slot("co_apple:24h:1", "co_apple", f.T0) for _ in range(5)]
    assert results.count(True) == 1


def test_in_progress_run_blocks_refresh(ctx) -> None:
    container, watch, *_ = ctx
    run = container.repos.runs.runs_for_company("co_apple")[0]
    container.repos.runs.update_run(run.transition(ResearchStatus.COLLECTING, at=f.T0))
    watch.watch("co_apple", 24)
    assert watch.tick().refreshes[0].action == "skipped_in_progress"


def test_new_filing_alert_references_sources_once(ctx) -> None:
    container, watch, sink, clock, client = ctx
    watch.watch("co_apple", 24 * 30)
    container.repos.documents.add_document(
        rf.Q2.model_copy(update={"published_at": f.dt(2024, 6, 2)})
    )
    clock.now = f.dt(2024, 6, 3)
    result = watch.tick()
    filing = [a for a in result.alerts if a.kind is AlertKind.NEW_FILING]
    assert len(filing) == 1 and filing[0].delivered
    src = filing[0].sources[0]
    assert (src.document_id, src.official_id, src.document_type) == (
        "doc_10q_2024q2",
        "0000320193-24-000069",
        "10-Q",
    )
    assert src.source_url and sink.sent[0].alert_id == filing[0].alert_id
    assert not [a for a in watch.tick().alerts if a.kind is AlertKind.NEW_FILING]  # no repeat
    stored = client.get("/alerts", params={"company_id": "co_apple"}).json()
    assert any(
        a["kind"] == "new_filing" and a["sources"][0]["document_id"] == "doc_10q_2024q2"
        for a in stored
    )


def test_run_finished_alert_references_run_report_and_sources(ctx) -> None:
    container, watch, sink, clock, _ = ctx
    repos = container.repos
    watch.watch("co_apple", 24 * 30)
    repos.documents.add_document(rf.DOC)
    repos.documents.add_document(rf.Q2)
    run = repos.runs.runs_for_company("co_apple")[0]
    for status in (ResearchStatus.COLLECTING, ResearchStatus.PARSING, ResearchStatus.ANALYZING,
                   ResearchStatus.VALUING, ResearchStatus.REVIEWING):  # fmt: skip
        run = run.transition(status, at=f.T0)
        repos.runs.update_run(run)
    findings = [
        x.model_copy(update={"research_run_id": run.research_run_id}) for x in rf.findings()
    ]
    vals = rf.valuations(run.research_run_id)
    report = compose_report(run=run, company=f.company(), documents=[rf.DOC, rf.Q2], findings=findings,
                            metrics=rf.metrics(), valuations=vals, thesis=rf.thesis(vals))  # fmt: skip
    repos.reports.add_report(report)
    review = review_report(report, [rf.DOC, rf.Q2], rf.metrics(), vals)
    repos.reports.add_review(review)
    repos.runs.update_run(apply_review(run, review, at=f.T0))

    alert = watch.run_finished(run.research_run_id)
    assert alert is not None and alert.kind is AlertKind.RUN_COMPLETED
    assert alert.research_run_id == run.research_run_id and alert.report_id == report.report_id
    assert {s.document_id for s in alert.sources} == {"doc_10k_2023", "doc_10q_2024q2"}
    assert watch.run_finished(run.research_run_id) is None  # idempotent hook
    assert sink.sent[-1].alert_id == alert.alert_id


def test_webhook_sink_uses_external_secrets_and_hides_them() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(500 if b"fail" in request.content else 204)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    sink = WebhookAlertSink(
        SecretStr("https://hooks.example.test/T/secret-path"), SecretStr("tok-123"), client
    )
    from equitydesk.domain.watchlist import Alert

    alert = Alert(alert_id="a1", kind=AlertKind.RUN_COMPLETED, company_id="co", research_run_id="run_1",
                  message="done", created_at=f.T0)  # fmt: skip
    sink.send(alert)
    assert seen[0].headers["authorization"] == "Bearer tok-123"
    body = json.loads(seen[0].content)
    assert body["research_run_id"] == "run_1" and "tok-123" not in seen[0].content.decode()
    from equitydesk.application.watchlist import AlertDeliveryError

    with pytest.raises(AlertDeliveryError) as exc:
        sink.send(alert.model_copy(update={"message": "fail"}))
    assert "secret-path" not in str(exc.value) and "tok-123" not in str(exc.value)
    assert "secret-path" not in repr(sink.__dict__)


def test_failed_delivery_is_recorded(ctx) -> None:
    container, watch, _, clock, _ = ctx

    class Broken:
        def send(self, alert):
            from equitydesk.application.watchlist import AlertDeliveryError

            raise AlertDeliveryError("webhook responded with HTTP 503")

    watch.sink = Broken()
    watch.watch("co_apple", 24)
    result = watch.tick()
    started = next(a for a in result.alerts if a.kind is AlertKind.REFRESH_STARTED)
    stored = next(
        a for a in container.repos.watchlist.alerts("co_apple") if a.alert_id == started.alert_id
    )
    assert not stored.delivered and stored.delivery_error == "webhook responded with HTTP 503"


def test_watchlist_api_and_scheduler_loop(ctx) -> None:
    _, watch, _, _, client = ctx
    assert client.put("/watchlist/co_missing", json={}).status_code == 404
    assert client.put("/watchlist/co_apple", json={"refresh_interval_hours": 0}).status_code == 422
    client.put("/watchlist/co_apple", json={"refresh_interval_hours": 6})
    assert [e["company_id"] for e in client.get("/watchlist").json()] == ["co_apple"]
    ticks: list = []
    n = run_scheduler(watch, 0, threading.Event(), on_tick=ticks.append, max_ticks=2)
    assert (
        n == 2
        and ticks[0].refreshes[0].action == "started"
        and ticks[1].refreshes[0].action == "skipped_not_due"
    )
    assert client.delete("/watchlist/co_apple").status_code == 204
    assert client.delete("/watchlist/co_apple").status_code == 404
