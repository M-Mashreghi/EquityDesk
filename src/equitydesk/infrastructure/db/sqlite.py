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
from equitydesk.domain.normalized import NormalizedDocument
from equitydesk.domain.peers import PeerSet
from equitydesk.domain.report import ResearchReport
from equitydesk.domain.research import ResearchFinding, ResearchRun, ResearchStatus
from equitydesk.domain.review import ReviewFinding, ReviewGateError, ReviewResult
from equitydesk.domain.snapshot import ResearchSnapshot
from equitydesk.domain.valuation import ValuationRun

SCHEMA_VERSION = 5

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

-- v2: parsed documents, keyed by (document, parser version) so re-parses never overwrite.
CREATE TABLE IF NOT EXISTS normalized_documents (
    document_id TEXT NOT NULL REFERENCES source_documents(document_id),
    parser_name TEXT NOT NULL,
    parser_version TEXT NOT NULL,
    payload TEXT NOT NULL,
    PRIMARY KEY (document_id, parser_name, parser_version)
);

-- v3: point-in-time snapshot per research run. snapshot_id is content-derived, so two runs
-- over identical inputs share the same snapshot_id.
CREATE TABLE IF NOT EXISTS research_snapshots (
    research_run_id TEXT PRIMARY KEY REFERENCES research_runs(research_run_id),
    snapshot_id TEXT NOT NULL,
    as_of TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_snapshots_id ON research_snapshots(snapshot_id);
CREATE TABLE IF NOT EXISTS snapshot_documents (
    research_run_id TEXT NOT NULL REFERENCES research_snapshots(research_run_id),
    ordinal INTEGER NOT NULL,
    document_id TEXT NOT NULL REFERENCES source_documents(document_id),
    PRIMARY KEY (research_run_id, ordinal)
);

-- v4: explicit, versioned peer sets (insert-only).
CREATE TABLE IF NOT EXISTS peer_sets (
    name TEXT NOT NULL,
    version INTEGER NOT NULL,
    target_company_id TEXT NOT NULL REFERENCES companies(company_id),
    effective_from TEXT NOT NULL,
    payload TEXT NOT NULL,
    PRIMARY KEY (name, version)
);

-- v5: composed reports and independent reviews (insert-only).
CREATE TABLE IF NOT EXISTS research_reports (
    report_id TEXT PRIMARY KEY,
    research_run_id TEXT NOT NULL REFERENCES research_runs(research_run_id),
    schema_version TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_reports_run ON research_reports(research_run_id);
CREATE TABLE IF NOT EXISTS reviews (
    review_id TEXT PRIMARY KEY,
    research_run_id TEXT NOT NULL REFERENCES research_runs(research_run_id),
    report_id TEXT NOT NULL REFERENCES research_reports(report_id),
    passed INTEGER NOT NULL,
    reviewed_at TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_reviews_run ON reviews(research_run_id, reviewed_at);
CREATE TABLE IF NOT EXISTS review_findings (
    review_id TEXT NOT NULL REFERENCES reviews(review_id),
    review_finding_id TEXT NOT NULL,
    research_run_id TEXT NOT NULL,
    code TEXT NOT NULL,
    severity TEXT NOT NULL,
    payload TEXT NOT NULL,
    PRIMARY KEY (review_id, review_finding_id)
);
CREATE INDEX IF NOT EXISTS ix_review_findings_run ON review_findings(research_run_id, severity);
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
        elif existing < SCHEMA_VERSION:
            # Schema changes are additive (CREATE ... IF NOT EXISTS), applied above.
            self.conn.execute(
                "UPDATE schema_meta SET value = ? WHERE key = 'schema_version'",
                (str(SCHEMA_VERSION),),
            )

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
        if run.status is ResearchStatus.COMPLETED:
            # Review gate: only a run whose latest review passed may be completed.
            row = self.db.conn.execute(
                "SELECT passed FROM reviews WHERE research_run_id = ? "
                "ORDER BY reviewed_at DESC, rowid DESC LIMIT 1",
                (run.research_run_id,),
            ).fetchone()
            if row is None or not row["passed"]:
                raise ReviewGateError(
                    f"research run {run.research_run_id} has no passing review; cannot complete"
                )
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

    def find_by_source_url(self, company_id: str, source_url: str) -> list[SourceDocument]:
        rows = self.db.conn.execute(
            "SELECT payload FROM source_documents WHERE company_id = ? "
            "AND json_extract(payload, '$.source_url') = ? "
            "ORDER BY json_extract(payload, '$.version'), published_at",
            (company_id, source_url),
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


class SqliteNormalizedDocumentRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def add_normalized(self, doc: NormalizedDocument) -> None:
        row = self.db.conn.execute(
            "SELECT payload FROM normalized_documents WHERE document_id = ? AND parser_name = ? "
            "AND parser_version = ?",
            (doc.document_id, doc.parser_name, doc.parser_version),
        ).fetchone()
        payload = doc.model_dump_json()
        if row is not None:
            if row["payload"] != payload:
                raise ImmutableRecordError(
                    f"{doc.document_id} parsed by {doc.parser_name} {doc.parser_version} "
                    "already stored with different output (parser is not deterministic?)"
                )
            return
        self.db.conn.execute(
            "INSERT INTO normalized_documents(document_id, parser_name, parser_version, payload) "
            "VALUES (?, ?, ?, ?)",
            (doc.document_id, doc.parser_name, doc.parser_version, payload),
        )

    def get_normalized(self, document_id: str) -> NormalizedDocument:
        """Latest parse of a document (highest parser version, lexicographic)."""
        row = self.db.conn.execute(
            "SELECT payload FROM normalized_documents WHERE document_id = ? "
            "ORDER BY parser_version DESC, parser_name LIMIT 1",
            (document_id,),
        ).fetchone()
        return _load(NormalizedDocument, row, f"normalized document {document_id}")


class SqliteSnapshotRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def add_snapshot(self, snapshot: ResearchSnapshot) -> None:
        with self.db.transaction():
            _insert_immutable(
                self.db,
                "research_snapshots",
                "research_run_id",
                {
                    "research_run_id": snapshot.research_run_id,
                    "snapshot_id": snapshot.snapshot_id,
                    "as_of": ts(snapshot.as_of),
                    "payload": snapshot.model_dump_json(),
                },
                "snapshot for run",
            )
            exists = self.db.conn.execute(
                "SELECT 1 FROM snapshot_documents WHERE research_run_id = ? LIMIT 1",
                (snapshot.research_run_id,),
            ).fetchone()
            if exists is None:
                self.db.conn.executemany(
                    "INSERT INTO snapshot_documents(research_run_id, ordinal, document_id) "
                    "VALUES (?, ?, ?)",
                    [
                        (snapshot.research_run_id, i, doc_id)
                        for i, doc_id in enumerate(snapshot.document_ids)
                    ],
                )

    def get_snapshot(self, snapshot_id: str) -> ResearchSnapshot:
        row = self.db.conn.execute(
            "SELECT payload FROM research_snapshots WHERE snapshot_id = ? LIMIT 1", (snapshot_id,)
        ).fetchone()
        return _load(ResearchSnapshot, row, f"snapshot {snapshot_id}")

    def snapshot_for_run(self, research_run_id: str) -> ResearchSnapshot | None:
        row = self.db.conn.execute(
            "SELECT payload FROM research_snapshots WHERE research_run_id = ?", (research_run_id,)
        ).fetchone()
        return ResearchSnapshot.model_validate_json(row["payload"]) if row else None

    def member_ids(self, research_run_id: str) -> list[str]:
        rows = self.db.conn.execute(
            "SELECT document_id FROM snapshot_documents WHERE research_run_id = ? ORDER BY ordinal",
            (research_run_id,),
        )
        return [r["document_id"] for r in rows]


class SqlitePeerSetRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def add_peer_set(self, peer_set: PeerSet) -> None:
        latest = self.db.conn.execute(
            "SELECT MAX(version) AS v FROM peer_sets WHERE name = ?", (peer_set.name,)
        ).fetchone()["v"]
        if latest is not None and peer_set.version != latest + 1:
            if (
                peer_set.version <= latest
                and self.get_version(peer_set.name, peer_set.version) == peer_set
            ):
                return
            raise ImmutableRecordError(
                f"peer set {peer_set.name}: expected version {latest + 1}, got {peer_set.version}"
            )
        if latest is None and peer_set.version != 1:
            raise ImmutableRecordError("the first peer set version must be 1")
        self.db.conn.execute(
            "INSERT INTO peer_sets(name, version, target_company_id, effective_from, payload) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                peer_set.name,
                peer_set.version,
                peer_set.target_company_id,
                ts(peer_set.effective_from),
                peer_set.model_dump_json(),
            ),
        )

    def get_version(self, name: str, version: int) -> PeerSet:
        row = self.db.conn.execute(
            "SELECT payload FROM peer_sets WHERE name = ? AND version = ?", (name, version)
        ).fetchone()
        return _load(PeerSet, row, f"peer set {name}:v{version}")

    def effective_as_of(self, name: str, as_of: datetime) -> PeerSet | None:
        """Latest version whose effective_from is at or before as_of."""
        row = self.db.conn.execute(
            "SELECT payload FROM peer_sets WHERE name = ? AND effective_from <= ? "
            "ORDER BY version DESC LIMIT 1",
            (name, ts(as_of)),
        ).fetchone()
        return PeerSet.model_validate_json(row["payload"]) if row else None

    def history(self, name: str) -> list[PeerSet]:
        rows = self.db.conn.execute(
            "SELECT payload FROM peer_sets WHERE name = ? ORDER BY version", (name,)
        )
        return [PeerSet.model_validate_json(r["payload"]) for r in rows]


class SqliteReportRepository:
    """Reports and reviews are insert-only; a re-review is a new review record."""

    def __init__(self, db: Database) -> None:
        self.db = db

    def add_report(self, report: ResearchReport) -> None:
        _insert_immutable(
            self.db,
            "research_reports",
            "report_id",
            {
                "report_id": report.report_id,
                "research_run_id": report.research_run_id,
                "schema_version": report.schema_version,
                "payload": report.model_dump_json(),
            },
            "report",
        )

    def get_report(self, report_id: str) -> ResearchReport:
        row = self.db.conn.execute(
            "SELECT payload FROM research_reports WHERE report_id = ?", (report_id,)
        ).fetchone()
        return _load(ResearchReport, row, f"report {report_id}")

    def reports_for_run(self, research_run_id: str) -> list[ResearchReport]:
        rows = self.db.conn.execute(
            "SELECT payload FROM research_reports WHERE research_run_id = ? ORDER BY rowid",
            (research_run_id,),
        )
        return [ResearchReport.model_validate_json(r["payload"]) for r in rows]

    def add_review(self, review: ReviewResult) -> None:
        with self.db.transaction():
            _insert_immutable(
                self.db,
                "reviews",
                "review_id",
                {
                    "review_id": review.review_id,
                    "research_run_id": review.research_run_id,
                    "report_id": review.report_id,
                    "passed": int(review.passed),
                    "reviewed_at": ts(review.reviewed_at),
                    "payload": review.model_dump_json(),
                },
                "review",
            )
            self.db.conn.executemany(
                "INSERT OR IGNORE INTO review_findings(review_id, review_finding_id, "
                "research_run_id, code, severity, payload) VALUES (?, ?, ?, ?, ?, ?)",
                [
                    (
                        review.review_id,
                        f.review_finding_id,
                        review.research_run_id,
                        f.code,
                        f.severity.value,
                        f.model_dump_json(),
                    )
                    for f in review.findings
                ],
            )

    def latest_review(self, research_run_id: str) -> ReviewResult | None:
        row = self.db.conn.execute(
            "SELECT payload FROM reviews WHERE research_run_id = ? "
            "ORDER BY reviewed_at DESC, rowid DESC LIMIT 1",
            (research_run_id,),
        ).fetchone()
        return ReviewResult.model_validate_json(row["payload"]) if row else None

    def review_findings(self, review_id: str) -> list[ReviewFinding]:
        rows = self.db.conn.execute(
            "SELECT payload FROM review_findings WHERE review_id = ? ORDER BY rowid", (review_id,)
        )
        return [ReviewFinding.model_validate_json(r["payload"]) for r in rows]


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
        self.normalized = SqliteNormalizedDocumentRepository(db)
        self.snapshots = SqliteSnapshotRepository(db)
        self.peer_sets = SqlitePeerSetRepository(db)
        self.reports = SqliteReportRepository(db)


def open_database(path: Path | str = ":memory:") -> Repositories:
    db = Database(path)
    db.init_schema()
    return Repositories(db)
