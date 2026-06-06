"""Tests for GenericPDFHandler — real PyMuPDF text extraction (no more stub)."""

from pathlib import Path
from types import SimpleNamespace

import pytest

fitz = pytest.importorskip("fitz", reason="PyMuPDF not installed")

from paper_bridge.summarizer.src.input_handlers.generic_handler import (  # noqa: E402
    GenericPDFHandler,
)

H = GenericPDFHandler


def _make_pdf(path: Path, pages: list[str]) -> Path:
    """Write a simple text PDF with one block of text per page."""
    doc = fitz.open()
    for text in pages:
        page = doc.new_page()
        page.insert_text((72, 72), text, fontsize=12)
    doc.save(str(path))
    doc.close()
    return path


@pytest.mark.unit
class TestExtractPdfText:
    def test_extracts_text_from_single_page(self, tmp_path: Path) -> None:
        pdf = _make_pdf(tmp_path / "a.pdf", ["Hello world from a PDF."])
        text = H._extract_pdf_text(pdf)
        assert "Hello world from a PDF." in text

    def test_concatenates_multiple_pages(self, tmp_path: Path) -> None:
        pdf = _make_pdf(tmp_path / "b.pdf", ["Page one content.", "Page two content."])
        text = H._extract_pdf_text(pdf)
        assert "Page one content." in text
        assert "Page two content." in text

    def test_respects_char_budget(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setattr(H, "MAX_CONTENT_CHARS", 50)
        pdf = _make_pdf(tmp_path / "c.pdf", ["x" * 200, "y" * 200])
        text = H._extract_pdf_text(pdf)
        assert len(text) <= 50

    def test_respects_page_budget(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setattr(H, "MAX_PDF_PAGES", 1)
        pdf = _make_pdf(tmp_path / "d.pdf", ["FIRST page.", "SECOND page."])
        text = H._extract_pdf_text(pdf)
        assert "FIRST page." in text
        assert "SECOND page." not in text

    def test_unreadable_pdf_returns_empty(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad.pdf"
        bad.write_text("not a real pdf")
        assert H._extract_pdf_text(bad) == ""


@pytest.mark.unit
class TestParseContent:
    async def test_parse_content_populates_text(self, tmp_path: Path) -> None:
        pdf = _make_pdf(tmp_path / "p.pdf", ["Parsed body text."])
        paper = SimpleNamespace(arxiv_id="abc123", _pdf_path=pdf)
        handler = H(config=SimpleNamespace(pdf_download_timeout=30))
        parsed = await handler.parse_content(paper, tmp_path)
        assert "Parsed body text." in parsed.text
        assert parsed.figures == []
        assert parsed.pdf_path == pdf

    async def test_missing_pdf_raises(self, tmp_path: Path) -> None:
        paper = SimpleNamespace(arxiv_id="abc123", _pdf_path=tmp_path / "nope.pdf")
        handler = H(config=SimpleNamespace(pdf_download_timeout=30))
        with pytest.raises(ValueError, match="PDF not found"):
            await handler.parse_content(paper, tmp_path)

    async def test_no_pdf_path_attribute_raises(self, tmp_path: Path) -> None:
        paper = SimpleNamespace(arxiv_id="abc123")
        handler = H(config=SimpleNamespace(pdf_download_timeout=30))
        with pytest.raises(ValueError, match="PDF not found"):
            await handler.parse_content(paper, tmp_path)


@pytest.mark.unit
class TestUrlHelpers:
    def test_validate_url_accepts_http(self) -> None:
        assert (
            H._validate_url("https://example.com/x.pdf") == "https://example.com/x.pdf"
        )

    def test_validate_url_rejects_empty(self) -> None:
        assert H._validate_url("") is None
        assert H._validate_url("   ") is None

    def test_validate_url_rejects_garbage(self) -> None:
        assert H._validate_url("not a url") is None

    def test_filename_from_url(self) -> None:
        assert H._get_filename_from_url("https://x.com/paper.pdf") == "paper.pdf"

    def test_filename_defaults_when_no_pdf(self) -> None:
        assert H._get_filename_from_url("https://x.com/page") == "document.pdf"

    def test_title_from_url_normalizes(self) -> None:
        assert (
            H._extract_title_from_url("https://x.com/my_great-paper.pdf")
            == "my great paper"
        )


def _make_pdf_with_title(
    path: Path,
    title: str,
    body: str,
    *,
    meta_title: str | None = None,
    meta_author: str | None = None,
) -> Path:
    """Write a PDF whose first-page title is in a larger font than the body."""
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), title, fontsize=24)
    page.insert_text((72, 140), body, fontsize=11)
    meta = {}
    if meta_title is not None:
        meta["title"] = meta_title
    if meta_author is not None:
        meta["author"] = meta_author
    if meta:
        doc.set_metadata(meta)
    doc.save(str(path))
    doc.close()
    return path


@pytest.mark.unit
class TestIsSaneTitle:
    def test_accepts_plausible_title(self) -> None:
        assert H._is_sane_title("Attention Is All You Need")

    def test_rejects_empty(self) -> None:
        assert not H._is_sane_title("")

    def test_rejects_overlong_paragraph(self) -> None:
        assert not H._is_sane_title("word " * 100)

    def test_accepts_short_real_title(self) -> None:
        # Minimal sanity only: short single words from PDF metadata are accepted
        # (the extractor never sees URL filenames, so no hash/placeholder guard).
        assert H._is_sane_title("BERT")


@pytest.mark.unit
class TestCleanTitle:
    def test_collapses_whitespace_and_strips_quotes(self) -> None:
        assert H._clean_title('  "A   Great   Title"  ') == "A Great Title"

    def test_empty_input(self) -> None:
        assert H._clean_title("") == ""


@pytest.mark.unit
class TestExtractTitle:
    def test_prefers_largest_font_on_page_one(self, tmp_path: Path) -> None:
        pdf = _make_pdf_with_title(
            tmp_path / "t.pdf",
            "Attention Is All You Need",
            "The dominant sequence transduction models are based on ...",
        )
        assert H._extract_title(pdf) == "Attention Is All You Need"

    def test_uses_metadata_title_when_plausible(self, tmp_path: Path) -> None:
        pdf = _make_pdf_with_title(
            tmp_path / "m.pdf",
            "Tiny",
            "body text here",
            meta_title="A Proper Metadata Title",
        )
        assert H._extract_title(pdf) == "A Proper Metadata Title"

    def test_returns_none_for_unreadable_pdf(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad.pdf"
        bad.write_text("not a real pdf")
        assert H._extract_title(bad) is None


@pytest.mark.unit
class TestExtractAuthors:
    def test_splits_metadata_authors(self, tmp_path: Path) -> None:
        pdf = _make_pdf_with_title(
            tmp_path / "a.pdf",
            "T",
            "body",
            meta_author="Ashish Vaswani, Noam Shazeer and Niki Parmar",
        )
        assert H._extract_authors(pdf) == [
            "Ashish Vaswani",
            "Noam Shazeer",
            "Niki Parmar",
        ]

    def test_empty_when_no_author_metadata(self, tmp_path: Path) -> None:
        pdf = _make_pdf_with_title(tmp_path / "b.pdf", "T", "body")
        assert H._extract_authors(pdf) == []

    def test_empty_for_unreadable_pdf(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad.pdf"
        bad.write_text("not a real pdf")
        assert H._extract_authors(bad) == []
