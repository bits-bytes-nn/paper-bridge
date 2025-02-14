import os
import requests
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, cast
import arxiv
import boto3
import fitz
from llama_index.llms.bedrock import Bedrock
from pydantic import BaseModel, field_validator
from .aws_helpers import get_cross_inference_model_id
from .constants import URLs
from .logger import logger
from .prompts import MainContentExtractionPrompt
from .utils import HTMLTagOutputParser, measure_execution_time
from paper_bridge.indexer.configs.config import Config


class Paper(BaseModel):
    arxiv_id: str
    authors: List[str]
    published_at: datetime
    title: str
    summary: str
    upvotes: int
    thumbnail: Optional[str] = None
    content: Optional[str] = None

    @field_validator("authors")
    def validate_authors(cls, v: List[str]) -> List[str]:
        if not v:
            raise ValueError("Authors list cannot be empty")
        return v


class PaperFetcher:
    DEFAULT_TIMEOUT: int = 10
    MAX_RETRIES: int = 3
    RETRY_DELAY: int = 1
    MAX_WORKERS: int = 4

    def __init__(
        self,
        config: Config,
        profile_name: Optional[str] = None,
        timeout: int = DEFAULT_TIMEOUT,
    ) -> None:
        self.boto3_session = boto3.Session(profile_name=profile_name)

        self._init_config_values(config, timeout)
        self._init_llm_components(config, profile_name)

    def _init_config_values(self, config: Config, timeout: int) -> None:
        self.papers_per_day = max(1, config.indexing.papers_per_day)
        self.days_to_fetch = max(1, config.indexing.days_to_fetch)
        self.min_upvotes = (
            max(0, config.indexing.min_upvotes)
            if config.indexing.min_upvotes is not None
            else None
        )
        self.timeout = max(1, timeout)

    def _init_llm_components(self, config: Config, profile_name: Optional[str]) -> None:
        self.prompt_template = None
        self.llm = None
        self.output_parser = None

        if config.indexing.main_content_extraction_model_id:
            logger.info("Initializing LLM for extracting main content from papers")
            self.prompt_template = MainContentExtractionPrompt
            self.llm = Bedrock(
                model=get_cross_inference_model_id(
                    self.boto3_session,
                    config.indexing.main_content_extraction_model_id.value,
                    config.resources.bedrock_region_name,
                ),
                aws_region=config.resources.bedrock_region_name,
                profile_name=profile_name,
            )
            self.output_parser = HTMLTagOutputParser(
                tag_names=self.prompt_template.OUTPUT_VARIABLES
            )

    @measure_execution_time
    def fetch_papers_for_date_range(
        self, target_date: Optional[datetime] = None
    ) -> Dict[str, List[Paper]]:
        def _get_target_date(target_date: Optional[datetime]) -> datetime:
            if target_date is None:
                return datetime.now(timezone.utc).replace(
                    hour=0, minute=0, second=0, microsecond=0
                ).astimezone(timezone.utc) - timedelta(days=1)
            return target_date

        try:
            target_date = _get_target_date(target_date)
            papers_by_date = self._fetch_papers_by_date_range(target_date)
            filtered_papers = self._filter_and_sort_papers(papers_by_date)
            return self._process_selected_papers(filtered_papers)
        except requests.RequestException as e:
            logger.error("Error fetching papers: %s", e)
            return {}
        except Exception as e:
            logger.error("Unexpected error: %s", e)
            return {}

    def _fetch_papers_by_date_range(self, end_date: datetime) -> Dict[str, List[Paper]]:
        def _date_range(start_date: datetime, end_date: datetime):
            current_date = end_date
            while current_date >= start_date:
                yield current_date
                current_date -= timedelta(days=1)

        papers_by_date: Dict[str, List[Paper]] = {}
        start_date = end_date - timedelta(days=self.days_to_fetch - 1)

        for current_date in _date_range(start_date, end_date):
            date_str = current_date.strftime("%Y-%m-%d")
            papers_by_date[current_date.date().isoformat()] = self._fetch_daily_papers(
                date_str, current_date
            )

        return papers_by_date

    def _fetch_daily_papers(self, date_str: str, current_date: datetime) -> List[Paper]:
        response = self._make_request(f"{URLs.HF_DAILY_PAPERS.url}?date={date_str}")
        if not response:
            return []

        daily_papers = response.json()
        papers: List[Paper] = []

        for paper_data in daily_papers:
            paper = self._process_paper_metadata(paper_data, current_date)
            if paper:
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
                time.sleep(self.RETRY_DELAY)

    def _process_paper_metadata(
        self, paper_data: Dict[str, Any], current_date: datetime
    ) -> Optional[Paper]:
        def _extract_author_names(authors: List[Dict[str, str] | str]) -> List[str]:
            author_names = []
            for author in authors:
                if isinstance(author, dict) and "name" in author:
                    author_names.append(author["name"])
                elif isinstance(author, str):
                    author_names.append(author)
            return author_names

        published_at = self._parse_date(paper_data.get("publishedAt"))
        if not published_at or published_at.date() != current_date.date():
            return None

        author_names = _extract_author_names(paper_data["paper"]["authors"])

        try:
            paper = Paper(
                arxiv_id=paper_data["paper"]["id"],
                authors=author_names,
                published_at=published_at,
                title=paper_data["paper"]["title"],
                summary=paper_data["paper"]["summary"],
                upvotes=paper_data["paper"]["upvotes"],
                thumbnail=paper_data["paper"].get("thumbnail"),
            )
            if self.min_upvotes is None or paper.upvotes >= self.min_upvotes:
                return paper
        except ValueError as e:
            logger.error(f"Error creating Paper object: {e}")

        return None

    def _filter_and_sort_papers(
        self, papers_by_date: Dict[str, List[Paper]]
    ) -> Dict[str, List[Paper]]:
        return {
            date: sorted(papers, key=lambda x: x.upvotes, reverse=True)[
                : self.papers_per_day
            ]
            for date, papers in papers_by_date.items()
        }

    def _process_selected_papers(
        self, filtered_papers: Dict[str, List[Paper]]
    ) -> Dict[str, List[Paper]]:
        with ThreadPoolExecutor(max_workers=self.MAX_WORKERS) as executor:
            futures = {}
            for papers in filtered_papers.values():
                for paper in papers:
                    future = executor.submit(
                        self.download_and_parse_paper, paper.arxiv_id
                    )
                    futures[future] = paper

            for future in as_completed(futures):
                paper = futures[future]
                try:
                    content = future.result()
                    if content:
                        paper.content = content
                except Exception as e:
                    logger.error(f"Error processing paper {paper.arxiv_id}: {str(e)}")

        return filtered_papers

    def _parse_date(self, date_str: Optional[str]) -> Optional[datetime]:
        if not date_str:
            return None
        try:
            return datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        except ValueError:
            return None

    def download_and_parse_paper(self, arxiv_id: str) -> Optional[str]:
        try:
            paper = self._download_paper(arxiv_id)
            if not paper:
                return None

            temp_dir = tempfile.mkdtemp()
            try:
                paper.download_pdf(temp_dir)

                pdf_files = [f for f in os.listdir(temp_dir) if f.endswith(".pdf")]
                if not pdf_files:
                    logger.error(f"No PDF file found in temp directory for {arxiv_id}")
                    return None

                pdf_path = os.path.join(temp_dir, pdf_files[0])
                return self._process_pdf_content(pdf_path)
            finally:
                for file in os.listdir(temp_dir):
                    os.unlink(os.path.join(temp_dir, file))
                os.rmdir(temp_dir)

        except Exception as e:
            logger.error(f"Error downloading/parsing paper {arxiv_id}: {str(e)}")
            return None

    def _download_paper(self, arxiv_id: str) -> Optional[Any]:
        try:
            search = arxiv.Search(id_list=[arxiv_id])
            return next(search.results())
        except Exception as e:
            logger.error(f"Error downloading paper {arxiv_id}: {str(e)}")
            return None

    def _process_pdf_content(self, pdf_path: str) -> Optional[str]:
        text_content = self._extract_text_from_pdf(pdf_path)
        if not text_content:
            return None

        if self.llm:
            text_content = self._extract_main_content(text_content)

        return text_content.strip() if text_content else None

    def _extract_text_from_pdf(self, pdf_path: str) -> Optional[str]:
        try:
            text_content = []
            with fitz.open(pdf_path) as pdf_doc:
                for page in pdf_doc:
                    text = page.get_text()
                    text_content.append(text)
            return "\n".join(text_content)
        except Exception as e:
            logger.error(f"Error extracting text from PDF: {str(e)}")
            return None

    def _extract_main_content(self, text_content: str) -> Optional[str]:
        # TODO: enhance the accuracy of the main content extraction
        if not self.prompt_template or not self.llm or not self.output_parser:
            return text_content

        try:
            prompt = self.prompt_template.get_prompt()
            messages = prompt.format_messages(text=text_content)
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
            end_idx = text_content.find(end_marker)

            logger.debug(
                "start_idx: %s, start_marker: %s, end_idx: %s, end_marker: %s",
                start_idx,
                start_marker,
                end_idx,
                end_marker,
            )

            if start_idx == -1:
                start_idx = 0
            if end_idx == -1:
                end_idx = len(text_content) - len(end_marker)

            return text_content[start_idx : end_idx + len(end_marker)]

        except Exception as e:
            logger.error(f"Error extracting main content: {str(e)}")
            return None
