"""Normalized document model: sections, chunks and tables with stable source locators.

Chunk ids (`<document_id>:s<section>:p<paragraph>`) are the locators that research evidence
cites, so they must be deterministic for a given document content and parser version.
"""

from __future__ import annotations

from pydantic import Field, model_validator

from equitydesk.domain.common import DomainModel


class Chunk(DomainModel):
    chunk_id: str = Field(min_length=1)
    section_id: str
    ordinal: int = Field(ge=0)
    text: str = Field(min_length=1)
    page: int | None = Field(default=None, ge=1)
    char_start: int = Field(ge=0)
    char_end: int = Field(ge=0)
    is_table: bool = False


class Section(DomainModel):
    section_id: str
    heading: str
    level: int = Field(ge=0, le=6)
    ordinal: int = Field(ge=0)
    item_code: str | None = Field(default=None, description="SEC item code, e.g. 1A, 7")
    chunk_ids: tuple[str, ...] = ()


class Table(DomainModel):
    table_id: str
    section_id: str
    chunk_id: str
    rows: tuple[tuple[str, ...], ...]


class NormalizedDocument(DomainModel):
    document_id: str = Field(min_length=1)
    parser_name: str
    parser_version: str
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    title: str = ""
    sections: tuple[Section, ...]
    chunks: tuple[Chunk, ...]
    tables: tuple[Table, ...] = ()
    plain_text: str

    @model_validator(mode="after")
    def _consistent(self) -> NormalizedDocument:
        ids = [c.chunk_id for c in self.chunks]
        if len(set(ids)) != len(ids):
            raise ValueError("chunk ids must be unique")
        for c in self.chunks:
            if self.plain_text[c.char_start : c.char_end] != c.text:
                raise ValueError(f"chunk {c.chunk_id} offsets do not match plain_text")
        return self

    def chunk(self, chunk_id: str) -> Chunk | None:
        return next((c for c in self.chunks if c.chunk_id == chunk_id), None)

    def section(self, section_id: str) -> Section | None:
        return next((s for s in self.sections if s.section_id == section_id), None)

    def section_for_item(self, item_code: str) -> Section | None:
        return next((s for s in self.sections if s.item_code == item_code.upper()), None)

    def chunks_in(self, section_id: str) -> list[Chunk]:
        return [c for c in self.chunks if c.section_id == section_id]
