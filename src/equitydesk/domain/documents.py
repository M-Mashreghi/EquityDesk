"""Source document metadata (raw payloads live in a separate store referenced by hash)."""

from __future__ import annotations

from datetime import date
from enum import StrEnum

from pydantic import Field

from equitydesk.domain.common import DomainModel, UtcDateTime


class SourceType(StrEnum):
    REGULATORY_FILING = "regulatory_filing"
    ISSUER_IR = "issuer_ir"
    ISSUER_PRESENTATION = "issuer_presentation"
    MARKET_DATA = "market_data"
    SECONDARY = "secondary"


# Lower number = more authoritative. A lower-priority source must never silently override
# a higher-priority one.
SOURCE_PRIORITY: dict[SourceType, int] = {
    SourceType.REGULATORY_FILING: 1,
    SourceType.ISSUER_IR: 2,
    SourceType.ISSUER_PRESENTATION: 3,
    SourceType.MARKET_DATA: 4,
    SourceType.SECONDARY: 5,
}


class SourceDocument(DomainModel):
    document_id: str = Field(min_length=1)
    company_id: str = Field(min_length=1)
    source_type: SourceType
    document_type: str = Field(min_length=1, description="e.g. 10-K, 10-Q, 8-K, earnings_release")
    title: str = ""
    source_url: str | None = None
    official_id: str | None = Field(default=None, description="e.g. SEC accession number")
    published_at: UtcDateTime
    retrieved_at: UtcDateTime
    period_end: date | None = None
    content_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    raw_ref: str | None = Field(default=None, description="raw payload store reference")
    mime_type: str | None = None
    parser_version: str | None = None
    version: int = Field(default=1, ge=1)
    amends_document_id: str | None = None
    supersedes_document_id: str | None = None

    @property
    def priority(self) -> int:
        return SOURCE_PRIORITY[self.source_type]
