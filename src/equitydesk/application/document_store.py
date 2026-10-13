"""Document ingestion: hash verification, deduplication, versions and amendment links.

Nothing is ever overwritten: a changed document becomes a new version that points at the one
it supersedes, and an amended filing points at the original it amends, so every historical
version stays queryable for point-in-time research.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from equitydesk.application.ports import DocumentRepository, RawContentStore
from equitydesk.domain.documents import SourceDocument
from equitydesk.domain.provenance import ProvenanceError, compute_content_hash


class IngestStatus(StrEnum):
    STORED = "stored"
    DUPLICATE = "duplicate"
    NEW_VERSION = "new_version"
    AMENDMENT = "amendment"


@dataclass(frozen=True)
class IngestResult:
    status: IngestStatus
    document: SourceDocument
    duplicate_of: str | None = None
    notes: tuple[str, ...] = ()


def _revalidate(doc: SourceDocument, **updates: Any) -> SourceDocument:
    return SourceDocument.model_validate({**doc.model_dump(), **updates})


class DocumentIngestionService:
    def __init__(self, documents: DocumentRepository, raw_store: RawContentStore) -> None:
        self.documents = documents
        self.raw_store = raw_store

    def ingest(self, document: SourceDocument, content: bytes) -> IngestResult:
        content_hash = compute_content_hash(content)
        if document.content_hash is not None and document.content_hash != content_hash:
            raise ProvenanceError(f"{document.document_id}: content does not match content_hash")
        notes: list[str] = []

        # 1. Official identifiers (e.g. SEC accession numbers) are immutable upstream.
        if document.official_id:
            for existing in self.documents.find_by_official_id(document.official_id):
                if existing.company_id != document.company_id:
                    continue
                if existing.content_hash == content_hash:
                    return IngestResult(IngestStatus.DUPLICATE, existing, existing.document_id)
                raise ProvenanceError(
                    f"official id {document.official_id} already stored with different content"
                )

        # 2. Exact-content dedupe (e.g. a press release also filed as an 8-K exhibit).
        duplicate = self.documents.find_by_hash(document.company_id, content_hash)
        if duplicate is not None:
            if duplicate.published_at <= document.published_at:
                return IngestResult(IngestStatus.DUPLICATE, duplicate, duplicate.document_id)
            # Same bytes known to be public earlier through this source: keep both records so
            # the earlier availability is not lost; the snapshot builder dedupes by hash.
            notes.append(f"same content as {duplicate.document_id}, published earlier here")

        updates: dict[str, Any] = {
            "content_hash": content_hash,
            "raw_ref": self.raw_store.put(content),
        }
        status = IngestStatus.STORED

        # 3. Versions: same URL, new content -> new version superseding the latest one.
        if document.source_url and not document.official_id:
            history = self.documents.find_by_source_url(document.company_id, document.source_url)
            history = [d for d in history if d.content_hash != content_hash]
            if history:
                latest = max(history, key=lambda d: d.version)
                if document.published_at < latest.published_at:
                    raise ProvenanceError(
                        "a new version cannot be published before the version it supersedes"
                    )
                updates |= {
                    "version": latest.version + 1,
                    "supersedes_document_id": latest.document_id,
                }
                status = IngestStatus.NEW_VERSION

        # 4. Amendments: link "X/A" to the original "X" for the same period.
        if document.document_type.endswith("/A") and document.amends_document_id is None:
            original = self._find_original(document)
            if original is not None:
                updates["amends_document_id"] = original.document_id
            else:
                notes.append("amendment without a stored original filing")
            status = IngestStatus.AMENDMENT

        stored = _revalidate(document, **updates)
        self.documents.add_document(stored)
        return IngestResult(
            status, stored, duplicate.document_id if duplicate else None, tuple(notes)
        )

    def _find_original(self, amendment: SourceDocument) -> SourceDocument | None:
        base_form = amendment.document_type.removesuffix("/A")
        candidates = [
            d
            for d in self.documents.documents_for_company(amendment.company_id)
            if d.document_type == base_form
            and d.period_end == amendment.period_end
            and d.published_at <= amendment.published_at
        ]
        return max(candidates, key=lambda d: d.published_at) if candidates else None

    def version_chain(self, document_id: str) -> list[SourceDocument]:
        """The document followed by every earlier version it supersedes (newest first)."""
        chain = [self.documents.get_document(document_id)]
        while chain[-1].supersedes_document_id:
            chain.append(self.documents.get_document(chain[-1].supersedes_document_id))
        return chain

    def load_content(self, document: SourceDocument) -> bytes:
        if document.raw_ref is None:
            raise ProvenanceError(f"{document.document_id} has no stored raw content")
        content = self.raw_store.get(document.raw_ref)
        if document.content_hash and compute_content_hash(content) != document.content_hash:
            raise ProvenanceError(f"{document.document_id}: stored content hash mismatch")
        return content
