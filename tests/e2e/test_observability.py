"""Observability: stage timing, LLM token/cost telemetry, run correlation in logs, health probes."""

from __future__ import annotations

import io
import json
import logging
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

import factories as f
from e2e_support import INPUTS
from equitydesk.api.app import create_app
from equitydesk.application.telemetry import RunTelemetry, TokenPrices
from equitydesk.bootstrap import build_container, diagnostics, readiness
from equitydesk.config import Settings
from equitydesk.logging_setup import configure_logging

pytestmark = pytest.mark.e2e


@pytest.fixture
def logs():
    stream = io.StringIO()
    handler = configure_logging(
        "INFO", "json", secrets=["sk-test-supersecret-value"], stream=stream
    )
    yield lambda: [json.loads(line) for line in stream.getvalue().splitlines() if line.strip()]
    logger = logging.getLogger("equitydesk")
    logger.removeHandler(handler)
    logger.propagate = True


def test_run_telemetry_and_log_correlation(env, logs) -> None:
    container, make = env
    workflow, _ = make()
    workflow.telemetry_store = container.repos.telemetry
    workflow.token_prices = TokenPrices(input_per_mtok=5.0, output_per_mtok=25.0)
    run = container.research.create_run("AAPL", f.dt(2024, 1, 15))
    outcome = workflow.execute(run.research_run_id, INPUTS)

    t = outcome.telemetry
    assert [s["stage"] for s in t["stages"]] == [
        "Collecting",
        "Parsing",
        "Analyzing",
        "Valuing",
        "Reviewing",
    ]
    assert all(s["seconds"] >= 0 for s in t["stages"]) and t["stages"][-1]["outcome"] == "Completed"
    assert t["counters"]["documents_collected"] == 1 and t["counters"]["collection_failures"] == 4
    assert t["counters"]["documents_parsed"] == 1 and t["counters"]["facts_mapped"] > 0
    assert t["counters"]["findings_accepted"] > 0 and t["counters"]["valuations"] == 2
    llm = t["llm"]
    assert llm["calls"] == 3  # business, risks, contradiction review
    assert {c["prompt"] for c in llm["by_call"]} >= {
        "business_model_extractor@1.0.0",
        "report_contradiction_review@1.0.0",
    }
    expected = (llm["input_tokens"] * 5.0 + llm["output_tokens"] * 25.0) / 1e6
    assert llm["input_tokens"] > 0 and llm["cost_usd"] == pytest.approx(expected, abs=1e-6)
    assert container.repos.telemetry.get_telemetry(run.research_run_id) == json.loads(json.dumps(t))

    records = logs()
    run_records = [r for r in records if r.get("research_run_id") == run.research_run_id]
    messages = {r["message"] for r in run_records}
    assert {
        "research run started",
        "stage finished",
        "llm call",
        "research run finished",
    } <= messages
    assert len({r["correlation_id"] for r in run_records}) == 1  # one correlation id for the run


def test_cost_unknown_without_prices() -> None:
    t = RunTelemetry("run_x", timer=iter([0.0, 1.5, 1.5, 2.0]).__next__)
    t.enter("Collecting")
    t.enter("Parsing")
    t.close("Completed")
    assert t.summary()["stages"] == [
        {"stage": "Collecting", "seconds": 1.5, "outcome": "ok"},
        {"stage": "Parsing", "seconds": 0.5, "outcome": "Completed"},
    ]
    assert t.summary()["llm"]["cost_usd"] == 0.0  # no calls
    assert TokenPrices(5.0, None).cost(100, 100) is None


def test_health_endpoints_and_request_correlation(tmp_path: Path, logs) -> None:
    settings = Settings(data_dir=tmp_path / "data", llm_provider="anthropic", llm_model="m",
                        llm_api_key=SecretStr("sk-test-supersecret-value"),
                        alert_webhook_url=SecretStr("https://hooks.example.test/secret-path"))  # fmt: skip
    c = build_container(settings, db_path=tmp_path / "h.db")
    client = TestClient(create_app(c.research, c.watchlist, readiness=lambda: readiness(c),
                                   diagnostics=lambda: diagnostics(c), telemetry=c.repos.telemetry))  # fmt: skip
    assert client.get("/healthz").json()["status"] == "ok"
    ready = client.get("/ready")
    assert ready.status_code == 200 and ready.json()["checks"]["database"]["ok"] is True
    detailed = client.get("/health/detailed")
    body = detailed.text
    assert (
        detailed.json()["llm"]["api_key_configured"] is True
        and detailed.json()["alerts"]["webhook_configured"]
    )
    assert "supersecret" not in body and "secret-path" not in body

    resp = client.get("/healthz", headers={"X-Correlation-ID": "req-123"})
    assert resp.headers["X-Correlation-ID"] == "req-123"
    generated = client.get("/healthz", headers={"X-Correlation-ID": "bad id with spaces!"}).headers[
        "X-Correlation-ID"
    ]
    assert generated != "bad id with spaces!" and len(generated) == 32
    access = [r for r in logs() if r.get("message") == "request" and r.get("path") == "/healthz"]
    assert any(r["correlation_id"] == "req-123" and r["status"] == 200 for r in access)
    run_id = client.post(
        "/research-runs", json={"query": "AAPL", "as_of": "2024-01-15T00:00:00Z"}
    ).json()["research_run_id"]
    assert client.get(f"/research-runs/{run_id}/telemetry").status_code == 404

    c.repos.db.close()
    down = client.get("/ready")
    assert down.status_code == 503 and down.json()["checks"]["database"]["ok"] is False


def test_secrets_never_reach_logs(logs) -> None:
    log = logging.getLogger("equitydesk.test")
    log.error(
        "provider failed with key sk-test-supersecret-value",
        extra={"authorization": "Bearer abcdefghijkl"},
    )
    text = json.dumps(logs())
    assert "supersecret" not in text and "abcdefghijkl" not in text
