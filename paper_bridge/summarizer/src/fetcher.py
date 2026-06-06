from __future__ import annotations

import asyncio
import base64
import json
import re
import time
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime, timedelta
from enum import Enum, auto
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import arxiv
import boto3
import fitz
import httpx
import requests
from bs4 import BeautifulSoup, Tag
from llama_index.core.llms import ChatMessage, ImageBlock, MessageRole, TextBlock
from llama_index.core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field, HttpUrl, field_validator

from paper_bridge.shared import PaperScorer, SelectionConfig
from paper_bridge.summarizer.configs import Config

from .aws_helpers import get_cross_inference_model_id, get_ssm_param_value
from .constants import EnvVars, LanguageModelId, LocalPaths, SSMParams, URLs

if TYPE_CHECKING:
    # Imported lazily at runtime to keep this module importable for unit tests
    # without eagerly loading the heavy bedrock-converse / aioboto3 stack.
    from llama_index.llms.bedrock_converse import BedrockConverse
from .logger import is_aws_env, logger
from .prompts import FigureAnalysisPrompt
from .utils import HTMLTagOutputParser, extract_text_from_html, measure_execution_time

# Fallback when a parser is constructed without an explicit limit (e.g. in tests
# or ad-hoc use). Production paths pass the value from
# ``config.summarization.figure_analysis_max_tokens``.
DEFAULT_FIGURE_ANALYSIS_MAX_TOKENS: int = 4096


class Content(BaseModel):
    text: str = Field(default="")

    def __str__(self) -> str:
        return f"Content(text='{self.text}')"

    @field_validator("text")
    def validate_text(cls, v: str) -> str:
        return v.strip()


class Figure(BaseModel):
    figure_id: str
    path: str
    caption: str | None = Field(default=None)
    analysis: str | None = Field(default=None)

    def __str__(self) -> str:
        return (
            f"Figure("
            f"figure_id='{self.figure_id}', "
            f"path='{self.path}', "
            f"caption='{self.caption}', "
            f"analysis='{self.analysis}'"
            f")"
        )

    @classmethod
    async def from_llm(
        cls,
        prompt_template: ChatPromptTemplate,
        multi_modal_llm: BedrockConverse,
        output_parser: HTMLTagOutputParser,
        figure_id: str,
        path: str,
        caption: str | None = None,
        timeout: int = 60,
    ) -> Figure:
        try:
            image_data = await cls._get_image_data(path, timeout)
            user_message = cls._generate_prompt(prompt_template, caption or "")

            # graphrag v3 forced dropping llama-index-multi-modal-llms-bedrock
            # (it pins core==0.13.6, conflicting with v3's core==0.14.20). Modern
            # BedrockConverse handles images natively via chat messages with an
            # ImageBlock; ``image`` accepts the base64-encoded bytes/str directly
            # (the mimetype is auto-detected and the converse client forwards the
            # raw bytes to the Bedrock Converse API).
            message = ChatMessage(
                role=MessageRole.USER,
                blocks=[
                    TextBlock(text=user_message),
                    ImageBlock(image=image_data),
                ],
            )
            response = await multi_modal_llm.achat([message])

            analysis = cast(
                str, output_parser.parse(str(response.message.content) or "")
            )

        except Exception as e:
            logger.warning("Failed to analyze figure %s: %s", figure_id, str(e))
            analysis = None

        return cls(
            figure_id=figure_id,
            path=path,
            caption=caption,
            analysis=analysis,
        )

    @classmethod
    def _generate_prompt(cls, prompt_template: ChatPromptTemplate, caption: str) -> str:
        try:
            return prompt_template.format(caption=caption)
        except Exception as e:
            logger.warning("Error formatting prompt: %s", str(e))
            return f"Analyze this figure with caption: {caption}"

    @staticmethod
    async def _get_image_data(path: str, timeout: int) -> str:
        async with httpx.AsyncClient(timeout=timeout) as client:
            if path.startswith(("http://", "https://")):
                try:
                    response = await client.get(path)
                    response.raise_for_status()
                    return base64.b64encode(response.content).decode("utf-8")
                except httpx.HTTPError as e:
                    raise Exception(f"Failed to fetch image: {str(e)}") from e

            try:
                with open(path, "rb") as f:
                    return base64.b64encode(f.read()).decode("utf-8")
            except OSError as e:
                raise Exception(f"Failed to read image file: {str(e)}") from e


