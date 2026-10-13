"""SQLite system of record.

Each table keeps a few indexed columns for querying plus the full validated domain object as a
JSON `payload`, so round-trips are lossless and schema evolution is additive.
Timestamps in indexed columns are UTC ISO-8601 strings with fixed precision (sortable as text).
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel

from equitydesk.application.ports import ImmutableRecordError, NotFoundError
from equitydesk.domain.companies import Company, Security
from equitydesk.domain.documents import SourceDocument
from equitydesk.domain.financials import FinancialFact
from equitydesk.domain.research import ResearchFinding, ResearchRun
from equitydesk.domain.valuation import ValuationRun

SCHEMA_VERSION = 1

M = TypeVar("M", bound=BaseModel)

SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS companies (
    company_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    cik TEXT,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_companies_cik ON companies(cik);

CREATE TABLE IF NOT EXISTS securities (
    security_id TEXT PRIMARY KEY,
    company_id TEXT NOT NULL REFERENCES companies(company_id),
    ticker TEXT NOT NULL,
    exchange TEXT NOT NULL,
    isin TEXT,
    payload TEXT NOT NULL,
    UNIQUE (ticker, exchange, security_id)
);
CREATE INDEX IF NOT EXISTS ix_securities_company ON securities(company_id);
CREATE INDEX IF NOT EXISTS ix_securities_ticker ON securities(ticker);

CREATE TABLE IF NOT EXISTS research_runs (
    research_run_id TEXT PRIMARY KEY,
    company_id TEXT NOT NULL REFERENCES companies(company_id),
    as_of TEXT NOT NULL,
    created_at TEXT NOT NULL,
    status TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_runs_company ON research_runs(company_id, as_of);

CREATE TABLE IF NOT EXISTS source_documents (
    document_id TEXT PRIMARY KEY,
    company_id TEXT NOT NULL REFERENCES companies(company_id),
    source_type TEXT NOT NULL,
    document_type TEXT NOT NULL,
    official_id TEXT,
    content_hash TEXT,
    published_at TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_docs_company ON source_documents(company_id, published_at);
CREATE INDEX IF NOT EXISTS ix_docs_hash ON source_documents(company_id, content_hash);
CREATE INDEX IF NOT EXISTS ix_docs_official ON source_documents(official_id);

CREATE TABLE IF NOT EXISTS research_findings (
    finding_id TEXT PRIMARY KEY,
    research_run_id TEXT NOT NULL REFERENCES research_runs(research_run_id),
    category TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_findings_run ON research_findings(research_run_id);

CREATE TABLE IF NOT EXISTS financial_facts (
    fact_id TEXT PRIMARY KEY,
    company_id TEXT NOT NULL REFERENCES companies(company_id),
    concept TEXT NOT NULL,
    period_end TEXT NOT NULL,
    available_at TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_facts_company ON financial_facts(company_id, concept, period_end);

CREATE TABLE IF NOT EXISTS valuation_runs (
    valuation_id TEXT PRIMARY KEY,
    research_run_id TEXT NOT NULL REFERENCES research_runs(research_run_id),
    method TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_valuations_run ON valuation_runs(research_run_id);
"""


def ts(value: datetime) -> str:
    """Canonical sortable UTC text for indexed timestamp columns."""
    if value.tzinfo is None:
        raise ValueError("naive datetime cannot be persisted")
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


class Database:
    def __init__(self, path: Path | str = ":memory:") -> None:
        if isinstance(path, Path):
            path.parent.mkdir(parents=True, exist_ok=True)
        self.path = str(path)
        self.conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        if self.path != ":memory:":
            self.conn.execute("PRAGMA journal_mode = WAL")

    def init_schema(self) -> None:
        self.conn.executescript(SCHEMA)
        existing = self.schema_version()
        if existing is None:
            self.conn.execute(
                "INSERT INTO schema_meta(key, value) VALUES ('schema_version', ?)",
                (str(SCHEMA_VERSION),),
            )
        elif existing > SCHEMA_VERSION:
            raise RuntimeError(f"database schema {existing} is newer than code ({SCHEMA_VERSION})")

    def schema_version(self) -> int | None:
        try:
            row = self.conn.execute(
                "SELECT value FROM schema_meta WHERE key='schema_version'"
            ).fetchone()
        except sqlite3.OperationalError:
            return None
        return int(row["value"]) if row else None

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        self.conn.execute("BEGIN")
        try:
            yield self.conn
        except BaseException:
            self.conn.execute("ROLLBACK")
            raise
        else:
            self.conn.execute("COMMIT")

    def close(self) -> None:
        self.conn.close()


def _load[M: BaseModel](model: type[M], row: sqlite3.Row | None, what: str) -> M:
    if row is None:
        raise NotFoundError(what)
    return model.model_validate_json(row["payload"])


