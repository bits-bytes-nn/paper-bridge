import os
import re
import tempfile
import time
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime, timedelta
from enum import Enum, auto
from pathlib import Path
from typing import Any, cast

import arxiv
import boto3
import PyPDF2
import requests
from llama_index.llms.bedrock_converse import BedrockConverse
from llama_parse import LlamaParse
from llama_parse.base import ResultType
from pydantic import BaseModel, Field, HttpUrl, field_validator
from unstructured.partition.pdf import partition_pdf

from paper_bridge.indexer.configs.config import Config
from paper_bridge.shared import PaperScorer, SelectionConfig

from .aws_helpers import get_cross_inference_model_id, get_ssm_param_value
from .constants import EnvVars, SSMParams, URLs
from .logger import is_aws_env, logger
from .prompts import MainContentExtractionPrompt
from .utils import HTMLTagOutputParser, measure_execution_time


class PaperStatus(Enum):
    FAILED = auto()
    PENDING = auto()
    PROCESSED = auto()


class ArxivPaperError(Exception):
    pass


class ArxivDownloadError(ArxivPaperError):
    pass


class ArxivNotFoundError(ArxivPaperError):
    pass


class Paper(BaseModel):
    arxiv_id: str
    authors: list[str] = Field(default_factory=list)
    published_at: datetime
    title: str
    summary: str
    upvotes: int = Field(default=0, ge=0)
    thumbnail: str | None = Field(default=None)
    content: str | None = Field(default=None)
    base_date: str
    pdf_url: HttpUrl | None = Field(default=None)
    status: PaperStatus = Field(default=PaperStatus.PENDING)

    @field_validator("authors")
    def validate_authors(cls, authors: list[str]) -> list[str]:
        if not authors:
            raise ValueError("Authors list cannot be empty")
        return authors

    @field_validator("base_date")
    def validate_base_date(cls, base_date: str) -> str:
        pattern = r"^\d{4}-\d{2}-\d{2}$"
        if not re.match(pattern, base_date):
            raise ValueError("Base date must be in the format 'YYYY-MM-DD'")
        return base_date

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()


