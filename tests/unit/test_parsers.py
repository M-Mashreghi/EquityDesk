from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from equitydesk.infrastructure.parsers.base import ParseError, ParseErrorCode, split_long
from equitydesk.infrastructure.parsers.html import HtmlDocumentParser
from equitydesk.infrastructure.parsers.registry import ParserRegistry
from equitydesk.infrastructure.parsers.text import PagedTextParser, PdfParser, PlainTextParser

FIX = Path(__file__).resolve().parents[1] / "fixtures"
TENK = (FIX / "sec" / "aapl-20230930.htm").read_bytes()
RELEASE = (FIX / "ir" / "apple-q4-fy2023-release.html").read_bytes()
DOC_ID = "sec_0000320193-23-000106"


@pytest.fixture(scope="module")
def tenk():
    return HtmlDocumentParser().parse(DOC_ID, TENK)


def test_sec_items_become_sections(tenk) -> None:
    items = [s.item_code for s in tenk.sections if s.item_code]
    assert items == ["1", "1A", "7", "8"]
    risk = tenk.section_for_item("1a")
    assert risk is not None and risk.heading == "Item 1A. Risk Factors"
    texts = [c.text for c in tenk.chunks_in(risk.section_id)]
    assert any("outsourcing partners" in t for t in texts)


def test_scripts_styles_comments_skipped_and_entities_decoded(tenk) -> None:
    assert "var x" not in tenk.plain_text
    assert "margin: 0" not in tenk.plain_text
    assert "Synthetic test fixture" not in tenk.plain_text
    assert "Company’s fiscal year" in tenk.plain_text
    assert tenk.title == "aapl-20230930"


def test_tables_are_extracted_with_rows_and_citable_chunk(tenk) -> None:
    assert len(tenk.tables) == 1
    table = tenk.tables[0]
    assert table.rows[3] == ("Total net sales", "$ 383,285", "$ 394,328", "(3)%")
    chunk = tenk.chunk(table.chunk_id)
    assert chunk is not None and chunk.is_table and "Total net sales" in chunk.text
    assert table.section_id == tenk.section_for_item("7").section_id  # type: ignore[union-attr]


def test_locators_are_stable_and_offsets_valid(tenk) -> None:
    again = HtmlDocumentParser().parse(DOC_ID, TENK)
    assert again == tenk
    digest = hashlib.sha256(tenk.model_dump_json().encode()).hexdigest()
    assert digest == hashlib.sha256(again.model_dump_json().encode()).hexdigest()
    for c in tenk.chunks:
        assert c.chunk_id.startswith(f"{DOC_ID}:{c.section_id}:p")
        assert tenk.plain_text[c.char_start : c.char_end] == c.text
    assert tenk.content_hash == hashlib.sha256(TENK).hexdigest()
    assert (tenk.parser_name, tenk.parser_version) == ("html", "1.0.0")


def test_h_tags_are_headings() -> None:
    doc = HtmlDocumentParser().parse("ir_1", RELEASE)
    assert [s.heading for s in doc.sections] == [
        "Apple reports fourth quarter results",
        "Outlook",
        "Forward-Looking Statements",
    ]
    outlook = next(s for s in doc.sections if s.heading == "Outlook")
    assert "December quarter revenue" in doc.chunks_in(outlook.section_id)[0].text


@pytest.mark.parametrize(
    ("content", "code"),
    [
        (b"", ParseErrorCode.EMPTY_CONTENT),
        (b"   \n ", ParseErrorCode.EMPTY_CONTENT),
        (b"\x00\x01\x02binary", ParseErrorCode.BINARY_CONTENT),
        (b"just text, no markup", ParseErrorCode.MALFORMED),
        (b"<html><script>only()</script></html>", ParseErrorCode.NO_TEXT),
        (b"<html><h1>Item 1. Business</h1></html>", ParseErrorCode.NO_TEXT),
    ],
)
def test_malformed_html_fails_structurally(content: bytes, code: ParseErrorCode) -> None:
    with pytest.raises(ParseError) as exc:
        HtmlDocumentParser().parse("bad", content)
    assert exc.value.code is code
    assert exc.value.document_id == "bad"


def test_cp1252_fallback() -> None:
    doc = HtmlDocumentParser().parse("x", "<p>café ’</p>".encode("cp1252"))
    assert doc.chunks[0].text == "café ’"


def test_plain_text_paragraphs_and_headings() -> None:
    content = b"# Overview\nLine one\ncontinues.\n\nSecond para.\n\nItem 7. MD&A\nRevenue grew."
    doc = PlainTextParser().parse("t", content)
    assert [s.heading for s in doc.sections] == ["Overview", "Item 7. MD&A"]
    assert [c.text for c in doc.chunks] == ["Line one continues.", "Second para.", "Revenue grew."]


def test_paged_text_keeps_page_locators() -> None:
    content = b"Item 1. Business\n\nPage one text.\fPage two text.\n\nMore on two."
    doc = PagedTextParser().parse("p", content)
    assert [(c.text, c.page) for c in doc.chunks] == [
        ("Page one text.", 1),
        ("Page two text.", 2),
        ("More on two.", 2),
    ]


def test_pdf_requires_extractor_and_valid_header() -> None:
    with pytest.raises(ParseError) as exc:
        PdfParser().parse("pdf", b"%PDF-1.7 ...")
    assert exc.value.code is ParseErrorCode.UNSUPPORTED_FORMAT
    with pytest.raises(ParseError) as exc:
        PdfParser(lambda b: ["x"]).parse("pdf", b"not a pdf")
    assert exc.value.code is ParseErrorCode.MALFORMED

    def broken(_: bytes) -> list[str]:
        raise RuntimeError("corrupt xref")

    with pytest.raises(ParseError) as exc:
        PdfParser(broken).parse("pdf", b"%PDF-1.7")
    assert exc.value.code is ParseErrorCode.MALFORMED

    doc = PdfParser(lambda b: ["Slide one.", "Slide two."]).parse("pdf", b"%PDF-1.7")
    assert doc.parser_name == "pdf" and [c.page for c in doc.chunks] == [1, 2]


def test_registry_dispatch() -> None:
    reg = ParserRegistry()
    assert reg.parse("a", TENK, "text/html; charset=utf-8").parser_name == "html"
    assert reg.parse("b", TENK, None).parser_name == "html"
    assert reg.parse("c", b"a\fb", None).parser_name == "paged_text"
    assert reg.parse("d", b"hello", None).parser_name == "text"
    with pytest.raises(ParseError) as exc:
        reg.parse("e", b"x", "application/vnd.ms-excel")
    assert exc.value.code is ParseErrorCode.UNSUPPORTED_FORMAT


def test_long_paragraphs_split_at_sentences() -> None:
    text = " ".join(f"Sentence number {i}." for i in range(400))
    parts = split_long(text, limit=200)
    assert all(len(p) <= 200 for p in parts)
    assert " ".join(parts) == text
    assert split_long("x" * 450, limit=200) == ["x" * 200, "x" * 200, "x" * 50]