def _insert_immutable(
    db: Database, table: str, key: str, values: dict[str, Any], what: str
) -> None:
    """Insert; identical re-insert is a no-op; a different payload for the same id is an error."""
    row = db.conn.execute(f"SELECT payload FROM {table} WHERE {key} = ?", (values[key],)).fetchone()  # noqa: S608
    if row is not None:
        if row["payload"] != values["payload"]:
            raise ImmutableRecordError(f"{what} {values[key]} already exists with other content")
        return
    cols = ", ".join(values)
    marks = ", ".join("?" for _ in values)
    db.conn.execute(f"INSERT INTO {table} ({cols}) VALUES ({marks})", tuple(values.values()))  # noqa: S608


class SqliteCompanyRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def save_company(self, company: Company) -> None:
        self.db.conn.execute(
            "INSERT INTO companies(company_id, name, cik, payload) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(company_id) DO UPDATE SET name=excluded.name, cik=excluded.cik, "
            "payload=excluded.payload",
            (company.company_id, company.name, company.cik, company.model_dump_json()),
        )

    def get_company(self, company_id: str) -> Company:
        row = self.db.conn.execute(
            "SELECT payload FROM companies WHERE company_id = ?", (company_id,)
        ).fetchone()
        return _load(Company, row, f"company {company_id}")

    def list_companies(self) -> list[Company]:
        rows = self.db.conn.execute("SELECT payload FROM companies ORDER BY company_id")
        return [Company.model_validate_json(r["payload"]) for r in rows]

    def save_security(self, security: Security) -> None:
        self.db.conn.execute(
            "INSERT INTO securities(security_id, company_id, ticker, exchange, isin, payload) "
            "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(security_id) DO UPDATE SET "
            "company_id=excluded.company_id, ticker=excluded.ticker, exchange=excluded.exchange, "
            "isin=excluded.isin, payload=excluded.payload",
            (
                security.security_id,
                security.company_id,
                security.ticker,
                security.exchange,
                security.isin,
                security.model_dump_json(),
            ),
        )

    def get_security(self, security_id: str) -> Security:
        row = self.db.conn.execute(
            "SELECT payload FROM securities WHERE security_id = ?", (security_id,)
        ).fetchone()
        return _load(Security, row, f"security {security_id}")

    def securities_for_company(self, company_id: str) -> list[Security]:
        rows = self.db.conn.execute(
            "SELECT payload FROM securities WHERE company_id = ? ORDER BY security_id",
            (company_id,),
        )
        return [Security.model_validate_json(r["payload"]) for r in rows]


class SqliteResearchRunRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def add_run(self, run: ResearchRun) -> None:
        self.db.conn.execute(
            "INSERT INTO research_runs(research_run_id, company_id, as_of, created_at, status, "
            "payload) VALUES (?, ?, ?, ?, ?, ?)",
            (
                run.research_run_id,
                run.company_id,
                ts(run.as_of),
                ts(run.created_at),
                run.status.value,
                run.model_dump_json(),
            ),
        )

    def update_run(self, run: ResearchRun) -> None:
        current = self.get_run(run.research_run_id)
        if current.is_terminal:
            raise ImmutableRecordError(f"research run {run.research_run_id} is {current.status}")
        identity = ("company_id", "security_id", "as_of", "created_at", "config_version")
        if any(getattr(current, f) != getattr(run, f) for f in identity):
            raise ImmutableRecordError("research run identity fields cannot change")
        self.db.conn.execute(
            "UPDATE research_runs SET status = ?, payload = ? WHERE research_run_id = ?",
            (run.status.value, run.model_dump_json(), run.research_run_id),
        )

    def get_run(self, research_run_id: str) -> ResearchRun:
        row = self.db.conn.execute(
            "SELECT payload FROM research_runs WHERE research_run_id = ?", (research_run_id,)
        ).fetchone()
        return _load(ResearchRun, row, f"research run {research_run_id}")

    def runs_for_company(self, company_id: str) -> list[ResearchRun]:
        rows = self.db.conn.execute(
            "SELECT payload FROM research_runs WHERE company_id = ? ORDER BY as_of, created_at",
            (company_id,),
        )
        return [ResearchRun.model_validate_json(r["payload"]) for r in rows]


