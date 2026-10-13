"""HTML parser (filings, earnings releases) built on the standard-library HTMLParser."""

from __future__ import annotations

from html.parser import HTMLParser

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

BLOCK_TAGS = frozenset(
    {
        "p", "div", "li", "ul", "ol", "br", "section", "article", "header", "footer",
        "blockquote", "pre", "dd", "dt", "center", "body", "hr",
    }
)  # fmt: skip
HEADING_TAGS = {"h1": 1, "h2": 2, "h3": 3, "h4": 4, "h5": 5, "h6": 6}
SKIP_TAGS = frozenset({"script", "style", "noscript", "template", "svg"})


class _BlockExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[Block] = []
        self.title = ""
        self._buffer: list[str] = []
        self._skip_depth = 0
        self._in_title = False
        self._heading_level: int | None = None
        self._table_depth = 0
        self._rows: list[list[str]] = []
        self._cell: list[str] | None = None

    # -- text accumulation -------------------------------------------------
    def _flush(self) -> None:
        text = clean_text("".join(self._buffer))
        self._buffer = []
        if not text:
            return
        if self._heading_level is not None:
            self.blocks.append(Block("heading", text, level=self._heading_level))
            return
        level = heading_level(text)
        if level is not None:
            self.blocks.append(Block("heading", text, level=level))
        else:
            self.blocks.append(Block("para", text))

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in SKIP_TAGS:
            self._skip_depth += 1
            return
        if tag == "title":
            self._in_title = True
            return
        if self._skip_depth:
            return
        if tag == "table":
            if self._table_depth == 0:
                self._flush()
                self._rows = []
            self._table_depth += 1
        elif self._table_depth:
            if tag == "tr":
                self._rows.append([])
            elif tag in ("td", "th"):
                self._cell = []
        elif tag in HEADING_TAGS:
            self._flush()
            self._heading_level = HEADING_TAGS[tag]
        elif tag in BLOCK_TAGS:
            self._flush()

    def handle_endtag(self, tag: str) -> None:
        if tag in SKIP_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if tag == "title":
            self._in_title = False
            return
        if self._skip_depth:
            return
        if self._table_depth:
            if tag in ("td", "th") and self._cell is not None:
                if self._rows:
                    self._rows[-1].append(clean_text("".join(self._cell)))
                self._cell = None
            elif tag == "table":
                self._table_depth -= 1
                if self._table_depth == 0:
                    self._emit_table()
            return
        if tag in HEADING_TAGS:
            self._flush()
            self._heading_level = None
        elif tag in BLOCK_TAGS:
            self._flush()

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data
        elif self._skip_depth:
            return
        elif self._table_depth:
            if self._cell is not None:
                self._cell.append(data)
        else:
            self._buffer.append(data)

    def _emit_table(self) -> None:
        rows = tuple(tuple(c for c in row) for row in self._rows if any(c for c in row))
        self._rows = []
        if not rows:
            return
        text = "\n".join(" | ".join(c for c in row if c) for row in rows)
        self.blocks.append(Block("table", text, rows=rows))

    def close(self) -> None:
        super().close()
        self._flush()


class HtmlDocumentParser:
    name = "html"
    version = "1.0.0"

    def parse(self, document_id: str, content: bytes) -> NormalizedDocument:
        text = decode(document_id, content)
        if "<" not in text or ">" not in text:
            raise ParseError(document_id, ParseErrorCode.MALFORMED, "no markup found")
        extractor = _BlockExtractor()
        try:
            extractor.feed(text)
            extractor.close()
        except (AssertionError, ValueError) as exc:  # pragma: no cover - HTMLParser is lenient
            raise ParseError(document_id, ParseErrorCode.MALFORMED, str(exc)) from exc
        return build_document(
            document_id,
            content,
            extractor.blocks,
            self.name,
            self.version,
            title=clean_text(extractor.title),
        )
