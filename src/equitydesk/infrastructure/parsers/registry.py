"""Choose a parser by declared MIME type, falling back to content sniffing."""

from __future__ import annotations

from equitydesk.domain.normalized import NormalizedDocument
from equitydesk.infrastructure.parsers.base import DocumentParser, ParseError, ParseErrorCode
from equitydesk.infrastructure.parsers.html import HtmlDocumentParser
from equitydesk.infrastructure.parsers.text import (
    PagedTextParser,
    PdfParser,
    PdfTextExtractor,
    PlainTextParser,
)


class ParserRegistry:
    def __init__(self, pdf_extractor: PdfTextExtractor | None = None) -> None:
        self.by_mime: dict[str, DocumentParser] = {
            "text/html": HtmlDocumentParser(),
            "application/xhtml+xml": HtmlDocumentParser(),
            "text/plain": PlainTextParser(),
            "text/x-paged": PagedTextParser(),
            "application/pdf": PdfParser(pdf_extractor),
        }

    def parser_for(self, document_id: str, content: bytes, mime_type: str | None) -> DocumentParser:
        if mime_type:
            parser = self.by_mime.get(mime_type.split(";")[0].strip().lower())
            if parser is None:
                raise ParseError(document_id, ParseErrorCode.UNSUPPORTED_FORMAT, mime_type)
            return parser
        head = content[:512].lstrip().lower()
        if head.startswith(b"%pdf"):
            return self.by_mime["application/pdf"]
        if head.startswith((b"<!doctype html", b"<html", b"<?xml")):
            return self.by_mime["text/html"]
        if b"\f" in content:
            return self.by_mime["text/x-paged"]
        return self.by_mime["text/plain"]

    def parse(self, document_id: str, content: bytes, mime_type: str | None) -> NormalizedDocument:
        return self.parser_for(document_id, content, mime_type).parse(document_id, content)
