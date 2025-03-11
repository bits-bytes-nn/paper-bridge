import asyncio
import base64
import json
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from enum import Enum, auto
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union, cast
import arxiv
import boto3
import fitz
import httpx
import requests
from bs4 import BeautifulSoup, Tag
from llama_index.core.prompts import ChatPromptTemplate
from llama_index.core.schema import ImageNode
from llama_index.llms.bedrock import Bedrock
from llama_index.multi_modal_llms.bedrock import BedrockMultiModal
from pydantic import BaseModel, Field, field_validator
from .aws_helpers import get_cross_inference_model_id, get_ssm_param_value
from .constants import EnvVars, LanguageModelId, LocalPaths, SSMParams, URLs
from .logger import is_aws_env, logger
from .prompts import FigureAnalysisPrompt
from .utils import HTMLTagOutputParser, extract_text_from_html, measure_execution_time
from paper_bridge.summarizer.configs import Config


class Content(BaseModel):
    text: str = Field(default="")

    def __str__(self) -> str:
        return f"Content(text='{self.text}')"

    @classmethod
    @field_validator("text")
    def validate_text(cls, v: str) -> str:
        return v.strip()


class Figure(BaseModel):
    figure_id: str
    path: str
    caption: Optional[str] = Field(default=None)
    analysis: Optional[str] = Field(default=None)

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
        multi_modal_llm: BedrockMultiModal,
        output_parser: HTMLTagOutputParser,
        figure_id: str,
        path: str,
        caption: Optional[str] = None,
    ) -> "Figure":
        try:
            image_data = await cls._get_image_data(path)
            formatted_caption = caption or "No caption available"

            user_message = cls._generate_prompt(prompt_template, formatted_caption)

            image_node = ImageNode(image=image_data)
            response = await multi_modal_llm.acomplete(
                prompt=user_message,
                image_documents=[image_node],
            )

            analysis = cast(str, output_parser.parse(response.text or ""))

        except Exception as e:
            logger.warning("Failed to analyze figure %s: %s", figure_id, str(e))
            analysis = None

        if path.startswith("/html/"):
            path = path.replace("/html/", "https://ar5iv.org//html/")

        return cls(
            figure_id=figure_id,
            path=path,
            caption=caption,
            analysis=analysis,
        )

    @classmethod
    def _generate_prompt(
        cls, prompt_template: ChatPromptTemplate, formatted_caption: str
    ) -> str:
        if isinstance(prompt_template, ChatPromptTemplate):
            try:
                formatted_prompt = prompt_template.format(caption=formatted_caption)

                if (
                    hasattr(formatted_prompt, "messages")
                    and len(formatted_prompt.messages) > 1
                ):
                    return formatted_prompt.messages[1].content
            except Exception as e:
                logger.warning(f"Error formatting prompt: {e}")

        return f"Analyze this figure with caption: {formatted_caption}"

    @staticmethod
    async def _get_image_data(path: str) -> str:
        async with httpx.AsyncClient(timeout=60) as client:
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
            except IOError as e:
                raise Exception(f"Failed to read image file: {str(e)}") from e


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
    figures: List[Dict[str, Any]] = Field(default_factory=list)
    status: PaperStatus = Field(default=PaperStatus.PENDING)

    @classmethod
    @field_validator("authors")
    def validate_authors(cls, authors: List[str]) -> List[str]:
        if not authors:
            raise ValueError("Authors list cannot be empty")
        return authors

    def to_dict(self) -> Dict[str, Any]:
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

    async def __aenter__(self) -> "BaseParser":
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.async_client.aclose()

    def _initialize_chain(
        self,
        model_id: LanguageModelId,
        profile_name: Optional[str],
        region_name: str,
        boto3_session: boto3.Session,
    ) -> None:
        self.prompt_template = FigureAnalysisPrompt.get_prompt()
        self.llm = Bedrock(
            get_cross_inference_model_id(
                boto3_session,
                model_id.value,
                region_name,
            ),
            temperature=0.0,
            max_tokens=4096,
            profile_name=profile_name,
            region_name=region_name,
        )
        self.multi_modal_llm = BedrockMultiModal(
            model=model_id.value,
            temperature=0.0,
            max_tokens=4096,
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
        profile_name: Optional[str] = None,
        boto3_session: Optional[boto3.Session] = None,
        region_name: str = "us-west-2",
        timeout: int = 60,
    ) -> None:
        super().__init__(timeout=timeout)
        self.boto3_session = boto3_session or boto3.Session(
            region_name=region_name, profile_name=profile_name
        )
        self._initialize_chain(
            figure_analysis_model_id, profile_name, region_name, self.boto3_session
        )

    async def parse(
        self, arxiv_id: str, extract_text: bool = True
    ) -> Tuple[List[Figure], Content]:
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

    async def _extract_figures(self, soup: Any) -> List[Figure]:
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
                    path=f"{'/'.join(self.url.split('/')[:-2])}/{img.get('src', '')}",
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
                    path=f"{'/'.join(self.url.split('/')[:-2])}/{img.get('src', '')}",
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
        boto3_session: Optional[boto3.Session] = None,
        profile_name: Optional[str] = None,
        region_name: str = "us-west-2",
        timeout: int = 60,
        api_key: Optional[str] = None,
    ):
        super().__init__(timeout=timeout)
        self.boto3_session = boto3_session or boto3.Session(
            region_name=region_name, profile_name=profile_name
        )
        self._initialize_chain(
            figure_analysis_model_id, profile_name, region_name, self.boto3_session
        )
        self.api_key = api_key or EnvVars.UPSTAGE_API_KEY.value
        if not self.api_key:
            raise ValueError(
                f"{EnvVars.UPSTAGE_API_KEY.value} must be provided or set in environment"
            )

    async def parse(
        self,
        pdf_path: Path,
        figures_dir: Optional[Path] = None,
        use_cache: bool = True,
        extract_text: bool = True,
    ) -> Tuple[List[Figure], Content]:
        try:
            response = await self._get_or_parse_document(pdf_path, use_cache)
            elements = response.get("elements", [])

            figures_dir = figures_dir or pdf_path.parent / LocalPaths.FIGURES_DIR.value
            figures_dir.mkdir(parents=True, exist_ok=True)

            figures = await self._extract_figures(elements, pdf_path, figures_dir)
            content_text = response.get("content", {}).get("html", "").strip()

            content = Content(
                text=(
                    extract_text_from_html(content_text)
                    if extract_text
                    else content_text
                )
            )
            logger.info("Successfully extracted %d figures from PDF", len(figures))
            return figures, content

        except Exception as e:
            logger.warning("Failed to parse PDF document '%s': %s", pdf_path, str(e))
            return [], Content()

    async def _get_or_parse_document(
        self, pdf_path: Path, use_cache: bool
    ) -> Dict[str, Any]:
        parsed_path = pdf_path.parent / LocalPaths.PARSED_FILE.value

        if use_cache and parsed_path.exists():
            return self._load_cached_response(parsed_path)

        response = self._request_document_parse(pdf_path)
        self._cache_response(parsed_path, response)
        return response

    @staticmethod
    def _cache_response(path: Path, response: Dict[str, Any]) -> None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(response, f, indent=2, ensure_ascii=False)
        except IOError as e:
            logger.warning("Failed to cache response: %s", str(e))

    @staticmethod
    def _load_cached_response(path: Path) -> Dict[str, Any]:
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (IOError, json.JSONDecodeError) as e:
            raise Exception(f"Failed to load cached response: {str(e)}") from e

    def _request_document_parse(self, pdf_path: Path) -> Dict[str, Any]:
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

        except (IOError, requests.RequestException) as e:
            logger.error("Failed to parse document: %s", str(e))
            raise Exception(f"Document parsing failed: {str(e)}") from e

    async def _extract_figures(
        self, elements: List[Dict[str, Any]], pdf_path: Path, figures_dir: Path
    ) -> List[Figure]:
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
                if img and hasattr(img, "get"):
                    alt_text = img.get("alt")
                    if alt_text and hasattr(alt_text, "strip"):
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
            logger.error(f"Failed to extract figures from PDF: {str(e)}")
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
            for i, (fd, path) in enumerate(zip(figure_data, paths))
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
        profile_name: Optional[str] = None,
        timeout: int = DEFAULT_TIMEOUT,
    ) -> None:
        self.boto3_session = self._create_boto3_session(
            config.resources.bedrock_region_name, profile_name
        )
        self._configure(config, timeout)
        self._init_parsers(config, profile_name)

    @staticmethod
    def _create_boto3_session(
        region_name: str, profile_name: Optional[str]
    ) -> boto3.Session:
        return boto3.Session(region_name=region_name, profile_name=profile_name)

    def _configure(self, config: Config, timeout: int) -> None:
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
        self.config = config

    def _init_parsers(self, config: Config, profile_name: Optional[str]) -> None:
        if config.summarization.figure_analysis_model_id is None:
            raise ValueError("'figure_analysis_model_id' is not set")

        figure_model_id = LanguageModelId(
            config.summarization.figure_analysis_model_id.value
        )

        self.html_parser = HTMLRichParser(
            figure_analysis_model_id=figure_model_id,
            profile_name=profile_name,
            region_name=config.resources.bedrock_region_name,
            timeout=self.timeout,
        )

        base_path = f"/{config.resources.project_name}-{config.resources.stage}"
        upstage_api_key = (
            get_ssm_param_value(
                self.boto3_session, f"{base_path}/{SSMParams.UPSTAGE_API_KEY.value}"
            )
            if is_aws_env()
            else EnvVars.UPSTAGE_API_KEY.value
        )

        self.pdf_parser = PDFParser(
            figure_analysis_model_id=figure_model_id,
            profile_name=profile_name,
            region_name=config.resources.bedrock_region_name,
            timeout=self.timeout,
            api_key=upstage_api_key,
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
    def _date_range(start_date: datetime, end_date: datetime) -> List[datetime]:
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
                executor.submit(self.process_paper, paper): paper
                for papers in filtered_papers.values()
                for paper in papers
            }

            for future in as_completed(futures):
                paper = futures[future]
                try:
                    future.result()
                except Exception as e:
                    logger.error(f"Error processing paper {paper.arxiv_id}: {str(e)}")
                    paper.status = PaperStatus.FAILED

        return filtered_papers

    def process_paper(self, paper: Paper) -> None:
        try:
            success = self._process_paper_with_html(paper)

            if not success:
                success = self._process_paper_with_pdf(paper)

            if success:
                paper.status = PaperStatus.PROCESSED
            else:
                paper.status = PaperStatus.FAILED

        except Exception as e:
            logger.error(f"Error processing paper {paper.arxiv_id}: {str(e)}")
            paper.status = PaperStatus.FAILED

    def _process_paper_with_html(self, paper: Paper) -> bool:
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
            paper.figures = [figure.model_dump() for figure in figures]

            return True
        except Exception as e:
            logger.warning(f"HTML parsing failed for {paper.arxiv_id}: {str(e)}")
            return False

    def _process_paper_with_pdf(self, paper: Paper) -> bool:
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                pdf_path = self._download_arxiv_pdf(paper.arxiv_id, Path(temp_dir))
                if not pdf_path:
                    return False

                figures_dir = Path(temp_dir) / "figures"
                figures_dir.mkdir(exist_ok=True)

                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                try:
                    figures, content = loop.run_until_complete(
                        self.pdf_parser.parse(pdf_path, figures_dir)
                    )
                finally:
                    loop.close()

                paper.content = content.text
                paper.figures = [figure.model_dump() for figure in figures]

                return True
        except Exception as e:
            logger.warning(f"PDF parsing failed for {paper.arxiv_id}: {str(e)}")
            return False

    @staticmethod
    def _download_arxiv_pdf(arxiv_id: str, temp_dir: Path) -> Optional[Path]:
        try:
            client = arxiv.Client()
            search = arxiv.Search(id_list=[arxiv_id])
            paper = next(client.results(search))

            pdf_path = temp_dir / f"{arxiv_id}.pdf"
            paper.download_pdf(dirpath=str(temp_dir), filename=f"{arxiv_id}.pdf")

            return pdf_path if pdf_path.exists() else None
        except Exception as e:
            logger.error(f"Failed to download PDF for {arxiv_id}: {str(e)}")
            return None

    @staticmethod
    def _parse_date(date_str: Optional[str]) -> Optional[datetime]:
        if not date_str:
            return None
        try:
            return datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        except ValueError:
            logger.error(f"Invalid date format: {date_str}")
            return None
