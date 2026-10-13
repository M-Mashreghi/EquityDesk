from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

import factories as f
from equitydesk.application.document_store import DocumentIngestionService, IngestStatus
from equitydesk.domain.documents import SourceType
from equitydesk.domain.provenance import ProvenanceError, compute_content_hash
from equitydesk.infrastructure.db.raw_store import FilesystemRawStore
from equitydesk.infrastructure.db.sqlite import Repositories, open_database

pytestmark = pytest.mark.integration

IR_URL = "https://www.apple.com/newsroom/2023/11/apple-reports-fourth-quarter-results/"


@pytest.fixture
def env(tmp_path: Path) -> tuple[DocumentIngestionService, Repositories]:
    repos = open_database()
    repos.companies.save_company(f.company())
    return DocumentIngestionService(repos.documents, FilesystemRawStore(tmp_path / "raw")), repos


def filing(content: bytes, **kw: object):
    data: dict[str, object] = {"content_hash": compute_content_hash(content)}
    data.update(kw)
    return f.document(**data)


def ir_doc(content: bytes, doc_id: str, published):
    return f.document(
        document_id=doc_id,
        source_type=SourceType.ISSUER_IR,
        document_type="earnings_release",
        source_url=IR_URL,
        official_id=None,
        published_at=published,
        retrieved_at=f.dt(2026, 1, 1),
        content_hash=compute_content_hash(content),
        period_end=None,
    )


def test_store_and_reload_raw_content(env) -> None:
    svc, _ = env
    result = svc.ingest(filing(b"10-K body"), b"10-K body")
    assert result.status is IngestStatus.STORED
    assert result.document.raw_ref == compute_content_hash(b"10-K body")
    assert svc.load_content(result.document) == b"10-K body"


def test_hash_mismatch_rejected(env) -> None:
    svc, _ = env
    with pytest.raises(ProvenanceError):
        svc.ingest(filing(b"original"), b"tampered")


def test_identical_content_is_deduplicated(env) -> None:
    svc, repos = env
    first = svc.ingest(filing(b"same"), b"same").document
    again = svc.ingest(filing(b"same"), b"same")
    assert again.status is IngestStatus.DUPLICATE and again.document == first
    # same bytes from another channel published later -> dedupe to existing record
    other = ir_doc(b"same", "ir_copy", f.dt(2024, 1, 1))
    dup = svc.ingest(other, b"same")
    assert dup.status is IngestStatus.DUPLICATE and dup.duplicate_of == first.document_id
    assert len(repos.documents.documents_for_company("co_apple")) == 1


def test_earlier_publication_of_same_bytes_is_kept(env) -> None:
    svc, repos = env
    svc.ingest(filing(b"x"), b"x")  # published 2023-11-03
    earlier = svc.ingest(ir_doc(b"x", "ir_early", f.dt(2023, 11, 2, 20, 30)), b"x")
    assert earlier.status is IngestStatus.STORED and earlier.duplicate_of == "doc_10k_2023"
    assert len(repos.documents.documents_for_company("co_apple")) == 2


def test_official_id_conflict_is_rejected(env) -> None:
    svc, _ = env
    svc.ingest(filing(b"v1"), b"v1")
    with pytest.raises(ProvenanceError, match="official id"):
        svc.ingest(filing(b"v2", document_id="other"), b"v2")


def test_changed_ir_page_becomes_new_version_without_overwrite(env) -> None:
    svc, repos = env
    v1 = svc.ingest(ir_doc(b"release v1", "ir_v1", f.dt(2023, 11, 2, 20, 30)), b"release v1")
    v2 = svc.ingest(ir_doc(b"release v2", "ir_v2", f.dt(2023, 11, 3, 12)), b"release v2")
    assert v2.status is IngestStatus.NEW_VERSION
    assert v2.document.version == 2 and v2.document.supersedes_document_id == "ir_v1"
    # historical version remains queryable, untouched
    assert repos.documents.get_document("ir_v1") == v1.document
    assert svc.load_content(repos.documents.get_document("ir_v1")) == b"release v1"
    assert [d.document_id for d in svc.version_chain("ir_v2")] == ["ir_v2", "ir_v1"]
    v3 = svc.ingest(ir_doc(b"release v3", "ir_v3", f.dt(2023, 11, 4)), b"release v3")
    assert v3.document.version == 3 and v3.document.supersedes_document_id == "ir_v2"


def test_backdated_new_version_rejected(env) -> None:
    svc, _ = env
    svc.ingest(ir_doc(b"v1", "ir_v1", f.dt(2023, 11, 3)), b"v1")
    with pytest.raises(ProvenanceError, match="before"):
        svc.ingest(ir_doc(b"v2", "ir_v2", f.dt(2023, 11, 1)), b"v2")


def test_amendment_links_to_original(env) -> None:
    svc, repos = env
    original = svc.ingest(filing(b"10-K"), b"10-K").document
    amendment = filing(
        b"10-K/A",
        document_id="sec_amend",
        document_type="10-K/A",
        official_id="0000320193-24-000050",
        published_at=f.dt(2024, 3, 15, 13),
        period_end=date(2023, 9, 30),
    )
    result = svc.ingest(amendment, b"10-K/A")
    assert result.status is IngestStatus.AMENDMENT
    assert result.document.amends_document_id == original.document_id
    # original stays queryable and unchanged
    assert repos.documents.get_document(original.document_id) == original


def test_amendment_without_original_is_flagged(env) -> None:
    svc, _ = env
    result = svc.ingest(
        filing(b"10-Q/A", document_id="a", document_type="10-Q/A", official_id="acc-x"), b"10-Q/A"
    )
    assert result.status is IngestStatus.AMENDMENT
    assert result.document.amends_document_id is None
    assert "without a stored original" in result.notes[0]


def test_raw_store_detects_corruption(tmp_path: Path) -> None:
    store = FilesystemRawStore(tmp_path)
    ref = store.put(b"payload")
    assert store.put(b"payload") == ref and store.exists(ref)
    (tmp_path / ref[:2] / ref).write_bytes(b"corrupted")
    with pytest.raises(ProvenanceError):
        store.get(ref)
    with pytest.raises(ValueError):
        store.get("../../etc/passwd")
