"""Point-in-time source provenance contract.

Rules (see docs/ARCHITECTURE.md "Point-in-time provenance"):
1. A document is visible to a research run iff `published_at <= as_of`. `retrieved_at` is
   audit metadata only: retrieving an old filing today does not make it unavailable historically.
2. Primary documents must carry a source URL or official id plus a SHA-256 content hash.
3. Stored source documents are immutable; content must always re-hash to the stored hash.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime

from equitydesk.domain.common import DomainError
from equitydesk.domain.documents import SourceDocument


class ProvenanceError(DomainError):
    pass


class FutureLeakageError(DomainError):
    def __init__(self, as_of: datetime, offending: Sequence[str]) -> None:
        self.as_of = as_of
        self.offending = tuple(offending)
        super().__init__(
            f"{len(offending)} item(s) published after as_of={as_of.isoformat()}: "
            + ", ".join(offending)
        )


def compute_content_hash(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def verify_content(document: SourceDocument, content: bytes) -> None:
    """Raise if raw content does not match the document's recorded hash."""
    if document.content_hash is None:
        raise ProvenanceError(f"{document.document_id} has no content hash")
    actual = compute_content_hash(content)
    if actual != document.content_hash:
        raise ProvenanceError(
            f"content hash mismatch for {document.document_id}: "
            f"expected {document.content_hash}, got {actual}"
        )


def _require_aware(as_of: datetime) -> None:
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ProvenanceError("as_of must be timezone-aware")


def is_visible(published_at: datetime, as_of: datetime) -> bool:
    _require_aware(as_of)
    return published_at <= as_of


@dataclass(frozen=True)
class PointInTimeView[T]:
    as_of: datetime
    visible: tuple[T, ...]
    excluded_future: tuple[T, ...]


def split_as_of[T](
    items: Iterable[T], as_of: datetime, available_at: Callable[[T], datetime]
) -> PointInTimeView[T]:
    """Partition items into visible / future relative to `as_of`, preserving input order."""
    _require_aware(as_of)
    visible: list[T] = []
    future: list[T] = []
    for item in items:
        (visible if available_at(item) <= as_of else future).append(item)
    return PointInTimeView(as_of, tuple(visible), tuple(future))


def filter_documents_as_of(
    documents: Iterable[SourceDocument], as_of: datetime
) -> PointInTimeView[SourceDocument]:
    return split_as_of(documents, as_of, lambda d: d.published_at)


def assert_no_future_documents(documents: Iterable[SourceDocument], as_of: datetime) -> None:
    view = filter_documents_as_of(documents, as_of)
    if view.excluded_future:
        raise FutureLeakageError(as_of, [d.document_id for d in view.excluded_future])
