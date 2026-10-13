"""Plain-text and paged-text parsers.

`PagedTextParser` handles PDF-like inputs that have already been converted to text with
form-feed (`\\f`) page breaks (the convention of `pdftotext`), keeping page locators.
`PdfParser` wraps a pluggable text-extraction adapter; without one, PDFs fail structurally
instead of being silently skipped or guessed.
"""

from __future__ import annotations

import re
from collections.abc import Callable

from equitydesk.domain.normalized import NormalizedDocument
from equitydesk.infrastructure.parsers.base import (
    Block,
    ParseError,
    ParseErrorCode,
    build_document,
    clean_text,
    decode,
    heading_level,
)

_PARAGRAPH_SPLIT = re.compile(r"\n\s*\n")
_MARKDOWN_HEADING = re.compile(r"^(#{1,6})\s+(.+)$")


def _blocks_from_text(text: str, page: int | None = None) -> list[Block]:
    blocks: list[Block] = []
    for raw in _PARAGRAPH_SPLIT.split(text.replace("\r\n", "\n")):
        lines = [ln.strip() for ln in raw.split("\n") if ln.strip()]
        if not lines:
            continue
        md = _MARKDOWN_HEADING.match(lines[0])
        first = clean_text(lines[0])
        level = len(md.group(1)) if md else heading_level(first)
        if level is not None:
            blocks.append(Block("heading", clean_text(md.group(2)) if md else first, level, page))
            lines = lines[1:]
        if lines:
            blocks.append(Block("para", clean_text(" ".join(lines)), page=page))
    return blocks


class PlainTextParser:
    name = "text"
    version = "1.0.0"

    def parse(self, document_id: str, content: bytes) -> NormalizedDocument:
        text = decode(document_id, content)
        return build_document(
            document_id, content, _blocks_from_text(text), self.name, self.version
        )


class PagedTextParser:
    name = "paged_text"
    version = "1.0.0"

    def parse(self, document_id: str, content: bytes) -> NormalizedDocument:
        text = decode(document_id, content)
        return self.parse_pages(document_id, content, text.split("\f"))

    def parse_pages(self, document_id: str, content: bytes, pages: list[str]) -> NormalizedDocument:
        blocks: list[Block] = []
        for number, page_text in enumerate(pages, start=1):
            blocks.extend(_blocks_from_text(page_text, page=number))
        return build_document(document_id, content, blocks, self.name, self.version)


PdfTextExtractor = Callable[[bytes], list[str]]


class PdfParser:
    """PDF adapter: delegates text extraction (one string per page) to an injected extractor."""

    name = "pdf"
    version = "1.0.0"

    def __init__(self, extractor: PdfTextExtractor | None = None) -> None:
        self.extractor = extractor

    def parse(self, document_id: str, content: bytes) -> NormalizedDocument:
        if not content.startswith(b"%PDF"):
            raise ParseError(document_id, ParseErrorCode.MALFORMED, "missing %PDF header")
        if self.extractor is None:
            raise ParseError(
                document_id, ParseErrorCode.UNSUPPORTED_FORMAT, "no PDF text extractor configured"
            )
        try:
            pages = self.extractor(content)
        except Exception as exc:
            raise ParseError(document_id, ParseErrorCode.MALFORMED, type(exc).__name__) from exc
        doc = PagedTextParser().parse_pages(document_id, content, pages)
        return doc.model_copy(update={"parser_name": self.name, "parser_version": self.version})
