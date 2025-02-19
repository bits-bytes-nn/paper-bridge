import os
import requests
import tempfile
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Union
import arxiv
import boto3
import cv2
import numpy as np
import pdf2image
import pytesseract
from layoutparser.elements.layout import Layout
from layoutparser.models import Detectron2LayoutModel
from pydantic import BaseModel, field_validator
from paper_bridge.indexer.configs.config import Config
from .constants import URLs
from .logger import logger
from .utils import measure_execution_time


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
    @classmethod
    def validate_authors(cls, v: List[str]) -> List[str]:
        if not v:
            raise ValueError("Authors list cannot be empty")
        return v

    def to_dict(self) -> Dict[str, Any]:
        return self.model_dump()


class PaperFetcher:
    DEFAULT_TIMEOUT: int = 10
    MAX_RETRIES: int = 3
    RETRY_DELAY: int = 1
    MAX_WORKERS: int = 4

    EXCLUDED_SECTIONS: frozenset[str] = frozenset(
        {
            "acknowledgments",
            "abstract",
            "appendix",
            "bibliography",
            "references",
        }
    )
    VALID_BLOCK_TYPES: frozenset[str] = frozenset({"List", "Text", "Title"})
    MODEL_CONFIG: str = "lp://PubLayNet/mask_rcnn_X_101_32x8d_FPN_3x/config"
    SCORE_THRESHOLD: float = 0.8
    LAYOUT_SCORE_THRESHOLD: float = 0.5

    def __init__(
        self,
        config: Config,
        profile_name: Optional[str] = None,
        timeout: int = DEFAULT_TIMEOUT,
    ) -> None:
        self.boto3_session = self._init_boto3_session(profile_name)
        self.layout_model = self._init_layout_model()
        self._init_config_values(config, timeout)

    def _init_boto3_session(self, profile_name: Optional[str]) -> boto3.Session:
        return boto3.Session(profile_name=profile_name)

    def _init_layout_model(self) -> Detectron2LayoutModel:
        return Detectron2LayoutModel(
            self.MODEL_CONFIG,
            extra_config=["MODEL.ROI_HEADS.SCORE_THRESH_TEST", self.SCORE_THRESHOLD],
        )

    def _init_config_values(self, config: Config, timeout: int) -> None:
        self.papers_per_day = max(1, config.indexing.papers_per_day)
        self.days_to_fetch = max(1, config.indexing.days_to_fetch)
        self.min_upvotes = (
            max(0, config.indexing.min_upvotes)
            if config.indexing.min_upvotes is not None
            else None
        )
        self.timeout = max(1, timeout)

    @measure_execution_time
    def fetch_papers_for_date_range(
        self, target_date: Optional[datetime] = None
    ) -> Dict[str, List[Paper]]:
        try:
            target_date = self._get_target_date(target_date)
            papers_by_date = self._fetch_papers_by_date_range(target_date)
            filtered_papers = self._filter_and_sort_papers(papers_by_date)
            return self._process_selected_papers(filtered_papers)
        except requests.RequestException as e:
            logger.error("Error fetching papers: %s", e)
            return {}
        except Exception as e:
            logger.error("Unexpected error: %s", e)
            return {}

    @staticmethod
    def _get_target_date(target_date: Optional[datetime]) -> datetime:
        if target_date is None:
            return datetime.now(timezone.utc).replace(
                hour=0, minute=0, second=0, microsecond=0
            ).astimezone(timezone.utc) - timedelta(days=1)
        return target_date

    def _fetch_papers_by_date_range(self, end_date: datetime) -> Dict[str, List[Paper]]:
        papers_by_date: Dict[str, List[Paper]] = {}
        start_date = end_date - timedelta(days=self.days_to_fetch - 1)

        for current_date in self._date_range(start_date, end_date):
            date_str = current_date.strftime("%Y-%m-%d")
            papers_by_date[current_date.date().isoformat()] = self._fetch_daily_papers(
                date_str, current_date
            )

        return papers_by_date

    @staticmethod
    def _date_range(start_date: datetime, end_date: datetime):
        current_date = end_date
        while current_date >= start_date:
            yield current_date
            current_date -= timedelta(days=1)

    def _fetch_daily_papers(self, date_str: str, current_date: datetime) -> List[Paper]:
        response = self._make_request(f"{URLs.HF_DAILY_PAPERS.url}?date={date_str}")
        if not response:
            return []

        daily_papers = response.json()
        return [
            paper
            for paper_data in daily_papers
            if (paper := self._process_paper_metadata(paper_data, current_date))
        ]

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
            if self.min_upvotes is None or paper.upvotes >= self.min_upvotes:
                return paper
        except (ValueError, KeyError) as e:
            logger.error(f"Error creating Paper object: {e}")

        return None

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

    def _process_selected_papers(
        self, filtered_papers: Dict[str, List[Paper]]
    ) -> Dict[str, List[Paper]]:
        with ThreadPoolExecutor(max_workers=self.MAX_WORKERS) as executor:
            futures_to_papers = {
                executor.submit(self.download_and_parse_paper, paper.arxiv_id): paper
                for papers in filtered_papers.values()
                for paper in papers
            }

            for future in as_completed(futures_to_papers):
                paper = futures_to_papers[future]
                try:
                    if content := future.result():
                        paper.content = content
                except Exception as e:
                    logger.error(f"Error processing paper {paper.arxiv_id}: {str(e)}")

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
            if not (paper := self._download_paper(arxiv_id)):
                return None

            with tempfile.TemporaryDirectory() as temp_dir:
                paper.download_pdf(temp_dir)
                pdf_files = [f for f in os.listdir(temp_dir) if f.endswith(".pdf")]
                if not pdf_files:
                    logger.error(f"No PDF file found in temp directory for {arxiv_id}")
                    return None

                pdf_path = os.path.join(temp_dir, pdf_files[0])
                return self._process_pdf_content(pdf_path)

        except Exception as e:
            logger.error(f"Error downloading/parsing paper {arxiv_id}: {str(e)}")
            return None

    @staticmethod
    def _download_paper(arxiv_id: str) -> Optional[Any]:
        try:
            client = arxiv.Client()
            search = arxiv.Search(id_list=[arxiv_id])
            return next(client.results(search))
        except StopIteration:
            logger.error(f"Paper not found: {arxiv_id}")
            return None
        except Exception as e:
            logger.error(f"Error downloading paper {arxiv_id}: {str(e)}")
            return None

    def _process_pdf_content(self, pdf_path: str) -> Optional[str]:
        try:
            images = pdf2image.convert_from_path(pdf_path)
            if not images:
                return None

            main_content: List[str] = []
            self._analyze_sample_layout(images[0])

            with self._modified_layout_threshold():
                for idx, image in enumerate(images):
                    page_content = self._process_page(image, idx)
                    if page_content:
                        main_content.extend(page_content)

            if not main_content:
                return self._backup_extraction(images)

            return "\n".join(main_content)

        except Exception as e:
            logger.error(f"Error processing PDF content: {str(e)}")
            logger.error(traceback.format_exc())
            return None

    def _analyze_sample_layout(self, sample_image: Any) -> None:
        sample_image_cv = cv2.cvtColor(np.array(sample_image), cv2.COLOR_RGB2BGR)
        if sample_layout := self.layout_model.detect(sample_image_cv):
            self._log_layout_info(sample_layout)

    def _log_layout_info(self, layout: Layout) -> None:
        logger.debug(f"Layout contains {len(layout)} blocks")
        if not layout:
            return

        sample_block = layout[0]
        logger.debug(f"Sample block dir: {dir(sample_block)}")
        logger.debug(
            f"Sample block attributes: {vars(sample_block) if hasattr(sample_block, '__dict__') else 'No __dict__'}"
        )

        for attr in ["type", "category", "label"]:
            if hasattr(sample_block, attr):
                logger.debug(
                    f"Block {attr}s in first page: {[getattr(b, attr) for b in layout]}"
                )

    def _modified_layout_threshold(self):
        class ThresholdContext:
            def __init__(self, fetcher):
                self.fetcher = fetcher
                self.original_threshold = fetcher.SCORE_THRESHOLD

            def __enter__(self):
                try:
                    self.fetcher.layout_model.model.model.roi_heads.box_predictor.test_score_thresh = (
                        self.fetcher.LAYOUT_SCORE_THRESHOLD
                    )
                except:
                    logger.warning("Could not modify model threshold dynamically")

            def __exit__(self, exc_type, exc_val, exc_tb):
                try:
                    self.fetcher.layout_model.model.model.roi_heads.box_predictor.test_score_thresh = (
                        self.original_threshold
                    )
                except:
                    pass

        return ThresholdContext(self)

    def _process_page(self, image: Any, idx: int) -> List[str]:
        image_cv = cv2.cvtColor(np.array(image), cv2.COLOR_RGB2BGR)
        page_content: List[str] = []

        if page_text := self._extract_page_text(image, idx):
            return [page_text]

        if layout := self.layout_model.detect(image_cv):
            self._log_block_types(layout, idx)
            page_content.extend(self._process_layout_blocks(layout, image_cv))

        return page_content

    def _extract_page_text(self, image: Any, idx: int) -> Optional[str]:
        page_text = pytesseract.image_to_string(image).strip()
        if page_text and not any(
            section in page_text.lower() for section in self.EXCLUDED_SECTIONS
        ):
            logger.debug(
                f"Extracted {len(page_text)} characters from page {idx+1} using direct OCR"
            )
            return f"--- Page {idx+1} ---\n{page_text}"
        return None

    def _log_block_types(self, layout: Layout, idx: int) -> None:
        block_types = []
        for block in layout:
            for attr in ["type", "category", "label"]:
                if hasattr(block, attr):
                    block_types.append(getattr(block, attr))
                    break
        logger.debug(f"Page {idx+1} has blocks with types: {set(block_types)}")

    def _process_layout_blocks(self, layout: Layout, image_cv: Any) -> List[str]:
        block_texts = []
        for block in layout:
            crop = block.crop_image(image_cv)
            if extracted_text := pytesseract.image_to_string(crop).strip():
                if not any(
                    section in extracted_text.lower()
                    for section in self.EXCLUDED_SECTIONS
                ):
                    block_texts.append(extracted_text)
        return block_texts

    def _backup_extraction(self, images: List[Any]) -> Optional[str]:
        logger.warning("No text content extracted, trying backup method")
        try:
            all_text = []
            for idx, image in enumerate(images):
                if page_text := pytesseract.image_to_string(image).strip():
                    all_text.append(f"--- Page {idx+1} ---\n{page_text}")

            if all_text:
                logger.debug(
                    f"Extracted text using backup method: {len(''.join(all_text))} characters"
                )
                return "\n\n".join(all_text)
        except Exception as e:
            logger.error(f"Backup extraction failed: {str(e)}")

        return None
