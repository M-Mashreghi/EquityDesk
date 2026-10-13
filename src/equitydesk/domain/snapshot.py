"""Point-in-time research snapshot: the exact, reproducible document set a run may use."""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from datetime import datetime

from pydantic import Field

from equitydesk.domain.common import DomainModel, UtcDateTime
from equitydesk.domain.documents import SourceDocument
from equitydesk.domain.provenance import FutureLeakageError, filter_documents_as_of

SNAPSHOT_BUILDER_VERSION = "1"


class ResearchSnapshot(DomainModel):
    snapshot_id: str = Field(min_length=1)
    research_run_id: str = Field(min_length=1)
    company_id: str = Field(min_length=1)
    as_of: UtcDateTime
    document_ids: tuple[str, ...]
    excluded_future_ids: tuple[str, ...] = ()
    excluded_superseded_ids: tuple[str, ...] = ()
    excluded_duplicate_ids: tuple[str, ...] = ()
    builder_version: str = SNAPSHOT_BUILDER_VERSION

    def contains(self, document_id: str) -> bool:
        return document_id in self.document_ids


def snapshot_id_for(
    company_id: str, as_of: datetime, document_ids: Iterable[str], builder_version: str
) -> str:
    """Content-derived id: identical inputs always produce the identical snapshot id."""
    material = "\n".join([company_id, as_of.isoformat(), builder_version, *sorted(document_ids)])
    return "snap_" + hashlib.sha256(material.encode()).hexdigest()[:32]


def select_snapshot_documents(
    documents: Iterable[SourceDocument], as_of: datetime
) -> tuple[list[SourceDocument], list[str], list[str], list[str]]:
    """Return (members, future_ids, superseded_ids, duplicate_ids).

    1. only documents published at or before `as_of`;
    2. of a version chain, only the newest version visible at `as_of`
       (a correction published later must not leak backwards, the old version stays);
    3. byte-identical documents collapse to the earliest publication;
    4. amendments and originals are both kept (they are distinct filings).
    """
    view = filter_documents_as_of(documents, as_of)
    visible = list(view.visible)
    future_ids = sorted(d.document_id for d in view.excluded_future)

    superseded = {d.supersedes_document_id for d in visible if d.supersedes_document_id}
    superseded_ids = sorted(d.document_id for d in visible if d.document_id in superseded)
    current = [d for d in visible if d.document_id not in superseded]

    by_hash: dict[str, SourceDocument] = {}
    duplicate_ids: list[str] = []
    for doc in sorted(current, key=lambda d: (d.published_at, d.document_id)):
        key = doc.content_hash or f"nohash:{doc.document_id}"
        if key in by_hash:
            duplicate_ids.append(doc.document_id)
        else:
            by_hash[key] = doc
    members = sorted(by_hash.values(), key=lambda d: (d.published_at, d.document_id))

    leaked = [d.document_id for d in members if d.published_at > as_of]
    if leaked:  # defensive: the filter above makes this unreachable
        raise FutureLeakageError(as_of, leaked)
    return members, future_ids, superseded_ids, sorted(duplicate_ids)
