"""Generic PDF URL handler for non-arXiv sources."""

import hashlib
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import unquote, urlparse

import httpx
from pydantic import HttpUrl, TypeAdapter

from ..logger import logger
from .base import BaseInputHandler, ParsedContent

if TYPE_CHECKING:
    import fitz

    from ...configs.config import InputConfig
    from ..fetcher import Paper


class GenericPDFHandler(BaseInputHandler):
    """Handler for generic PDF URLs (non-arXiv)."""

    # Cap extraction so a pathological PDF can't blow up memory / the LLM context.
    MAX_PDF_PAGES: int = 100
    MAX_CONTENT_CHARS: int = 200_000

    DEFAULT_HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "application/pdf,*/*",
    }

    def __init__(self, config: "InputConfig", timeout: int | None = None):
        super().__init__(config, timeout)

    async def fetch_paper(self, url: str, output_dir: Path) -> "Paper":
        """Fetch paper from generic PDF URL.

        Args:
            url: PDF URL to fetch
            output_dir: Directory to store downloaded PDF

        Returns:
            Paper object with basic metadata
        """
        from ..fetcher import Paper

        validated_url = self._validate_url(url)
        if not validated_url:
            raise ValueError(f"Invalid URL: {url}")

        if self.config.use_md5_hash_dirs:
            temp_dir = self.get_temp_dir(url, Path(self.config.temp_dir_base))
        else:
            temp_dir = output_dir
        temp_dir.mkdir(parents=True, exist_ok=True)

        pdf_path = await self._download_pdf(validated_url, temp_dir)
        # Prefer the paper's real title (PDF metadata, else the largest text on
        # page 1) over the URL filename — generic URLs are often opaque hashes
        # (e.g. "<md5>-Paper.pdf"), which render as a meaningless card title.
        title = self._extract_title(pdf_path) or self._extract_title_from_url(url)
        paper_id = hashlib.md5(url.encode()).hexdigest()[:12]

        paper = Paper(
            arxiv_id=paper_id,
            authors=["Unknown"],
            published_at=datetime.now(UTC),
            title=title,
            summary="",
            upvotes=0,
            pdf_url=HttpUrl(url),
            base_date=datetime.now(UTC).strftime("%Y-%m-%d"),
        )
        paper._pdf_path = pdf_path

        logger.info("Fetched paper from URL: %s (ID: %s)", url, paper_id)
        return paper

    async def parse_content(self, paper: "Paper", output_dir: Path) -> ParsedContent:
        """Parse PDF text content with PyMuPDF.

        Extracts plain text from the downloaded PDF (up to ``MAX_PDF_PAGES`` pages
        and ``MAX_CONTENT_CHARS`` characters). Figure extraction is not attempted
        for generic sources — unlike arXiv, there is no layout metadata to locate
        figures reliably, so ``figures`` is always empty.

        Args:
            paper: Paper object with ``_pdf_path`` set by ``fetch_paper``.
            output_dir: Directory for output files (unused for text-only parse).

        Returns:
            ParsedContent with extracted text (figures empty).
        """
        pdf_path = getattr(paper, "_pdf_path", None)
        if not pdf_path or not pdf_path.exists():
            raise ValueError(f"PDF not found for paper: {paper.arxiv_id}")

        text = self._extract_pdf_text(pdf_path)
        logger.info(
            "Extracted %d characters from generic PDF: %s", len(text), paper.arxiv_id
        )
        return ParsedContent(text=text, figures=[], pdf_path=pdf_path)

    # Upper bound on a title length; longer means we grabbed a paragraph, not a
    # title. No lower bound / content heuristics: this only ever sees text the
    # PDF itself designates as title (metadata or largest-font line), never a
    # URL filename, so guarding against hashes/placeholders here would be
    # defending a path that can't occur.
    _TITLE_MAX_CHARS: int = 300

    @classmethod
    def _extract_title(cls, pdf_path: Path) -> str | None:
        """Best-effort real title from a PDF.

        Trusts the embedded ``/Title`` metadata (the author set it); if absent,
        falls back to the line with the largest font on the first page (papers
        set the title in the biggest type). Returns ``None`` when neither yields
        usable text, so the caller can fall back to the URL-derived title.
        """
        import fitz

        try:
            with fitz.open(pdf_path) as doc:
                meta_title = cls._clean_title((doc.metadata or {}).get("title", ""))
                if cls._is_sane_title(meta_title):
                    return meta_title

                if doc.page_count == 0:
                    return None
                return cls._title_from_first_page(doc[0])
        except Exception as e:
            logger.warning("Could not extract title from %s: %s", pdf_path, e)
            return None

    @classmethod
    def _title_from_first_page(cls, page: "fitz.Page") -> str | None:
        """Pick the title as the text spans with the largest font on page 1.

        Adjacent spans that share the (rounded) maximum font size are joined so
        a title wrapped across two lines is recovered as one string.
        """
        data = page.get_text("dict")
        spans: list[tuple[float, str]] = []
        for block in data.get("blocks", []):
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    text = (span.get("text") or "").strip()
                    size = round(float(span.get("size", 0)), 1)
                    if text:
                        spans.append((size, text))

        if not spans:
            return None

        max_size = max(size for size, _ in spans)
        title = cls._clean_title(" ".join(t for s, t in spans if s == max_size))
        return title if cls._is_sane_title(title) else None

    @staticmethod
    def _clean_title(title: str) -> str:
        """Collapse whitespace and strip surrounding quotes from a title."""
        return " ".join((title or "").split()).strip("\"'").strip()

    @classmethod
    def _is_sane_title(cls, title: str) -> bool:
        """Minimal sanity: non-empty and not an overlong paragraph."""
        return bool(title) and len(title) <= cls._TITLE_MAX_CHARS

    @classmethod
    def _extract_pdf_text(cls, pdf_path: Path) -> str:
        """Extract and normalize text from a PDF using PyMuPDF.

        Pages are concatenated until the character budget is reached. Returns an
        empty string if the PDF cannot be opened (caller decides how to handle).
        """
        import fitz

        pieces: list[str] = []
        total = 0
        try:
            with fitz.open(pdf_path) as doc:
                for page in doc[: cls.MAX_PDF_PAGES]:
                    page_text = page.get_text("text").strip()
                    if not page_text:
                        continue
                    pieces.append(page_text)
                    total += len(page_text)
                    if total >= cls.MAX_CONTENT_CHARS:
                        break
        except Exception as e:
            logger.error("Failed to extract text from %s: %s", pdf_path, e)
            return ""

        return "\n\n".join(pieces)[: cls.MAX_CONTENT_CHARS]

    async def _download_pdf(self, url: str, temp_dir: Path) -> Path:
        """Download PDF from URL using httpx async client.

        Args:
            url: URL to download from
            temp_dir: Directory to store PDF

        Returns:
            Path to downloaded PDF
        """
        pdf_filename = self._get_filename_from_url(url)
        pdf_path = temp_dir / pdf_filename

        if pdf_path.exists():
            logger.debug("PDF already exists: %s", pdf_path)
            return pdf_path

        logger.info("Downloading PDF from: %s", url)
        async with httpx.AsyncClient(
            timeout=self.timeout,
            headers=self.DEFAULT_HEADERS,
            follow_redirects=True,
        ) as client:
            response = await client.get(url)
            response.raise_for_status()
            pdf_path.write_bytes(response.content)

        logger.info("Downloaded PDF to: %s", pdf_path)
        return pdf_path

    @staticmethod
    def _validate_url(url: str) -> str | None:
        """Validate URL using pydantic HttpUrl.

        Args:
            url: URL to validate

        Returns:
            Validated URL or None if invalid
        """
        stripped = url.strip() if url else ""
        if not stripped:
            return None
        try:
            adapter = TypeAdapter(HttpUrl)
            adapter.validate_python(stripped)
            return stripped
        except Exception:
            return None

    @staticmethod
    def _get_filename_from_url(url: str) -> str:
        """Extract filename from URL.

        Args:
            url: URL to extract filename from

        Returns:
            Filename or 'document.pdf' if not found
        """
        parsed = urlparse(url)
        path = unquote(parsed.path)
        filename = path.split("/")[-1]
        if not filename.lower().endswith(".pdf"):
            filename = "document.pdf"
        return filename

    @staticmethod
    def _extract_title_from_url(url: str) -> str:
        """Extract title from URL filename.

        Args:
            url: URL to extract title from

        Returns:
            Extracted title
        """
        parsed = urlparse(url)
        path = unquote(parsed.path)
        filename = path.split("/")[-1]

        if filename.lower().endswith(".pdf"):
            title = filename[:-4]
        else:
            title = filename

        title = title.replace("_", " ").replace("-", " ")
        return title if title else "Unknown Paper"