class PaperStatus(Enum):
    FAILED = auto()
    PENDING = auto()
    PROCESSED = auto()


class Paper(BaseModel):
    arxiv_id: str
    authors: list[str]
    published_at: datetime
    title: str
    summary: str
    upvotes: int
    thumbnail: str | None = None
    content: str | None = None
    base_date: str
    pdf_url: HttpUrl | None = Field(default=None)
    figures: list[Figure] = Field(default_factory=list)
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


class BaseParser:
    def __init__(self, timeout: int = 60) -> None:
        self.timeout = timeout
        self.sync_client = httpx.Client(timeout=timeout, follow_redirects=False)
        self.async_client = httpx.AsyncClient(timeout=timeout, follow_redirects=False)
        self.url = None

        self.prompt_template = None
        self.llm = None
        self.multi_modal_llm = None
        self.output_parser = None

    async def __aenter__(self) -> BaseParser:
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.async_client.aclose()

    def _initialize_chain(
        self,
        model_id: LanguageModelId,
        profile_name: str | None,
        region_name: str,
        boto3_session: boto3.Session,
        max_tokens: int = DEFAULT_FIGURE_ANALYSIS_MAX_TOKENS,
    ) -> None:
        from llama_index.llms.bedrock_converse import BedrockConverse

        self.prompt_template = FigureAnalysisPrompt.get_prompt()
        # Use the cross-region inference profile for both the (kept-for-API)
        # text LLM and the multimodal figure-analysis LLM. BedrockConverse
        # handles image input via ChatMessage ImageBlocks (see Figure.from_llm),
        # which replaces the removed BedrockMultiModal path.
        cross_region_model_id = get_cross_inference_model_id(
            boto3_session,
            model_id.value,
            region_name,
        )
        self.llm = BedrockConverse(
            cross_region_model_id,
            temperature=0.0,
            max_tokens=max_tokens,
            profile_name=profile_name,
            region_name=region_name,
        )
        self.multi_modal_llm = BedrockConverse(
            cross_region_model_id,
            temperature=0.0,
            max_tokens=max_tokens,
            region_name=region_name,
            profile_name=profile_name,
        )
        self.output_parser = HTMLTagOutputParser(
            tag_names=FigureAnalysisPrompt.OUTPUT_VARIABLES
        )


