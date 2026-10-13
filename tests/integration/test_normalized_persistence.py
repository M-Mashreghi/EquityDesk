from __future__ import annotations

from pathlib import Path

import pytest

import factories as f
from equitydesk.application.ports import ImmutableRecordError
from equitydesk.infrastructure.db.sqlite import SCHEMA_VERSION, Database, open_database
from equitydesk.infrastructure.parsers.html import HtmlDocumentParser

pytestmark = pytest.mark.integration

TENK = (Path(__file__).resolve().parents[1] / "fixtures" / "sec" / "aapl-20230930.htm").read_bytes()


def test_normalized_round_trip_and_immutability() -> None:
    repos = open_database()
    repos.companies.save_company(f.company())
    repos.documents.add_document(f.document())
    doc = HtmlDocumentParser().parse("doc_10k_2023", TENK)
    repos.normalized.add_normalized(doc)
    repos.normalized.add_normalized(doc)  # idempotent
    assert repos.normalized.get_normalized("doc_10k_2023") == doc
    with pytest.raises(ImmutableRecordError):
        repos.normalized.add_normalized(doc.model_copy(update={"title": "other"}))


def test_v1_database_is_migrated_additively(tmp_path: Path) -> None:
    path = tmp_path / "old.db"
    db = Database(path)
    db.init_schema()
    db.conn.execute("DROP TABLE normalized_documents")
    db.conn.execute("UPDATE schema_meta SET value = '1'")
    db.close()
    repos = open_database(path)
    assert repos.db.schema_version() == SCHEMA_VERSION
    tables = {r["name"] for r in repos.db.conn.execute("SELECT name FROM sqlite_master")}
    assert "normalized_documents" in tables


def test_newer_schema_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "new.db"
    open_database(path).db.conn.execute("UPDATE schema_meta SET value = '999'")
    with pytest.raises(RuntimeError, match="newer"):
        open_database(path)
