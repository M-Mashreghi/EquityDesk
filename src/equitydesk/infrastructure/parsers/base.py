"""Parser contracts and the shared block -> NormalizedDocument builder."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol

from equitydesk.domain.normalized import Chunk, NormalizedDocument, Section, Table
from equitydesk.domain.provenance import compute_content_hash

MAX_CHUNK_CHARS = 2000

_ITEM_HEADING = re.compile(r"^item\s+(\d{1,2}[a-c]?)\s*[.:\-—]", re.IGNORECASE)
_PART_HEADING = re.compile(r"^part\s+[ivx]+\b\.?$", re.IGNORECASE)
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


class ParseErrorCode(StrEnum):
    EMPTY_CONTENT = "empty_content"
    DECODE_ERROR = "decode_error"
    BINARY_CONTENT = "binary_content"
    UNSUPPORTED_FORMAT = "unsupported_format"
    NO_TEXT = "no_text"
    MALFORMED = "malformed"


class ParseError(ValueError):
    """Structured parser failure: never a partially-parsed document."""

    def __init__(self, document_id: str, code: ParseErrorCode, detail: str = "") -> None:
        self.document_id = document_id
        self.code = code
        self.detail = detail
        super().__init__(f"{document_id}: {code.value}" + (f" ({detail})" if detail else ""))


class DocumentParser(Protocol):
    name: str
    version: str

    def parse(self, document_id: str, content: bytes) -> NormalizedDocument: ...


@dataclass
class Block:
    kind: str  # "heading" | "para" | "table"
    text: str
    level: int = 0
    page: int | None = None
    rows: tuple[tuple[str, ...], ...] = field(default_factory=tuple)


def clean_text(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).replace(" ", " ")
    return " ".join(text.split())


def decode(document_id: str, content: bytes) -> str:
    if not content or not content.strip():
        raise ParseError(document_id, ParseErrorCode.EMPTY_CONTENT)
    if b"\x00" in content[:4096]:
        raise ParseError(document_id, ParseErrorCode.BINARY_CONTENT, "NUL bytes in content")
    for encoding in ("utf-8", "cp1252"):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ParseError(document_id, ParseErrorCode.DECODE_ERROR)


def heading_level(text: str) -> int | None:
    """Detect SEC-style structural headings ("PART I", "Item 1A. Risk Factors")."""
    if len(text) > 200:
        return None
    if _PART_HEADING.match(text):
        return 1
    if _ITEM_HEADING.match(text):
        return 2
    return None


def item_code(heading: str) -> str | None:
    m = _ITEM_HEADING.match(heading)
    return m.group(1).upper() if m else None


def split_long(text: str, limit: int = MAX_CHUNK_CHARS) -> list[str]:
    if len(text) <= limit:
        return [text]
    parts: list[str] = []
    current = ""
    for sentence in _SENTENCE_END.split(text):
        while len(sentence) > limit:  # pathological sentence without boundaries
            if current:
                parts.append(current)
                current = ""
            parts.append(sentence[:limit])
            sentence = sentence[limit:].lstrip()
        if current and len(current) + 1 + len(sentence) > limit:
            parts.append(current)
            current = sentence
        else:
            current = f"{current} {sentence}".strip()
    if current:
        parts.append(current)
    return parts


@dataclass
class _SectionBuffer:
    section_id: str
    heading: str
    level: int
    item_code: str | None
    chunk_ids: list[str] = field(default_factory=list)


def build_document(
    document_id: str,
    content: bytes,
    blocks: list[Block],
    parser_name: str,
    parser_version: str,
    title: str = "",
) -> NormalizedDocument:
    blocks = [b for b in blocks if b.text]
    if not blocks:
        raise ParseError(document_id, ParseErrorCode.NO_TEXT, "no text blocks extracted")

    sections = [_SectionBuffer("s0", "(preamble)", 0, None)]
    chunks: list[Chunk] = []
    tables: list[Table] = []
    pieces: list[str] = []
    offset = 0
    para_counter = 0

    def add_chunk(text: str, page: int | None, is_table: bool) -> str:
        nonlocal offset, para_counter
        sec = sections[-1]
        chunk_id = f"{document_id}:{sec.section_id}:p{para_counter}"
        start = offset + (2 if pieces else 0)
        chunks.append(
            Chunk(
                chunk_id=chunk_id,
                section_id=sec.section_id,
                ordinal=para_counter,
                text=text,
                page=page,
                char_start=start,
                char_end=start + len(text),
                is_table=is_table,
            )
        )
        pieces.append(text)
        offset = start + len(text)
        para_counter += 1
        sec.chunk_ids.append(chunk_id)
        return chunk_id

    for block in blocks:
        if block.kind == "heading":
            sections.append(
                _SectionBuffer(f"s{len(sections)}", block.text, block.level, item_code(block.text))
            )
            para_counter = 0
        elif block.kind == "table":
            chunk_id = add_chunk(block.text, block.page, True)
            tables.append(
                Table(
                    table_id=f"{document_id}:t{len(tables)}",
                    section_id=sections[-1].section_id,
                    chunk_id=chunk_id,
                    rows=block.rows,
                )
            )
        else:
            for part in split_long(block.text):
                add_chunk(part, block.page, False)

    if not chunks:
        raise ParseError(document_id, ParseErrorCode.NO_TEXT, "headings only, no body text")

    section_models = tuple(
        Section(
            section_id=s.section_id,
            heading=s.heading,
            level=s.level,
            ordinal=i,
            item_code=s.item_code,
            chunk_ids=tuple(s.chunk_ids),
        )
        for i, s in enumerate(sections)
        if s.chunk_ids or i > 0
    )
    return NormalizedDocument(
        document_id=document_id,
        parser_name=parser_name,
        parser_version=parser_version,
        content_hash=compute_content_hash(content),
        title=title,
        sections=section_models,
        chunks=tuple(chunks),
        tables=tuple(tables),
        plain_text="\n\n".join(pieces),
    )