class HTMLRichParser(BaseParser):
    def __init__(
        self,
        figure_analysis_model_id: LanguageModelId,
        profile_name: str | None = None,
        boto3_session: boto3.Session | None = None,
        region_name: str = "us-west-2",
        timeout: int = 60,
        max_tokens: int = DEFAULT_FIGURE_ANALYSIS_MAX_TOKENS,
    ) -> None:
        super().__init__(timeout=timeout)
        self.boto3_session = boto3_session or boto3.Session(
            region_name=region_name, profile_name=profile_name
        )
        self._initialize_chain(
            figure_analysis_model_id,
            profile_name,
            region_name,
            self.boto3_session,
            max_tokens,
        )

    async def parse(
        self, arxiv_id: str, extract_text: bool = True
    ) -> tuple[list[Figure], Content]:
        self.url = f"{URLs.ARXIV_HTML.url}/{arxiv_id}"

        try:
            html_content = await self._fetch_html()
            soup = BeautifulSoup(html_content, "html.parser")

            figures = await self._extract_figures(soup)
            content = self._extract_content(soup, extract_text)

            logger.info("Extracted %d characters from HTML", len(content.text))
            logger.info("Extracted %d figures from HTML", len(figures))
            return figures, content

        except Exception as e:
            logger.warning("No HTML content found for '%s': %s", arxiv_id, str(e))
            raise

    async def _extract_figures(self, soup: Any) -> list[Figure]:
        figure_elements = []

        for figure in soup.select(".ltx_figure"):
            img = figure.select_one("img")
            caption = figure.select_one("figcaption")

            if img and caption and isinstance(img, Tag) and isinstance(caption, Tag):
                figure_elements.append((img, caption))

        table_images = [
            (img, img.get("alt", "Refer to caption"))
            for img in soup.select(".ltx_td > img.ltx_graphics")
            if isinstance(img, Tag)
        ]

        if (
            self.prompt_template is None
            or self.multi_modal_llm is None
            or self.output_parser is None
        ):
            raise ValueError("LLM chain is not initialized")

        all_figures = []
        for i, (img, caption) in enumerate(figure_elements):
            all_figures.append(
                Figure.from_llm(
                    prompt_template=self.prompt_template,
                    multi_modal_llm=self.multi_modal_llm,
                    output_parser=self.output_parser,
                    figure_id=str(i),
                    path=f"{self.url}/{img.get('src', '')}",
                    caption=caption.text.strip(),
                )
            )

        offset = len(figure_elements)
        for i, (img, alt_text) in enumerate(table_images, start=offset):
            all_figures.append(
                Figure.from_llm(
                    prompt_template=self.prompt_template,
                    multi_modal_llm=self.multi_modal_llm,
                    output_parser=self.output_parser,
                    figure_id=str(i),
                    path=f"{self.url}/{img.get('src', '')}",
                    caption=str(alt_text) if alt_text else None,
                )
            )

        return await asyncio.gather(*all_figures)

    @staticmethod
    def _extract_content(soup: Any, extract_text: bool) -> Content:
        for selector in [".ltx_page_main", "body"]:
            if content := soup.select_one(selector):
                return Content(
                    text=(
                        extract_text_from_html(str(content))
                        if extract_text
                        else str(content)
                    )
                )
        return Content()

    async def _fetch_html(self) -> str:
        try:
            response = await self.async_client.get(self.url)
            response.raise_for_status()

            if response.status_code in (301, 302, 303, 307, 308):
                logger.warning("Redirect detected for URL '%s'", self.url)
                raise Exception("Redirect detected")

            return response.text

        except httpx.HTTPError as e:
            logger.error("Failed to fetch HTML from '%s': %s", self.url, str(e))
            raise Exception(f"Failed to fetch HTML: {str(e)}") from e


