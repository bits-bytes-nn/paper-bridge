import os
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from enum import Enum, auto
from typing import Any, Dict, List, Optional, Sequence, Union, cast
import arxiv
import boto3
import requests
from llama_index.llms.bedrock import Bedrock
from llama_parse import LlamaParse
from llama_parse.base import ResultType
from pydantic import BaseModel, Field, field_validator
from unstructured.partition.pdf import partition_pdf
from .aws_helpers import get_cross_inference_model_id, get_ssm_param_value
from .constants import EnvVars, SSMParams, URLs
from .logger import logger
from .prompts import MainContentExtractionPrompt
from .utils import HTMLTagOutputParser, is_aws_env, measure_execution_time
from paper_bridge.indexer.configs.config import Config


class PaperStatus(Enum):
    FAILED = auto()
    PENDING = auto()
    PROCESSED = auto()


class Paper(BaseModel):
    arxiv_id: str
    authors: List[str]
    published_at: datetime
    title: str
    summary: str
    upvotes: int
    thumbnail: Optional[str] = None
    content: Optional[str] = None
    status: PaperStatus = Field(default=PaperStatus.PENDING)

    @field_validator("authors")
    @classmethod
    def validate_authors(cls, authors: List[str]) -> List[str]:
        if not authors:
            raise ValueError("Authors list cannot be empty")
        return authors

    def to_dict(self) -> Dict[str, Any]:
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

    def __init__(
        self,
        config: Config,
        profile_name: Optional[str] = None,
        timeout: int = DEFAULT_TIMEOUT,
    ) -> None:
        self.boto3_session = self._create_boto3_session(
            config.resources.bedrock_region_name, profile_name
        )
        self._configure(config, timeout)
        self._init_llm_components(config, profile_name)

    def _create_boto3_session(
        self, region_name: str, profile_name: Optional[str]
    ) -> boto3.Session:
        return boto3.Session(region_name=region_name, profile_name=profile_name)

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
        base_path = f"/{config.resources.project_name}-{config.resources.stage}"
        llama_cloud_api_key = (
            get_ssm_param_value(
                self.boto3_session, f"{base_path}/{SSMParams.LLAMA_CLOUD_API_KEY.value}"
            )
            if is_aws_env()
            else os.getenv(EnvVars.LLAMA_CLOUD_API_KEY.name)
        )
        if llama_cloud_api_key is None:
            raise ValueError("LLAMA Cloud API key not found")

        self.llama_parser = LlamaParse(
            api_key=llama_cloud_api_key,
            max_timeout=self.LLAMA_PARSE_MAX_TIMEOUT,
            num_workers=self.LLAMA_PARSE_NUM_WORKERS,
            result_type=ResultType.MD,
            language="en",
        )

    def _init_llm_components(self, config: Config, profile_name: Optional[str]) -> None:
        self.prompt_template = None
        self.llm = None
        self.output_parser = None

        if config.indexing.main_content_extraction_model_id:
            self.prompt_template = MainContentExtractionPrompt
            self.llm = Bedrock(
                get_cross_inference_model_id(
                    self.boto3_session,
                    config.indexing.main_content_extraction_model_id.value,
                    config.resources.bedrock_region_name,
                ),
                temperature=0.0,
                max_tokens=4096,
                profile_name=profile_name,
                region_name=config.resources.bedrock_region_name,
            )
            self.output_parser = HTMLTagOutputParser(
                tag_names=self.prompt_template.OUTPUT_VARIABLES
            )

    @measure_execution_time
    def fetch_papers_for_date_range(
        self,
        target_date: Optional[datetime] = None,
        days_to_fetch: Optional[int] = None,
    ) -> Dict[str, List[Paper]]:
        try:
            target_date = self._get_target_date(target_date)
            if days_to_fetch == 0:
                days_to_fetch = None
            papers_by_date = self._fetch_papers_by_date_range(
                target_date, days_to_fetch=days_to_fetch
            )
            filtered_papers = self._filter_and_sort_papers(papers_by_date)
            return self._process_papers_concurrently(filtered_papers)
        except requests.RequestException as e:
            logger.error("Error fetching papers: %s", e)
            return {}
        except Exception as e:
            logger.error("Unexpected error: %s", e)
            return {}

    @staticmethod
    def _get_target_date(target_date: Optional[datetime]) -> datetime:
        if target_date is not None:
            return target_date.astimezone(timezone.utc)
        return datetime.now(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0
        ).astimezone(timezone.utc) - timedelta(days=1)

    def _fetch_papers_by_date_range(
        self, end_date: datetime, days_to_fetch: Optional[int] = None
    ) -> Dict[str, List[Paper]]:
        papers_by_date: Dict[str, List[Paper]] = {}
        start_date = end_date - timedelta(
            days=(days_to_fetch or self.days_to_fetch) - 1
        )
        logger.info(f"Fetching papers from '{start_date}' to '{end_date}'")

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

    def _fetch_daily_papers(self, date_str: str, current_date: datetime) -> List[Paper]:
        response = self._make_request(f"{URLs.HF_DAILY_PAPERS.url}?date={date_str}")
        if not response:
            return []

        papers = []
        for paper_data in response.json():
            if paper := self._process_paper_metadata(paper_data, current_date):
                papers.append(paper)
        return papers

    def _make_request(self, url: str) -> Optional[requests.Response]:
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
        self, paper_data: Dict[str, Any], current_date: datetime
    ) -> Optional[Paper]:
        published_at = self._parse_date(paper_data.get("publishedAt"))
        if not published_at or published_at.date() != current_date.date():
            return None

        try:
            paper_info = paper_data.get("paper", {})
            author_names = self._extract_author_names(paper_info.get("authors", []))

            paper = Paper(
                arxiv_id=paper_info["id"],
                authors=author_names,
                published_at=published_at,
                title=paper_info["title"],
                summary=paper_info["summary"],
                upvotes=paper_info["upvotes"],
                thumbnail=paper_info.get("thumbnail"),
            )
            if self._meets_upvote_threshold(paper.upvotes):
                return paper
        except (KeyError, ValueError) as e:
            logger.error(f"Error creating Paper object: {e}")

        return None

    def _meets_upvote_threshold(self, upvotes: int) -> bool:
        return self.min_upvotes is None or upvotes >= self.min_upvotes

    @staticmethod
    def _extract_author_names(authors: List[Union[Dict[str, str], str]]) -> List[str]:
        author_names = []
        for author in authors:
            if isinstance(author, dict) and "name" in author:
                author_names.append(author["name"])
            elif isinstance(author, str):
                author_names.append(author)
        return author_names

    def _filter_and_sort_papers(
        self, papers_by_date: Dict[str, List[Paper]]
    ) -> Dict[str, List[Paper]]:
        return {
            date: sorted(papers, key=lambda x: (-x.upvotes, x.title))[
                : self.papers_per_day
            ]
            for date, papers in papers_by_date.items()
        }

    def _process_papers_concurrently(
        self, filtered_papers: Dict[str, List[Paper]]
    ) -> Dict[str, List[Paper]]:
        with ThreadPoolExecutor(max_workers=self.MAX_WORKERS) as executor:
            futures = {
                executor.submit(self.download_and_parse_paper, paper.arxiv_id): paper
                for papers in filtered_papers.values()
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

        return filtered_papers

    @staticmethod
    def _parse_date(date_str: Optional[str]) -> Optional[datetime]:
        if not date_str:
            return None
        try:
            return datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        except ValueError:
            logger.error(f"Invalid date format: {date_str}")
            return None

    def download_and_parse_paper(self, arxiv_id: str) -> Optional[str]:
        try:
            paper = self._download_paper(arxiv_id)
            if not paper:
                return None

            with tempfile.TemporaryDirectory() as temp_dir:
                paper.download_pdf(temp_dir)
                pdf_files = [f for f in os.listdir(temp_dir) if f.endswith(".pdf")]
                if not pdf_files:
                    logger.error(
                        f"No PDF file found in temp directory for '{arxiv_id}'"
                    )
                    return None

                pdf_path = os.path.join(temp_dir, pdf_files[0])
                return self._process_pdf_content(pdf_path)

        except Exception as e:
            logger.error(f"Error downloading/parsing paper '{arxiv_id}': {str(e)}")
            return None

    @staticmethod
    def _download_paper(arxiv_id: str) -> Optional[Any]:
        try:
            client = arxiv.Client()
            search = arxiv.Search(id_list=[arxiv_id])
            return next(client.results(search))
        except StopIteration:
            logger.error(f"Paper not found: '{arxiv_id}'")
            return None
        except Exception as e:
            logger.error(f"Error downloading paper '{arxiv_id}': {str(e)}")
            return None

    def _process_pdf_content(self, pdf_path: str) -> Optional[str]:
        content = self._try_llama_parse(pdf_path)
        if content:
            return content

        logger.warning("LlamaParse failed, falling back to unstructured")
        return self._process_pdf_with_unstructured(pdf_path)

    def _try_llama_parse(self, pdf_path: str) -> Optional[str]:
        retry_count = 0

        while retry_count < self.LLAMA_PARSE_MAX_RETRIES:
            try:
                documents = self.llama_parser.load_data(file_path=pdf_path)
                if documents:
                    text_content = "\n".join(doc.text for doc in documents)
                    text_content = self._extract_main_content(text_content)
                    return text_content.strip() if text_content else None
                retry_count += 1
                if retry_count < self.LLAMA_PARSE_MAX_RETRIES:
                    logger.warning(
                        f"LlamaParse returned no documents, retrying ({retry_count}/{self.LLAMA_PARSE_MAX_RETRIES})"
                    )
                    time.sleep(self.LLAMA_PARSE_RETRY_DELAY)
            except Exception as e:
                logger.warning(f"LlamaParse failed with error: {str(e)}")
                break
        return None

    def _process_pdf_with_unstructured(self, pdf_path: str) -> Optional[str]:
        text_content = self._extract_text_from_pdf(pdf_path)
        if not text_content:
            return None

        text_content = self._extract_main_content(text_content)
        return text_content.strip() if text_content else None

    def _extract_text_from_pdf(self, pdf_path: str) -> Optional[str]:
        try:
            elements = partition_pdf(filename=pdf_path)
            text_content = "\n".join(str(element) for element in elements)
            return text_content
        except Exception as e:
            logger.error(f"Error extracting text from PDF: {str(e)}")
            return None

    def _extract_main_content(self, text_content: str) -> Optional[str]:
        max_chars = 200000
        offset = 10000

        if not self.prompt_template or not self.llm or not self.output_parser:
            return text_content

        try:
            prompt = self.prompt_template.get_prompt()
            messages = prompt.format_messages(text=text_content[:max_chars])
            response = self.llm.chat(messages)
            response_content = cast(str, response.message.content)

            markers = self.output_parser.parse(response_content)
            if not isinstance(markers, dict):
                return None

            start_marker = markers.get("start_marker", "")
            end_marker = markers.get("end_marker", "")

            if not start_marker or not end_marker:
                return None

            start_idx = text_content.find(start_marker)
            end_idx = text_content.find(end_marker, start_idx + offset)

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

            return text_content[start_idx:end_idx]

        except Exception as e:
            logger.error(f"Error extracting main content: {str(e)}")
            return None
