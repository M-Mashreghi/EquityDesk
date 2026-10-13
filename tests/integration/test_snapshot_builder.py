from __future__ import annotations

import random
from datetime import date
from pathlib import Path

import pytest

import factories as f
from equitydesk.application.document_store import DocumentIngestionService
from equitydesk.application.snapshot import SnapshotBuilder
from equitydesk.domain.documents import SourceType
from equitydesk.domain.provenance import compute_content_hash
from equitydesk.domain.snapshot import select_snapshot_documents
from equitydesk.infrastructure.db.raw_store import FilesystemRawStore
from equitydesk.infrastructure.db.sqlite import Repositories, open_database

pytestmark = pytest.mark.integration

IR_URL = "https://www.apple.com/newsroom/2024/02/apple-reports-first-quarter-results/"


def _filing(doc_id: str, accession: str, form: str, published, period: date, body: bytes):
    return f.document(
        document_id=doc_id,
        official_id=accession,
        document_type=form,
        published_at=published,
        period_end=period,
        content_hash=compute_content_hash(body),
    ), body


def _ir(doc_id: str, published, body: bytes):
    return f.document(
        document_id=doc_id,
        source_type=SourceType.ISSUER_IR,
        document_type="earnings_release",
        source_url=IR_URL,
        official_id=None,
        published_at=published,
        period_end=None,
        content_hash=compute_content_hash(body),
    ), body


CORPUS = [
    _filing("10k_fy23", "acc-1", "10-K", f.dt(2023, 11, 3, 4), date(2023, 9, 30), b"10-K FY23"),
    _ir("ir_q1_v1", f.dt(2024, 2, 1, 21, 30), b"Q1 release v1"),
    _filing("10q_q1", "acc-2", "10-Q", f.dt(2024, 2, 2, 5), date(2023, 12, 30), b"10-Q Q1"),
    _ir("ir_q1_v2", f.dt(2024, 2, 5, 15), b"Q1 release v2 (corrected)"),
    _filing("10k_fy23_a", "acc-3", "10-K/A", f.dt(2024, 3, 15, 13), date(2023, 9, 30), b"10-K/A"),
    _filing("8k_copy", "acc-4", "8-K", f.dt(2024, 2, 2, 6), date(2024, 2, 1), b"Q1 release v1"),
    _filing("10q_q2", "acc-5", "10-Q", f.dt(2024, 5, 3, 4), date(2024, 3, 30), b"10-Q Q2"),
]


@pytest.fixture
def env(tmp_path: Path) -> tuple[Repositories, SnapshotBuilder]:
    repos = open_database()
    repos.companies.save_company(f.company())
    ingest = DocumentIngestionService(repos.documents, FilesystemRawStore(tmp_path))
    for doc, body in CORPUS:
        ingest.ingest(doc, body)
    return repos, SnapshotBuilder(repos.documents, repos.snapshots)


def _run(repos: Repositories, run_id: str, as_of):
    run = f.run(research_run_id=run_id, as_of=as_of, created_at=f.dt(2026, 10, 13))
    repos.runs.add_run(run)
    return run


def test_future_documents_and_later_versions_are_excluded(env) -> None:
    repos, builder = env
    snap = builder.build(_run(repos, "r1", f.dt(2024, 2, 3)))
    assert snap.document_ids == ("10k_fy23", "ir_q1_v1", "10q_q1")
    assert set(snap.excluded_future_ids) == {"ir_q1_v2", "10k_fy23_a", "10q_q2"}
    # the 8-K copy of the release is byte-identical and was rejected at ingestion
    assert "8k_copy" not in snap.document_ids


def test_corrected_version_replaces_old_once_visible(env) -> None:
    repos, builder = env
    snap = builder.build(_run(repos, "r2", f.dt(2024, 4, 1)))
    assert snap.document_ids == ("10k_fy23", "10q_q1", "ir_q1_v2", "10k_fy23_a")
    assert snap.excluded_superseded_ids == ("ir_q1_v1",)
    # amendment and original are both kept
    assert {"10k_fy23", "10k_fy23_a"} <= set(snap.document_ids)


def test_snapshot_is_deterministic_and_persisted(env) -> None:
    repos, builder = env
    a = builder.build(_run(repos, "ra", f.dt(2024, 4, 1)))
    b = builder.build(_run(repos, "rb", f.dt(2024, 4, 1)))
    assert a.snapshot_id == b.snapshot_id and a.document_ids == b.document_ids
    assert repos.snapshots.member_ids("ra") == list(a.document_ids)
    assert repos.snapshots.snapshot_for_run("ra") == a
    assert builder.build(repos.runs.get_run("ra")) == a  # idempotent rebuild returns stored
    c = builder.build(_run(repos, "rc", f.dt(2024, 6, 1)))
    assert c.snapshot_id != a.snapshot_id


def test_snapshot_unchanged_after_new_documents_arrive(env, tmp_path: Path) -> None:
    repos, builder = env
    first = builder.build(_run(repos, "r_hist", f.dt(2024, 4, 1)))
    ingest = DocumentIngestionService(repos.documents, FilesystemRawStore(tmp_path))
    late = _filing("10q_q3", "acc-6", "10-Q", f.dt(2024, 8, 2, 4), date(2024, 6, 29), b"Q3")
    ingest.ingest(*late)
    assert builder.build(repos.runs.get_run("r_hist")) == first
    assert repos.snapshots.member_ids("r_hist") == list(first.document_ids)


def test_selection_is_order_independent() -> None:
    docs = [d for d, _ in CORPUS if d.document_id != "8k_copy"]
    expected = select_snapshot_documents(docs, f.dt(2024, 4, 1))
    for seed in range(5):
        shuffled = docs[:]
        random.Random(seed).shuffle(shuffled)
        assert select_snapshot_documents(shuffled, f.dt(2024, 4, 1)) == expected


def test_duplicate_content_collapses_to_earliest_publication() -> None:
    a, _ = _ir("late_copy", f.dt(2024, 2, 3), b"same")
    b, _ = _filing("early", "acc-9", "8-K", f.dt(2024, 2, 1), date(2024, 2, 1), b"same")
    members, _, _, dups = select_snapshot_documents([a, b], f.dt(2024, 3, 1))
    assert [m.document_id for m in members] == ["early"] and dups == ["late_copy"]