class PDFParser(BaseParser):
    def __init__(
        self,
        figure_analysis_model_id: LanguageModelId,
        boto3_session: boto3.Session | None = None,
        profile_name: str | None = None,
        region_name: str = "us-west-2",
        timeout: int = 60,
        api_key: str | None = None,
        max_tokens: int = DEFAULT_FIGURE_ANALYSIS_MAX_TOKENS,
    ):
        super().__init__(timeout=timeout)
        self.boto3_session = boto3_session or boto3.Session(
            region_name=region_name, profile_name=profile_name
        )
        self._initialize_chain(
            figure_analysis_model_id,
            profile_name,
            region_name,
            self.boto3_session,
            max_tokens,
        )
        self.api_key = api_key or EnvVars.UPSTAGE_API_KEY.env_value
        if not self.api_key:
            # NOTE: ``.value`` here is intentional — it is the env-var NAME shown
            # to the user, not the (missing) value.
            raise ValueError(
                f"{EnvVars.UPSTAGE_API_KEY.value} must be provided or set in environment"
            )

    async def parse(
        self,
        pdf_path: Path,
        figures_dir: Path | None = None,
        use_cache: bool = True,
        extract_text: bool = True,
    ) -> tuple[list[Figure], Content]:
        try:
            figures_dir = figures_dir or pdf_path.parent / LocalPaths.FIGURES_DIR.value
            figures_dir.mkdir(parents=True, exist_ok=True)

            return await self._parse_with_upstage(
                pdf_path, figures_dir, use_cache, extract_text
            )

        except Exception as e:
            logger.warning("Failed to parse PDF document '%s': %s", pdf_path, str(e))
            return [], Content()

    async def _parse_with_upstage(
        self,
        pdf_path: Path,
        figures_dir: Path,
        use_cache: bool,
        extract_text: bool,
    ) -> tuple[list[Figure], Content]:
        response = await self._get_or_parse_document(pdf_path, use_cache)
        elements = response.get("elements", [])

        figures = await self._extract_figures(elements, pdf_path, figures_dir)
        content_text = response.get("content", {}).get("html", "").strip()

        content = Content(
            text=(
                extract_text_from_html(content_text) if extract_text else content_text
            )
        )
        logger.info(
            "Successfully extracted %d figures from PDF using Upstage", len(figures)
        )
        return figures, content

    async def _get_or_parse_document(
        self, pdf_path: Path, use_cache: bool
    ) -> dict[str, Any]:
        parsed_path = pdf_path.parent / LocalPaths.PARSED_FILE.value

        if use_cache and parsed_path.exists():
            return self._load_cached_response(parsed_path)

        response = self._request_document_parse(pdf_path)
        self._cache_response(parsed_path, response)
        return response

    @staticmethod
    def _cache_response(path: Path, response: dict[str, Any]) -> None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(response, f, indent=2, ensure_ascii=False)
        except OSError as e:
            logger.warning("Failed to cache response: %s", str(e))

    @staticmethod
    def _load_cached_response(path: Path) -> dict[str, Any]:
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            raise Exception(f"Failed to load cached response: {str(e)}") from e

    def _request_document_parse(self, pdf_path: Path) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {self.api_key}"}

        try:
            with open(pdf_path, "rb") as f:
                files = {"document": f}
                response = requests.post(
                    URLs.UPSTAGE_DOCUMENT_PARSE.url,
                    headers=headers,
                    files=files,
                    timeout=self.timeout,
                )
                response.raise_for_status()
                return response.json()

        except (OSError, requests.RequestException) as e:
            logger.error("Failed to parse document: %s", str(e))
            raise Exception(f"Document parsing failed: {str(e)}") from e

    async def _extract_figures(
        self, elements: list[dict[str, Any]], pdf_path: Path, figures_dir: Path
    ) -> list[Figure]:
        figure_categories = frozenset({"chart", "figure"})

        figure_data = []
        for element in elements:
            if (
                element.get("category", "").lower() in figure_categories
                and len(element.get("coordinates", [])) >= 4
            ):
                soup = BeautifulSoup(
                    element.get("content", {}).get("html", ""), "html.parser"
                )
                img = soup.find("img")
                caption = ""
                if isinstance(img, Tag) and img.has_attr("alt"):
                    alt_text = img["alt"]
                    if isinstance(alt_text, str):
                        caption = alt_text.strip()

                figure_data.append(
                    {
                        "page": element.get("page", 1),
                        "coordinates": element.get("coordinates", []),
                        "caption": caption,
                        "figure_id": element.get("id", ""),
                    }
                )

        if not figure_data:
            return []

        paths = []
        try:
            with fitz.open(pdf_path) as doc:
                for idx, fig in enumerate(figure_data):
                    page_num = fig["page"] - 1
                    if 0 <= page_num < len(doc):
                        page = doc[page_num]
                        coords = fig["coordinates"]

                        rect_coords = [
                            coords[0]["x"] * page.rect.width,
                            coords[0]["y"] * page.rect.height,
                            coords[2]["x"] * page.rect.width,
                            coords[2]["y"] * page.rect.height,
                        ]

                        clip_rect = fitz.Rect(*rect_coords)
                        mat = fitz.Matrix(2, 2)
                        pix = page.get_pixmap(matrix=mat, clip=clip_rect, dpi=300)

                        figure_path = figures_dir / f"{idx}.png"
                        pix.save(figure_path)
                        paths.append(figure_path)
        except Exception as e:
            logger.error("Failed to extract figures from PDF: %s", str(e))
            return []

        if (
            self.prompt_template is None
            or self.multi_modal_llm is None
            or self.output_parser is None
        ):
            raise ValueError("LLM chain is not initialized")

        figure_tasks = [
            Figure.from_llm(
                prompt_template=self.prompt_template,
                multi_modal_llm=self.multi_modal_llm,
                output_parser=self.output_parser,
                figure_id=str(fd["figure_id"]) or str(i),
                path=str(path),
                caption=fd["caption"] or None,
            )
            for i, (fd, path) in enumerate(zip(figure_data, paths, strict=False))
        ]

        return await asyncio.gather(*figure_tasks)


