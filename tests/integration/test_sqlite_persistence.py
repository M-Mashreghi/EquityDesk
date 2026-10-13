from __future__ import annotations

from pathlib import Path

import pytest

import factories as f
from equitydesk.application.ports import ImmutableRecordError, NotFoundError
from equitydesk.domain.research import ResearchStatus
from equitydesk.infrastructure.db.sqlite import SCHEMA_VERSION, Repositories, open_database

pytestmark = pytest.mark.integration


@pytest.fixture
def repos(tmp_path: Path) -> Repositories:
    r = open_database(tmp_path / "db" / "equitydesk.db")
    r.companies.save_company(f.company())
    r.companies.save_security(f.security())
    return r


def test_fresh_db_initializes_and_is_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "fresh.db"
    r = open_database(path)
    assert r.db.schema_version() == SCHEMA_VERSION
    r.db.close()
    r2 = open_database(path)  # re-open existing DB
    assert r2.db.schema_version() == SCHEMA_VERSION
    tables = {
        row["name"]
        for row in r2.db.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert {
        "companies",
        "securities",
        "research_runs",
        "source_documents",
        "research_findings",
        "financial_facts",
        "valuation_runs",
    } <= tables


def test_company_and_security_round_trip(repos: Repositories) -> None:
    assert repos.companies.get_company("co_apple") == f.company()
    assert repos.companies.securities_for_company("co_apple") == [f.security()]
    with pytest.raises(NotFoundError):
        repos.companies.get_company("missing")


def test_run_round_trip_keeps_timezone_aware_utc(repos: Repositories) -> None:
    run = f.run()
    repos.runs.add_run(run)
    loaded = repos.runs.get_run("run_1")
    assert loaded == run
    assert loaded.as_of.tzinfo is not None and loaded.created_at.utcoffset().total_seconds() == 0


def test_run_status_update_and_terminal_immutability(repos: Repositories) -> None:
    run = f.run()
    repos.runs.add_run(run)
    run = run.transition(ResearchStatus.COLLECTING).transition(ResearchStatus.FAILED, "x")
    repos.runs.update_run(run)
    assert repos.runs.get_run("run_1").status is ResearchStatus.FAILED
    with pytest.raises(ImmutableRecordError):
        repos.runs.update_run(run)


def test_run_identity_cannot_change(repos: Repositories) -> None:
    repos.runs.add_run(f.run())
    with pytest.raises(ImmutableRecordError, match="identity"):
        repos.runs.update_run(f.run(as_of=f.dt(2024, 5, 1)))


def test_documents_findings_facts_valuations_round_trip(repos: Repositories) -> None:
    repos.runs.add_run(f.run())
    doc = f.document()
    repos.documents.add_document(doc)
    repos.documents.add_document(doc)  # identical re-insert is idempotent
    assert repos.documents.get_document(doc.document_id) == doc
    assert repos.documents.find_by_hash("co_apple", f.HASH) == doc
    assert repos.documents.find_by_official_id("0000320193-23-000106") == [doc]

    repos.findings.add_findings([f.finding()])
    assert repos.findings.findings_for_run("run_1") == [f.finding()]

    repos.facts.add_facts([f.fact()])
    assert repos.facts.facts_for_company("co_apple") == [f.fact()]

    repos.valuations.add_valuation(f.valuation())
    assert repos.valuations.valuations_for_run("run_1") == [f.valuation()]


def test_documents_cannot_be_overwritten(repos: Repositories) -> None:
    repos.documents.add_document(f.document())
    with pytest.raises(ImmutableRecordError):
        repos.documents.add_document(f.document(title="changed"))


def test_point_in_time_query_filters(repos: Repositories) -> None:
    repos.documents.add_document(f.document())
    later = f.document(document_id="doc_q1", published_at=f.dt(2024, 2, 2), official_id="x")
    repos.documents.add_document(later)
    visible = repos.documents.documents_for_company("co_apple", f.dt(2024, 1, 1))
    assert [d.document_id for d in visible] == ["doc_10k_2023"]
    repos.facts.add_facts([f.fact(), f.fact(fact_id="late", available_at=f.dt(2024, 2, 2))])
    assert [x.fact_id for x in repos.facts.facts_for_company("co_apple", f.dt(2024, 1, 1))] == [
        "fct_rev_fy23"
    ]


def test_foreign_keys_enforced(repos: Repositories) -> None:
    import sqlite3

    with pytest.raises(sqlite3.IntegrityError):
        repos.runs.add_run(f.run(research_run_id="r2", company_id="unknown_co"))


def test_failed_batch_rolls_back(repos: Repositories) -> None:
    repos.runs.add_run(f.run())
    with pytest.raises(ImmutableRecordError):
        repos.findings.add_findings(
            [f.finding(finding_id="a"), f.finding(finding_id="a", statement="different")]
        )
    assert repos.findings.findings_for_run("run_1") == []
