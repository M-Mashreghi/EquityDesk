"""Build and persist the point-in-time document snapshot of a research run."""

from __future__ import annotations

from typing import Protocol

from equitydesk.application.ports import DocumentRepository
from equitydesk.domain.documents import SourceDocument
from equitydesk.domain.research import ResearchRun
from equitydesk.domain.snapshot import (
    SNAPSHOT_BUILDER_VERSION,
    ResearchSnapshot,
    select_snapshot_documents,
    snapshot_id_for,
)


class SnapshotRepository(Protocol):
    def add_snapshot(self, snapshot: ResearchSnapshot) -> None: ...
    def get_snapshot(self, snapshot_id: str) -> ResearchSnapshot: ...
    def snapshot_for_run(self, research_run_id: str) -> ResearchSnapshot | None: ...


class SnapshotBuilder:
    def __init__(self, documents: DocumentRepository, snapshots: SnapshotRepository) -> None:
        self.documents = documents
        self.snapshots = snapshots

    def build(self, run: ResearchRun) -> ResearchSnapshot:
        """Compute (or return the already persisted) snapshot for the run."""
        existing = self.snapshots.snapshot_for_run(run.research_run_id)
        if existing is not None:
            return existing
        candidates = self.documents.documents_for_company(run.company_id)
        members, future, superseded, duplicates = select_snapshot_documents(candidates, run.as_of)
        ids = tuple(d.document_id for d in members)
        snapshot = ResearchSnapshot(
            snapshot_id=snapshot_id_for(run.company_id, run.as_of, ids, SNAPSHOT_BUILDER_VERSION),
            research_run_id=run.research_run_id,
            company_id=run.company_id,
            as_of=run.as_of,
            document_ids=ids,
            excluded_future_ids=tuple(future),
            excluded_superseded_ids=tuple(superseded),
            excluded_duplicate_ids=tuple(duplicates),
        )
        self.snapshots.add_snapshot(snapshot)
        return snapshot

    def member_documents(self, snapshot: ResearchSnapshot) -> list[SourceDocument]:
        return [self.documents.get_document(doc_id) for doc_id in snapshot.document_ids]