class PaperFetcher:
    DEFAULT_TIMEOUT: int = 10
    MAX_RETRIES: int = 3
    RETRY_DELAY: int = 1
    MAX_WORKERS: int = 4
    MIN_PAPERS_PER_DAY: int = 1
    MIN_DAYS_TO_FETCH: int = 1
    MIN_UPVOTES: int = 0
    LLAMA_PARSE_RETRY_DELAY: int = 1
    LLAMA_PARSE_MAX_RETRIES: int = 5
    LLAMA_PARSE_MAX_TIMEOUT: int = 300
    LLAMA_PARSE_NUM_WORKERS: int = 8
    ARXIV_DOWNLOAD_MAX_RETRIES: int = 3
    ARXIV_DOWNLOAD_RETRY_DELAY: int = 2
    MAX_PDF_PAGES: int = 100
    MAX_CONTENT_CHARS: int = 200000
    CONTENT_OFFSET: int = 10000

    def __init__(
        self,
        config: Config,
        boto3_session: boto3.Session | None = None,
        profile_name: str | None = None,
        timeout: int = DEFAULT_TIMEOUT,
    ) -> None:
        self.boto3_session = boto3_session or boto3.Session(
            region_name=config.resources.bedrock_region_name, profile_name=profile_name
        )
        self._configure(config, timeout)
        self._init_llm_components(config, profile_name)

    def _configure(self, config: Config, timeout: int) -> None:
        self.papers_per_day = max(
            self.MIN_PAPERS_PER_DAY, config.indexing.papers_per_day
        )
        self.days_to_fetch = max(self.MIN_DAYS_TO_FETCH, config.indexing.days_to_fetch)
        self.min_upvotes = (
            max(self.MIN_UPVOTES, config.indexing.min_upvotes)
            if config.indexing.min_upvotes is not None
            else None
        )
        self.timeout = max(1, timeout)
        self._scorer = PaperScorer(
            SelectionConfig(
                popularity_weight=config.indexing.selection_popularity_weight,
                recency_weight=config.indexing.selection_recency_weight,
                recency_half_life_days=config.indexing.selection_recency_half_life_days,
                min_upvotes=self.min_upvotes,
            )
        )

        llama_cloud_api_key = self._get_llama_cloud_api_key(config, self.boto3_session)
        if llama_cloud_api_key is None:
            raise ValueError("LLAMA Cloud API key not found")

        self.llama_parser = LlamaParse(
            api_key=llama_cloud_api_key,
            max_timeout=self.LLAMA_PARSE_MAX_TIMEOUT,
            num_workers=self.LLAMA_PARSE_NUM_WORKERS,
            result_type=ResultType.MD,
            language="en",
        )

    def _init_llm_components(self, config: Config, profile_name: str | None) -> None:
        self.prompt = None
        self.llm = None
        self.output_parser = None

        if config.indexing.main_content_extraction_model_id:
            self.prompt = MainContentExtractionPrompt.get_prompt()
            model_id = get_cross_inference_model_id(
                self.boto3_session,
                config.indexing.main_content_extraction_model_id.value,
                config.resources.bedrock_region_name,
            )
            self.llm = BedrockConverse(
                model_id,
                temperature=0.0,
                max_tokens=4096,
                profile_name=profile_name,
                region_name=config.resources.bedrock_region_name,
            )
            self.output_parser = HTMLTagOutputParser(
                tag_names=MainContentExtractionPrompt.OUTPUT_VARIABLES
            )

    @staticmethod
    def _get_llama_cloud_api_key(
        config: Config, boto3_session: boto3.Session
    ) -> str | None:
        base_path = f"/{config.resources.project_name}-{config.resources.stage}"
        if is_aws_env():
            return get_ssm_param_value(
                boto3_session,
                f"{base_path}/{SSMParams.LLAMA_CLOUD_API_KEY.value}",
            )
        return os.getenv(EnvVars.LLAMA_CLOUD_API_KEY.name)

    @measure_execution_time
    def fetch_papers_by_arxiv_ids(
        self, arxiv_ids: list[str], use_llama_parse: bool = False
    ) -> list[Paper]:
        try:
            papers = self._fetch_papers_by_arxiv_ids(arxiv_ids)
            return self._process_papers_concurrently(papers, use_llama_parse)
        except Exception as e:
            logger.error("Error fetching papers by arXiv IDs: %s", str(e))
            return []

    @staticmethod
    def _fetch_papers_by_arxiv_ids(arxiv_ids: list[str]) -> list[Paper]:
        papers = []
        client = arxiv.Client()
        logger.info("Fetching papers by arXiv IDs: '%s'", arxiv_ids)

        for arxiv_id in arxiv_ids:
            try:
                search = arxiv.Search(id_list=[arxiv_id])
                paper_result = next(client.results(search), None)

                if not paper_result:
                    logger.warning("Paper not found for arXiv ID: %s", arxiv_id)
                    continue

                current_date = datetime.now(UTC)
                paper = Paper(
                    arxiv_id=arxiv_id,
                    authors=[author.name for author in paper_result.authors],
                    published_at=paper_result.published,
                    title=paper_result.title,
                    summary=paper_result.summary,
                    upvotes=0,
                    pdf_url=(
                        None
                        if paper_result.pdf_url is None
                        else HttpUrl(str(paper_result.pdf_url))
                    ),
                    base_date=current_date.strftime("%Y-%m-%d"),
                )
                papers.append(paper)

            except Exception as e:
                logger.error(
                    "Error fetching paper with arXiv ID '%s': %s", arxiv_id, str(e)
                )

        return papers

    def _process_papers_concurrently(
        self, papers: list[Paper], use_llama_parse: bool
    ) -> list[Paper]:
        with ThreadPoolExecutor(max_workers=self.MAX_WORKERS) as executor:
            futures = {
                executor.submit(
                    self.download_and_parse_paper, paper.arxiv_id, use_llama_parse
                ): paper
                for paper in papers
            }

            for future in as_completed(futures):
                paper = futures[future]
                try:
                    if content := future.result():
                        paper.content = content
                        paper.status = PaperStatus.PROCESSED
                    else:
                        paper.status = PaperStatus.FAILED
                except Exception as e:
                    logger.error(f"Error processing paper {paper.arxiv_id}: {str(e)}")
                    paper.status = PaperStatus.FAILED

        return papers

    @measure_execution_time
    def fetch_papers_for_date_range(
        self,
        target_date: datetime | None = None,
        days_to_fetch: int | None = None,
        use_llama_parse: bool = False,
    ) -> list[Paper]:
        try:
            target_date = self._get_target_date(target_date)
            days_to_fetch = days_to_fetch if days_to_fetch != 0 else None

            papers_by_date = self._fetch_papers_by_date_range(
                target_date, days_to_fetch
            )
            papers = self._select_papers(papers_by_date, target_date)
            return self._process_papers_concurrently(papers, use_llama_parse)

        except Exception as e:
            logger.error(
                "Error fetching papers for target date '%s' and days to fetch '%s': %s",
                target_date,
                days_to_fetch,
                str(e),
            )
            return []

    @staticmethod
    def _get_target_date(target_date: datetime | None) -> datetime:
        if target_date is not None:
            return target_date.astimezone(UTC)
        return datetime.now(UTC).replace(
            hour=0, minute=0, second=0, microsecond=0
        ).astimezone(UTC) - timedelta(days=1)

    def _fetch_papers_by_date_range(
        self, end_date: datetime, days_to_fetch: int | None = None
    ) -> dict[str, list[Paper]]:
        papers_by_date: dict[str, list[Paper]] = {}
        days = days_to_fetch or self.days_to_fetch
        start_date = end_date - timedelta(days=days - 1)
        logger.info("Fetching papers from '%s' to '%s'", start_date, end_date)

        for current_date in self._date_range(start_date, end_date):
            date_str = current_date.strftime("%Y-%m-%d")
            papers = self._fetch_daily_papers(date_str, current_date)
            if papers:
                papers_by_date[current_date.date().isoformat()] = papers

        return papers_by_date

    @staticmethod
    def _date_range(start_date: datetime, end_date: datetime) -> Sequence[datetime]:
        return [
            end_date - timedelta(days=days)
            for days in range((end_date - start_date).days + 1)
        ]

    def _fetch_daily_papers(self, date_str: str, current_date: datetime) -> list[Paper]:
        response = self._make_request(f"{URLs.HF_DAILY_PAPERS.url}?date={date_str}")
        if not response:
            return []

        papers = []
        for paper_data in response.json():
            if paper := self._process_paper_metadata(paper_data, current_date):
                papers.append(paper)
        return papers

    def _make_request(self, url: str) -> requests.Response | None:
        for attempt in range(self.MAX_RETRIES):
            try:
                response = requests.get(url, timeout=self.timeout)
                response.raise_for_status()
                return response
            except requests.RequestException as e:
                if attempt == self.MAX_RETRIES - 1:
                    logger.error(
                        f"Failed to fetch data after {self.MAX_RETRIES} attempts: {e}"
                    )
                    return None
                time.sleep(self.RETRY_DELAY * (2**attempt))
        return None

    def _process_paper_metadata(
        self, paper_data: dict[str, Any], current_date: datetime
    ) -> Paper | None:
        try:
            published_at = self._parse_date(paper_data.get("publishedAt"))
            paper_info = paper_data.get("paper", {})
            author_names = self._extract_author_names(paper_info.get("authors", []))

            paper = Paper(
                arxiv_id=paper_info["id"],
                authors=author_names,
                published_at=published_at or current_date,
                title=paper_info["title"],
                summary=paper_info["summary"],
                upvotes=paper_info["upvotes"],
                thumbnail=paper_info.get("thumbnail"),
                pdf_url=HttpUrl(f"{URLs.ARXIV_PDF.url}/{paper_info['id']}"),
                base_date=current_date.strftime("%Y-%m-%d"),
            )
            if self._meets_upvote_threshold(paper.upvotes):
                return paper
        except (KeyError, ValueError) as e:
            logger.error(f"Error creating Paper object: {e}")

        return None

    @staticmethod
    def _parse_date(date_str: str | None) -> datetime | None:
        if not date_str:
            return None
        try:
            return datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        except ValueError:
            logger.error(f"Invalid date format: {date_str}")
            return None

    @staticmethod
    def _extract_author_names(authors: list[dict[str, str] | str]) -> list[str]:
        author_names = []
        for author in authors:
            if isinstance(author, dict) and "name" in author:
                author_names.append(author["name"])
            elif isinstance(author, str):
                author_names.append(author)
        return author_names

    def _meets_upvote_threshold(self, upvotes: int) -> bool:
        return self.min_upvotes is None or upvotes >= self.min_upvotes

    def _select_papers(
        self, papers_by_date: dict[str, list[Paper]], reference_date: datetime
    ) -> list[Paper]:
        """Pick the top papers per day, then de-duplicate across days.

        Each day keeps its top ``papers_per_day`` by the configurable
        popularity+recency score (see :class:`PaperScorer`). The per-day picks
        are then de-duplicated by arxiv_id across the whole range, since the same
        paper resurfaces on consecutive HuggingFace daily pages — this avoids
        re-indexing the same paper multiple times.
        """
        selected: list[Paper] = []
        for papers in papers_by_date.values():
            selected.extend(
                self._scorer.select(papers, self.papers_per_day, reference_date)
            )
        # Cross-day dedup (keeps the highest-upvote instance per arxiv_id).
        return self._scorer.select(selected, len(selected), reference_date)

    def download_and_parse_paper(
        self, arxiv_id: str, use_llama_parse: bool
    ) -> str | None:
        try:
            paper = self._download_paper(arxiv_id)
            if not paper:
                return None

            with tempfile.TemporaryDirectory() as temp_dir:
                paper.download_pdf(temp_dir)
                pdf_path = self._get_pdf_path(temp_dir, arxiv_id)
                if not pdf_path:
                    return None

                if not self._check_pdf_page_limit(pdf_path):
                    logger.warning(
                        f"Paper '{arxiv_id}' exceeds maximum page limit of {self.MAX_PDF_PAGES} pages, skipping"
                    )
                    return None

                return self._process_pdf_content(pdf_path, use_llama_parse)

        except Exception as e:
            logger.error(f"Error downloading/parsing paper '{arxiv_id}': {str(e)}")
            return None

    def _download_paper(self, arxiv_id: str) -> Any | None:
        for attempt in range(self.ARXIV_DOWNLOAD_MAX_RETRIES):
            try:
                client = arxiv.Client()
                search = arxiv.Search(id_list=[arxiv_id])
                return next(client.results(search))
            except StopIteration:
                logger.error(f"Paper not found: '{arxiv_id}'")
                return None
            except (ConnectionResetError, Exception) as e:
                if attempt == self.ARXIV_DOWNLOAD_MAX_RETRIES - 1:
                    logger.error(
                        f"Error downloading paper '{arxiv_id}' after {self.ARXIV_DOWNLOAD_MAX_RETRIES} attempts: {str(e)}"
                    )
                    return None

                error_type = (
                    "Connection reset"
                    if isinstance(e, ConnectionResetError)
                    else "Error"
                )
                logger.warning(
                    f"{error_type} while downloading paper '{arxiv_id}', retrying ({attempt+1}/{self.ARXIV_DOWNLOAD_MAX_RETRIES}): {str(e)}"
                )
                time.sleep(self.ARXIV_DOWNLOAD_RETRY_DELAY * (2**attempt))
        return None

    @staticmethod
    def _get_pdf_path(temp_dir: str, arxiv_id: str) -> str | None:
        pdf_files = list(Path(temp_dir).glob("*.pdf"))
        if not pdf_files:
            logger.error(f"No PDF file found in temp directory for '{arxiv_id}'")
            return None
        return str(pdf_files[0])

    def _check_pdf_page_limit(self, pdf_path: str) -> bool:
        try:
            with open(pdf_path, "rb") as file:
                pdf_reader = PyPDF2.PdfReader(file)
                num_pages = len(pdf_reader.pages)
                return num_pages <= self.MAX_PDF_PAGES
        except Exception as e:
            logger.error(f"Error checking PDF page count: {str(e)}")
            return True

    def _process_pdf_content(self, pdf_path: str, use_llama_parse: bool) -> str | None:
        if use_llama_parse:
            content = self._try_llama_parse(pdf_path)
            if content:
                return content

        logger.warning("LlamaParse disabled or failed, falling back to unstructured")
        return self._process_pdf_with_unstructured(pdf_path)

    def _try_llama_parse(self, pdf_path: str) -> str | None:
        for retry_count in range(self.LLAMA_PARSE_MAX_RETRIES):
            try:
                documents = self.llama_parser.load_data(file_path=pdf_path)
                if documents:
                    text_content = "\n".join(doc.text for doc in documents)
                    return self._extract_main_content(text_content)

                if retry_count < self.LLAMA_PARSE_MAX_RETRIES - 1:
                    logger.warning(
                        f"LlamaParse returned no documents, retrying ({retry_count+1}/{self.LLAMA_PARSE_MAX_RETRIES})"
                    )
                    time.sleep(self.LLAMA_PARSE_RETRY_DELAY)
            except Exception as e:
                logger.warning(f"LlamaParse failed with error: {str(e)}")
                break
        return None

    def _process_pdf_with_unstructured(self, pdf_path: str) -> str | None:
        text_content = self._extract_text_from_pdf(pdf_path)
        if not text_content:
            return None
        return self._extract_main_content(text_content)

    @staticmethod
    def _extract_text_from_pdf(pdf_path: str) -> str | None:
        try:
            elements = partition_pdf(
                filename=pdf_path,
                languages=["eng"],
                ocr_config=r"--dpi 300 --oem 1 --psm 6",
                strategy="hi_res",
                include_metadata=False,
                include_page_breaks=True,
            )

            if not elements:
                logger.warning(f"No elements extracted from PDF: {pdf_path}")
                return None

            text_content = "\n".join(str(element) for element in elements)

            if not text_content.strip():
                logger.warning(f"Extracted text is empty for PDF: {pdf_path}")
                return None

            return text_content

        except Exception as e:
            logger.error(f"Error extracting text from PDF: {str(e)}")
            return None

    def _extract_main_content(self, text_content: str) -> str | None:
        if not text_content:
            return None

        if not self.prompt or not self.llm or not self.output_parser:
            return text_content.strip()

        try:
            messages = self.prompt.format_messages(
                text=text_content[: self.MAX_CONTENT_CHARS]
            )
            response = self.llm.chat(messages)
            response_content = cast(str, response.message.content)

            markers = self.output_parser.parse(response_content)
            if not isinstance(markers, dict):
                return text_content.strip()

            content_range = self._find_content_range(text_content, markers)
            if not content_range:
                return text_content.strip()

            start_idx, end_idx = content_range
            return text_content[start_idx:end_idx].strip()

        except Exception as e:
            logger.error(f"Error extracting main content: {str(e)}")
            return text_content.strip()

    def _find_content_range(
        self, text_content: str, markers: dict[str, str]
    ) -> tuple[int, int] | None:
        start_marker = markers.get("start_marker", "")
        end_marker = markers.get("end_marker", "")

        if not start_marker or not end_marker:
            return None

        start_idx = text_content.find(start_marker)
        end_idx = text_content.find(end_marker, start_idx + self.CONTENT_OFFSET)

        logger.debug(
            "Content extraction markers - start_idx: %s, start_marker: %s, end_idx: %s, end_marker: %s",
            start_idx,
            start_marker,
            end_idx,
            end_marker,
        )

        if start_idx == -1:
            start_idx = 0
        if end_idx == -1:
            end_idx = len(text_content)

        return start_idx, end_idx
