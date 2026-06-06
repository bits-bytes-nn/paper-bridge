"""Base class for input handlers."""

import hashlib
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ...configs.config import InputConfig
    from ..fetcher import Figure, Paper


@dataclass
class ParsedContent:
    """Container for parsed paper content."""

    text: str
    figures: list["Figure"] = field(default_factory=list)
    pdf_path: Path | None = None


class BaseInputHandler(ABC):
    """Abstract base class for input handlers."""

    def __init__(self, config: "InputConfig", timeout: int | None = None):
        self.config = config
        self.timeout = timeout or config.pdf_download_timeout

    @abstractmethod
    async def fetch_paper(self, identifier: str, output_dir: Path) -> "Paper":
        """Fetch paper metadata and content.

        Args:
            identifier: URL or ID to fetch
            output_dir: Directory to store downloaded files

        Returns:
            Paper object with metadata and content
        """
        pass

    @abstractmethod
    async def parse_content(self, paper: "Paper", output_dir: Path) -> ParsedContent:
        """Parse paper content (PDF or HTML).

        Args:
            paper: Paper object with metadata
            output_dir: Directory containing downloaded files

        Returns:
            ParsedContent with text and figures
        """
        pass

    @staticmethod
    def get_temp_dir(url: str, base_dir: Path) -> Path:
        """Generate MD5 hash-based temp directory.

        Args:
            url: URL to hash
            base_dir: Base directory for temp files

        Returns:
            Path to temp directory
        """
        url_hash = hashlib.md5(url.encode()).hexdigest()
        return base_dir / url_hash

    @staticmethod
    def is_arxiv_url(url: str) -> bool:
        """Check if URL is an arXiv URL.

        Args:
            url: URL to check

        Returns:
            True if URL is from arxiv.org
        """
        return "arxiv.org" in url.lower()

    @staticmethod
    def extract_arxiv_id(url: str) -> str | None:
        """Extract arXiv ID from URL.

        Args:
            url: arXiv URL

        Returns:
            arXiv ID or None if not found
        """
        import re

        patterns = [
            r"arxiv\.org/abs/(\d+\.\d+)",
            r"arxiv\.org/pdf/(\d+\.\d+)",
            r"arxiv\.org/html/(\d+\.\d+)",
        ]
        for pattern in patterns:
            match = re.search(pattern, url, re.IGNORECASE)
            if match:
                return match.group(1)
        return None