class PaperFetcher:
    DEFAULT_TIMEOUT: int = 60
    MAX_RETRIES: int = 3
    RETRY_DELAY: int = 1
    MAX_WORKERS: int = 4
    MIN_PAPERS_PER_DAY: int = 1
    MIN_DAYS_TO_FETCH: int = 1
    MIN_UPVOTES: int = 0

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
        self._init_parsers(config, profile_name)

    def _configure(self, config: Config, timeout: int) -> None:
        self.config = config
        self.papers_per_day = max(
            self.MIN_PAPERS_PER_DAY, config.summarization.papers_per_day
        )
        self.days_to_fetch = max(
            self.MIN_DAYS_TO_FETCH, config.summarization.days_to_fetch
        )
        self.min_upvotes = (
            max(self.MIN_UPVOTES, config.summarization.min_upvotes)
            if config.summarization.min_upvotes is not None
            else None
        )
        self.timeout = max(1, timeout)
        self._scorer = PaperScorer(
            SelectionConfig(
                popularity_weight=config.summarization.selection_popularity_weight,
                recency_weight=config.summarization.selection_recency_weight,
                recency_half_life_days=(
                    config.summarization.selection_recency_half_life_days
                ),
                min_upvotes=self.min_upvotes,
            )
        )

    def _init_parsers(self, config: Config, profile_name: str | None) -> None:
        if config.summarization.figure_analysis_model_id is None:
            raise ValueError("'figure_analysis_model_id' is not set")

        figure_model_id = LanguageModelId(
            config.summarization.figure_analysis_model_id.value
        )
        figure_max_tokens = config.summarization.figure_analysis_max_tokens

        self.html_parser = HTMLRichParser(
            figure_analysis_model_id=figure_model_id,
            profile_name=profile_name,
            region_name=config.resources.bedrock_region_name,
            timeout=self.timeout,
            max_tokens=figure_max_tokens,
        )

        base_path = f"/{config.resources.project_name}-{config.resources.stage}"
        upstage_api_key = (
            get_ssm_param_value(
                self.boto3_session, f"{base_path}/{SSMParams.UPSTAGE_API_KEY.value}"
            )
            if is_aws_env()
            else EnvVars.UPSTAGE_API_KEY.env_value
        )

        self.pdf_parser = PDFParser(
            figure_analysis_model_id=figure_model_id,
            profile_name=profile_name,
            region_name=config.resources.bedrock_region_name,
            timeout=self.timeout,
            api_key=upstage_api_key,
            max_tokens=figure_max_tokens,
        )

    @measure_execution_time
    def fetch_papers_by_arxiv_ids(
        self, papers_dir: Path, arxiv_ids: list[str], parse_pdf: bool = False
    ) -> list[Paper]:
        try:
            papers = self._fetch_papers_by_arxiv_ids(arxiv_ids)
            return self._process_papers_concurrently(papers, papers_dir, parse_pdf)
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
        self, papers: list[Paper], papers_dir: Path, parse_pdf: bool = False
    ) -> list[Paper]:
        with ThreadPoolExecutor(max_workers=self.MAX_WORKERS) as executor:
            futures = {
                executor.submit(self.process_paper, paper, papers_dir, parse_pdf): paper
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
                    logger.error(
                        "Error processing paper %s: %s", paper.arxiv_id, str(e)
                    )
                    paper.status = PaperStatus.FAILED

        return papers

    @measure_execution_time
    def fetch_papers_for_date_range(
        self,
        papers_dir: Path,
        target_date: datetime | None = None,
        days_to_fetch: int | None = None,
        parse_pdf: bool = False,
    ) -> list[Paper]:
        try:
            target_date = self._get_target_date(target_date)
            days_to_fetch = days_to_fetch if days_to_fetch != 0 else None

            papers_by_date = self._fetch_papers_by_date_range(
                target_date, days_to_fetch
            )
            papers = self._select_papers(papers_by_date, target_date)
            return self._process_papers_concurrently(papers, papers_dir, parse_pdf)

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
                        "Failed to fetch data after %d attempts: %s",
                        self.MAX_RETRIES,
                        str(e),
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
                summary=paper_info.get("summary", ""),
                upvotes=paper_info.get("upvotes", 0),
                thumbnail=paper_info.get("thumbnail"),
                pdf_url=HttpUrl(f"{URLs.ARXIV_PDF.url}/{paper_info['id']}"),
                base_date=current_date.strftime("%Y-%m-%d"),
            )
            if self._meets_upvote_threshold(paper.upvotes):
                return paper
        except (KeyError, ValueError) as e:
            logger.error("Error creating Paper object: %s", str(e))

        return None

    @staticmethod
    def _parse_date(date_str: str | None) -> datetime | None:
        if not date_str:
            return None
        try:
            return datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        except ValueError:
            logger.error("Invalid date format: %s", date_str)
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
        summarizing the same paper multiple times.
        """
        selected: list[Paper] = []
        for papers in papers_by_date.values():
            selected.extend(
                self._scorer.select(papers, self.papers_per_day, reference_date)
            )
        return self._scorer.select(selected, len(selected), reference_date)

    def process_paper(
        self, paper: Paper, papers_dir: Path, parse_pdf: bool = False
    ) -> None:
        try:
            if parse_pdf:
                success = self._process_paper_with_pdf(paper, papers_dir)
            else:
                success = self._process_paper_with_html(paper)
                if not success:
                    success = self._process_paper_with_pdf(paper, papers_dir)

            if success:
                paper.status = PaperStatus.PROCESSED
            else:
                paper.status = PaperStatus.FAILED

        except Exception as e:
            logger.error("Error processing paper %s: %s", paper.arxiv_id, str(e))
            paper.status = PaperStatus.FAILED

    def _process_paper_with_pdf(self, paper: Paper, papers_dir: Path) -> bool | None:
        try:
            papers_dir = self._get_papers_dir(papers_dir, paper.arxiv_id)
            papers_dir.mkdir(parents=True, exist_ok=True)

            figures_dir = papers_dir / "figures"
            figures_dir.mkdir(exist_ok=True)

            pdf_path = self._download_arxiv_pdf(papers_dir, paper.arxiv_id)
            if not pdf_path:
                return False

            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                figures, content = loop.run_until_complete(
                    self.pdf_parser.parse(pdf_path, figures_dir)
                )
            finally:
                loop.close()

            paper.content = content.text
            paper.figures = figures

            return True
        except Exception as e:
            logger.warning("PDF parsing failed for %s: %s", paper.arxiv_id, str(e))
            return False

    @staticmethod
    def _get_papers_dir(papers_dir: Path, arxiv_id: str) -> Path:
        safe_id = arxiv_id.replace(".", "_")
        return papers_dir / safe_id

    @staticmethod
    def _download_arxiv_pdf(papers_dir: Path, arxiv_id: str) -> Path | None:
        try:
            client = arxiv.Client()
            search = arxiv.Search(id_list=[arxiv_id])
            paper = next(client.results(search))

            pdf_path = papers_dir / f"{arxiv_id}.pdf"
            paper.download_pdf(dirpath=str(papers_dir), filename=f"{arxiv_id}.pdf")

            return pdf_path if pdf_path.exists() else None
        except Exception as e:
            logger.error("Failed to download PDF for %s: %s", arxiv_id, str(e))
            return None

    def _process_paper_with_html(self, paper: Paper) -> bool | None:
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                figures, content = loop.run_until_complete(
                    self.html_parser.parse(paper.arxiv_id)
                )
            finally:
                loop.close()

            paper.content = content.text
            paper.figures = figures

            return True
        except Exception as e:
            logger.warning("HTML parsing failed for '%s': %s", paper.arxiv_id, str(e))
            return False
