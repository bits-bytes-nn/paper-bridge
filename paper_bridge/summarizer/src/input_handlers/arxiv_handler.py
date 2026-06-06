"""ArXiv-specific input handler with optimizations."""

from pathlib import Path
from typing import TYPE_CHECKING, Optional

from ..logger import logger
from .base import BaseInputHandler, ParsedContent

if TYPE_CHECKING:
    import boto3

    from ...configs.config import Config
    from ..fetcher import Paper, PaperFetcher


class ArxivInputHandler(BaseInputHandler):
    """Handler for arXiv URLs with arXiv-specific optimizations."""

    def __init__(
        self,
        config: "Config",
        boto3_session: Optional["boto3.Session"] = None,
        profile_name: str | None = None,
    ):
        super().__init__(config.input)
        self.full_config = config
        self.boto3_session = boto3_session
        self.profile_name = profile_name
        self._fetcher: PaperFetcher | None = None

    @property
    def fetcher(self) -> "PaperFetcher":
        """Lazy-initialize PaperFetcher."""
        if self._fetcher is None:
            from ..fetcher import PaperFetcher

            self._fetcher = PaperFetcher(
                config=self.full_config,
                boto3_session=self.boto3_session,
                profile_name=self.profile_name,
            )
        return self._fetcher

    async def fetch_paper(self, identifier: str, output_dir: Path) -> "Paper":
        """Fetch paper from arXiv.

        Args:
            identifier: arXiv ID or URL
            output_dir: Directory to store downloaded files

        Returns:
            Paper object with metadata and content
        """
        arxiv_id = self._normalize_identifier(identifier)
        if not arxiv_id:
            raise ValueError(f"Invalid arXiv identifier: {identifier}")

        papers = self.fetcher.fetch_papers_by_arxiv_ids(
            papers_dir=output_dir,
            arxiv_ids=[arxiv_id],
            parse_pdf=self.full_config.summarization.parse_pdf,
        )

        if not papers:
            raise ValueError(f"Failed to fetch paper: {arxiv_id}")

        logger.info("Fetched arXiv paper: %s", arxiv_id)
        return papers[0]

    async def parse_content(self, paper: "Paper", output_dir: Path) -> ParsedContent:
        """Parse arXiv paper content.

        Args:
            paper: Paper object with metadata
            output_dir: Directory containing downloaded files

        Returns:
            ParsedContent with text and figures
        """
        return ParsedContent(
            text=paper.content or "",
            figures=paper.figures or [],
            pdf_path=None,
        )

    def fetch_papers_for_date_range(
        self,
        output_dir: Path,
        target_date: str | None = None,
        days_to_fetch: int | None = None,
        papers_per_day: int | None = None,
        min_upvotes: int | None = None,
        parse_pdf: bool = False,
    ) -> list["Paper"]:
        """Fetch papers from HuggingFace Daily Papers for date range.

        This is a convenience method that wraps the existing PaperFetcher logic.

        Args:
            output_dir: Directory to store downloaded files
            target_date: Target date for fetching (YYYY-MM-DD)
            days_to_fetch: Number of days to fetch
            papers_per_day: Maximum papers per day
            min_upvotes: Minimum upvotes filter
            parse_pdf: Whether to parse PDFs

        Returns:
            List of Paper objects
        """
        return self.fetcher.fetch_papers_for_date_range(
            papers_dir=output_dir,
            target_date=target_date,
            days_to_fetch=days_to_fetch or self.full_config.summarization.days_to_fetch,
            papers_per_day=papers_per_day
            or self.full_config.summarization.papers_per_day,
            min_upvotes=min_upvotes or self.full_config.summarization.min_upvotes,
            parse_pdf=parse_pdf or self.full_config.summarization.parse_pdf,
        )

    def fetch_papers_by_arxiv_ids(
        self,
        output_dir: Path,
        arxiv_ids: list[str],
        parse_pdf: bool = False,
    ) -> list["Paper"]:
        """Fetch papers by arXiv IDs.

        Args:
            output_dir: Directory to store downloaded files
            arxiv_ids: List of arXiv IDs
            parse_pdf: Whether to parse PDFs

        Returns:
            List of Paper objects
        """
        return self.fetcher.fetch_papers_by_arxiv_ids(
            papers_dir=output_dir,
            arxiv_ids=arxiv_ids,
            parse_pdf=parse_pdf or self.full_config.summarization.parse_pdf,
        )

    def _normalize_identifier(self, identifier: str) -> str | None:
        """Normalize arXiv identifier (URL or ID).

        Args:
            identifier: arXiv URL or ID

        Returns:
            Normalized arXiv ID or None
        """
        if self.is_arxiv_url(identifier):
            return self.extract_arxiv_id(identifier)

        # Check if it's already an arXiv ID format
        import re

        if re.match(r"^\d+\.\d+v?\d*$", identifier.strip()):
            return identifier.strip()

        return None
