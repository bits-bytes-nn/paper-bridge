"""Base class for output handlers."""

from abc import ABC, abstractmethod
from pathlib import Path
from typing import TYPE_CHECKING

import boto3

if TYPE_CHECKING:
    from ...configs.config import Config, OutputConfig
    from ..fetcher import Paper
    from ..renderer import Result


class BaseOutputHandler(ABC):
    """Abstract base class for output handlers."""

    def __init__(
        self,
        config: "Config",
        boto3_session: boto3.Session | None = None,
    ):
        self.config = config
        self.output_config: OutputConfig = config.output
        self.boto3_session = boto3_session

    @abstractmethod
    async def process(
        self,
        papers: list["Paper"],
        results: list["Result"],
        output_dir: Path,
        retrievals: dict[str, dict[str, str]] | None = None,
    ) -> None:
        """Process papers and results through the output handler.

        Args:
            papers: List of Paper objects
            results: List of Result objects with summaries
            output_dir: Directory containing output files
            retrievals: Optional retrieval results
        """
        pass

    @abstractmethod
    async def send_single(
        self,
        paper: "Paper",
        result: "Result",
        output_path: Path,
        retrieval: dict[str, str] | None = None,
    ) -> bool:
        """Send a single paper result.

        Args:
            paper: Paper object
            result: Result object with summary
            output_path: Path to output file (HTML or Markdown)
            retrieval: Optional retrieval result

        Returns:
            True if successful, False otherwise
        """
        pass