class SqliteDocumentRepository:
    """Source documents are immutable once stored (point-in-time snapshot rule)."""

    def __init__(self, db: Database) -> None:
        self.db = db

    def add_document(self, document: SourceDocument) -> None:
        _insert_immutable(
            self.db,
            "source_documents",
            "document_id",
            {
                "document_id": document.document_id,
                "company_id": document.company_id,
                "source_type": document.source_type.value,
                "document_type": document.document_type,
                "official_id": document.official_id,
                "content_hash": document.content_hash,
                "published_at": ts(document.published_at),
                "payload": document.model_dump_json(),
            },
            "source document",
        )

    def get_document(self, document_id: str) -> SourceDocument:
        row = self.db.conn.execute(
            "SELECT payload FROM source_documents WHERE document_id = ?", (document_id,)
        ).fetchone()
        return _load(SourceDocument, row, f"document {document_id}")

    def documents_for_company(
        self, company_id: str, published_at_or_before: datetime | None = None
    ) -> list[SourceDocument]:
        sql = "SELECT payload FROM source_documents WHERE company_id = ?"
        params: list[Any] = [company_id]
        if published_at_or_before is not None:
            sql += " AND published_at <= ?"
            params.append(ts(published_at_or_before))
        sql += " ORDER BY published_at, document_id"
        return [
            SourceDocument.model_validate_json(r["payload"])
            for r in self.db.conn.execute(sql, params)
        ]

    def find_by_hash(self, company_id: str, content_hash: str) -> SourceDocument | None:
        row = self.db.conn.execute(
            "SELECT payload FROM source_documents WHERE company_id = ? AND content_hash = ? "
            "ORDER BY published_at, document_id LIMIT 1",
            (company_id, content_hash),
        ).fetchone()
        return SourceDocument.model_validate_json(row["payload"]) if row else None

    def find_by_official_id(self, official_id: str) -> list[SourceDocument]:
        rows = self.db.conn.execute(
            "SELECT payload FROM source_documents WHERE official_id = ? "
            "ORDER BY published_at, document_id",
            (official_id,),
        )
        return [SourceDocument.model_validate_json(r["payload"]) for r in rows]


class SqliteFindingRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def add_findings(self, findings: Sequence[ResearchFinding]) -> None:
        with self.db.transaction():
            for f in findings:
                _insert_immutable(
                    self.db,
                    "research_findings",
                    "finding_id",
                    {
                        "finding_id": f.finding_id,
                        "research_run_id": f.research_run_id,
                        "category": f.category.value,
                        "payload": f.model_dump_json(),
                    },
                    "finding",
                )

    def findings_for_run(self, research_run_id: str) -> list[ResearchFinding]:
        rows = self.db.conn.execute(
            "SELECT payload FROM research_findings WHERE research_run_id = ? ORDER BY rowid",
            (research_run_id,),
        )
        return [ResearchFinding.model_validate_json(r["payload"]) for r in rows]


class SqliteFinancialFactRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def add_facts(self, facts: Sequence[FinancialFact]) -> None:
        with self.db.transaction():
            for f in facts:
                _insert_immutable(
                    self.db,
                    "financial_facts",
                    "fact_id",
                    {
                        "fact_id": f.fact_id,
                        "company_id": f.company_id,
                        "concept": f.concept,
                        "period_end": f.period_end.isoformat(),
                        "available_at": ts(f.available_at),
                        "payload": f.model_dump_json(),
                    },
                    "financial fact",
                )

    def facts_for_company(
        self, company_id: str, available_at_or_before: datetime | None = None
    ) -> list[FinancialFact]:
        sql = "SELECT payload FROM financial_facts WHERE company_id = ?"
        params: list[Any] = [company_id]
        if available_at_or_before is not None:
            sql += " AND available_at <= ?"
            params.append(ts(available_at_or_before))
        sql += " ORDER BY concept, period_end, available_at, fact_id"
        return [
            FinancialFact.model_validate_json(r["payload"])
            for r in self.db.conn.execute(sql, params)
        ]


class SqliteValuationRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def add_valuation(self, valuation: ValuationRun) -> None:
        _insert_immutable(
            self.db,
            "valuation_runs",
            "valuation_id",
            {
                "valuation_id": valuation.valuation_id,
                "research_run_id": valuation.research_run_id,
                "method": valuation.method.value,
                "payload": valuation.model_dump_json(),
            },
            "valuation",
        )

    def valuations_for_run(self, research_run_id: str) -> list[ValuationRun]:
        rows = self.db.conn.execute(
            "SELECT payload FROM valuation_runs WHERE research_run_id = ? ORDER BY rowid",
            (research_run_id,),
        )
        return [ValuationRun.model_validate_json(r["payload"]) for r in rows]


class Repositories:
    """Convenience bundle of all SQLite repositories sharing one connection."""

    def __init__(self, db: Database) -> None:
        self.db = db
        self.companies = SqliteCompanyRepository(db)
        self.runs = SqliteResearchRunRepository(db)
        self.documents = SqliteDocumentRepository(db)
        self.findings = SqliteFindingRepository(db)
        self.facts = SqliteFinancialFactRepository(db)
        self.valuations = SqliteValuationRepository(db)


def open_database(path: Path | str = ":memory:") -> Repositories:
    db = Database(path)
    db.init_schema()
    return Repositories(db)
