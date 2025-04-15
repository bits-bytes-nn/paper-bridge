import io
import re
import time
from pathlib import Path
from typing import List, Optional, Union
from jinja2 import Environment, FileSystemLoader, Template
from PIL import Image
from pydantic import BaseModel, Field
from selenium import webdriver
from selenium.common.exceptions import WebDriverException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from .constants import Language, LocalPaths
from .fetcher import Paper
from .logger import logger


class Result(BaseModel):
    arxiv_id: str
    summary: str
    retrieval: Optional[str] = Field(default=None)
    tags: Optional[List[str]] = Field(default=None)
    urls: Optional[List[str]] = Field(default=None)


class PaperRenderer:
    AUTHOR_NAME_MAX_LENGTH: int = 50
    MAX_AUTHORS: int = 3

    def __init__(self, templates_dir: Path):
        self.templates_dir = templates_dir
        self.env = self._create_jinja_environment()
        self.template = self._get_template(LocalPaths.TEMPLATE_FILE.value)

    def _create_jinja_environment(self) -> Environment:
        return Environment(
            loader=FileSystemLoader(self.templates_dir),
            trim_blocks=True,
            lstrip_blocks=True,
            autoescape=True,
        )

    def _get_template(self, template_name: str) -> Template:
        return self.env.get_template(template_name)

    def render(
        self,
        paper: Paper,
        result: Result,
    ) -> str:
        template_data = {
            "title": paper.title,
            "date": paper.published_at.strftime("%Y-%m-%d"),
            "authors": self._format_authors(paper),
            "summary": result.summary.replace("다:", "다."),
            "tags": result.tags,
            "urls": self._process_urls(result.urls),
            "pdf_url": str(paper.pdf_url) if paper.pdf_url else None,
        }

        if result.retrieval:
            template_data["retrieval"] = result.retrieval.replace("다:", "다.")

        return self.template.render(**template_data)

    def _format_authors(self, paper: Paper) -> str:
        all_authors = []
        if hasattr(paper, "authors") and paper.authors:
            all_authors.extend(paper.authors)

        if not all_authors:
            return "Authors information not available"

        cleaned_authors = []
        for author in all_authors:
            clean_author = re.sub(r"\(.*?\)", "", author)
            clean_author = re.sub(r"<.*?>", "", clean_author)
            clean_author = clean_author.split("@")[0].strip()
            if clean_author and len(clean_author) < self.AUTHOR_NAME_MAX_LENGTH:
                cleaned_authors.append(clean_author)

        if not cleaned_authors:
            return "Authors information not available"

        if len(cleaned_authors) <= self.MAX_AUTHORS:
            return ", ".join(cleaned_authors)
        else:
            return f"{', '.join(cleaned_authors[:self.MAX_AUTHORS])} et al."

    @staticmethod
    def _process_urls(urls: Optional[List[str]]) -> Optional[List[str]]:
        if not urls:
            return None

        processed_urls = []
        for url in urls:
            match_md_link = re.match(r"\[(.*?)]\((\bhttps?://\S+)\)", url)
            if match_md_link:
                title = match_md_link.group(1).strip()
                link = match_md_link.group(2).strip()
                title = re.sub(r"[\[\]]", "", title)
                processed_urls.append(f'<a href="{link}" target="_blank">{title}</a>')
            elif match := re.match(r"(.*?)\s*\((\bhttps?://\S+)\)", url):
                title = match.group(1).strip()
                link = match.group(2).strip()
                title = re.sub(r"[\[\]]", "", title)
                processed_urls.append(f'<a href="{link}" target="_blank">{title}</a>')
            else:
                url = re.sub(r"[\[\]]", "", url)
                processed_urls.append(url)

        return processed_urls


class PaperDocumentBuilder:
    def __init__(
        self,
        templates_dir: Path,
        outputs_dir: Path,
        stage: Optional[str] = None,
        date_suffix: Optional[str] = None,
        language: Optional[Language] = None,
    ):
        self.templates_dir = templates_dir
        self.outputs_dir = outputs_dir
        self.outputs_dir.mkdir(parents=True, exist_ok=True)

        self.renderer = PaperRenderer(templates_dir)
        self.paper_filename = None

        self.stage = stage
        self.date_suffix = date_suffix
        self.language = language

    def create_document(self, paper: Paper, result: Result) -> Path:
        try:
            paper_html = self.renderer.render(paper, result)

            filename = self._generate_filename(
                paper.arxiv_id, self.stage, self.date_suffix, self.language
            )
            output_path = self.outputs_dir / filename
            output_path.write_text(paper_html, encoding="utf-8")

            self.paper_filename = filename
            logger.info("Paper document saved to '%s'", output_path)

            return output_path

        except Exception as e:
            logger.error("Failed to create paper document: %s", str(e))
            raise

    @staticmethod
    def _generate_filename(
        arxiv_id: str,
        stage: Optional[str] = None,
        date_suffix: Optional[str] = None,
        language: Optional[Language] = None,
    ) -> str:
        components = ["paper-bridge"]

        if stage:
            components.append(stage)

        components.append(arxiv_id.replace(".", "_"))

        if date_suffix:
            components.append(date_suffix)

        if language:
            components.append(language.value)

        return f"{'-'.join(components)}.html"

    def create_batch_documents(
        self, papers: List[Paper], results: List[Result]
    ) -> List[Path]:
        output_paths = []

        for paper, result in zip(papers, results):
            try:
                output_path = self.create_document(paper, result)
                output_paths.append(output_path)
            except Exception as e:
                logger.error(
                    "Failed to create document for %s: %s", paper.arxiv_id, str(e)
                )
                continue

        return output_paths


class HtmlToImageConverter:
    DEFAULT_WIDTH = 1200
    DEFAULT_WAIT_TIME = 3
    DEFAULT_MAX_HEIGHT = 2000
    DEFAULT_OVERLAP = 0
    DEFAULT_MIN_LAST_PAGE_HEIGHT = 1000

    def __init__(
        self,
        htmls_dir: Path,
        output_dir: Optional[Path] = None,
        max_height: int = DEFAULT_MAX_HEIGHT,
        overlap: int = DEFAULT_OVERLAP,
        min_last_page_height: int = DEFAULT_MIN_LAST_PAGE_HEIGHT,
    ) -> None:
        self.htmls_dir = Path(htmls_dir)
        self.output_dir = output_dir if output_dir else self.htmls_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.max_height = max_height
        self.overlap = overlap
        self.min_last_page_height = min_last_page_height
        self.chrome_options = self._configure_chrome_options()

    @staticmethod
    def _configure_chrome_options() -> Options:
        options = Options()
        options.add_argument("--headless")
        options.add_argument("--disable-gpu")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--window-size=1200,3000")
        options.add_argument("--lang=ko-KR")
        options.add_argument("--font-render-hinting=medium")
        return options

    def convert(
        self,
        html_file: Path,
        output_file: Optional[Path] = None,
        wait_time: Optional[int] = None,
        width: Optional[int] = None,
        split_pages: bool = False,
    ) -> Optional[Union[Path, List[Path]]]:
        html_path = self.htmls_dir / html_file

        if not html_path.exists():
            raise FileNotFoundError(f"HTML file not found: {html_path}")

        output_file = output_file or Path(str(html_file).replace(".html", ".png"))
        wait_time = wait_time or self.DEFAULT_WAIT_TIME
        width = width or self.DEFAULT_WIDTH

        if split_pages:
            return self._convert_split_pages(html_file, output_file, wait_time, width)
        else:
            return self._convert_single_page(html_file, output_file, wait_time, width)

    def _create_webdriver(self) -> webdriver.Chrome:
        try:
            service = Service(ChromeDriverManager().install())
            return webdriver.Chrome(service=service, options=self.chrome_options)
        except Exception as e:
            logger.error("Failed to create WebDriver: %s", str(e))
            raise

    def _convert_single_page(
        self, html_file: Path, output_file: Path, wait_time: int, width: int
    ) -> Optional[Path]:
        html_path = self.htmls_dir / html_file
        output_path = self.output_dir / output_file
        driver = None

        try:
            driver = self._create_webdriver()
            driver.get(f"file://{html_path.absolute()}")

            time.sleep(wait_time)

            total_height = driver.execute_script("return document.body.scrollHeight")
            driver.set_window_size(width, total_height)

            time.sleep(wait_time)

            driver.save_screenshot(str(output_path))
            logger.info("Converted %s to %s", html_file, output_path)
            return output_path

        except WebDriverException as e:
            logger.error("WebDriver error converting %s: %s", html_file, str(e))
            raise
        except Exception as e:
            logger.error("Failed to convert HTML to image: %s", str(e))
            raise
        finally:
            if driver:
                driver.quit()

    def _convert_split_pages(
        self,
        html_file: Path,
        output_file: Path,
        wait_time: int,
        width: int,
    ) -> Optional[List[Path]]:
        html_path = self.htmls_dir / html_file
        output_prefix = output_file.stem
        driver = None
        image_paths = []

        try:
            driver = self._create_webdriver()
            driver.get(f"file://{html_path.absolute()}")
            time.sleep(wait_time)

            total_height = driver.execute_script(
                "return Math.max(document.body.scrollHeight, document.documentElement.scrollHeight)"
            )

            driver.set_window_size(width, self.max_height + 200)
            time.sleep(wait_time // 2)

            split_positions = self._calculate_split_positions(driver, total_height)

            num_images = len(split_positions)
            logger.info(
                "Converting HTML to %d images (total height: %dpx, width: %dpx)",
                num_images,
                total_height,
                width,
            )

            for i, scroll_top in enumerate(split_positions):
                image_path = self._capture_page_section(
                    driver,
                    scroll_top,
                    split_positions,
                    i,
                    total_height,
                    output_prefix,
                    wait_time,
                )
                image_paths.append(image_path)
                logger.info("Generated image %d/%d: %s", i + 1, num_images, image_path)

            return image_paths

        except WebDriverException as e:
            logger.error("WebDriver error converting %s: %s", html_file, str(e))
            raise
        except Exception as e:
            logger.error("Failed to convert HTML to split images: %s", str(e))
            raise
        finally:
            if driver:
                driver.quit()

    def _capture_page_section(
        self,
        driver,
        scroll_top: int,
        split_positions: List[int],
        index: int,
        total_height: int,
        output_prefix: str,
        wait_time: int,
    ) -> Path:
        driver.execute_script(f"window.scrollTo(0, {scroll_top});")
        time.sleep(wait_time // 2)

        next_pos = (
            split_positions[index + 1]
            if index < len(split_positions) - 1
            else total_height
        )
        current_height = min(next_pos - scroll_top + self.overlap, self.max_height)

        if index == len(split_positions) - 1:
            current_height = total_height - scroll_top

        screenshot = driver.get_screenshot_as_png()
        screenshot_img = Image.open(io.BytesIO(screenshot))
        cropped_img = self._crop_screenshot(
            screenshot_img, scroll_top, current_height, total_height
        )

        page_output_file = f"{output_prefix}-p{str(index+1).zfill(2)}.png"
        output_path = self.output_dir / page_output_file
        cropped_img.save(str(output_path))

        return output_path

    def _calculate_split_positions(self, driver, total_height: int) -> List[int]:
        split_positions = [0]

        potential_breakpoints = self._find_potential_breakpoints(driver)

        current_pos = 0
        for break_point in potential_breakpoints:
            if (
                current_pos + self.max_height * 0.7
                < break_point
                <= current_pos + self.max_height
            ):
                split_positions.append(break_point)
                current_pos = break_point
            elif break_point > current_pos + self.max_height:
                optimal_pos = current_pos + self.max_height
                better_pos = self._find_nearest_whitespace(driver, optimal_pos)
                split_positions.append(better_pos)
                current_pos = better_pos

        if len(split_positions) == 1 and total_height > self.max_height:
            optimal_pos = self.max_height
            whitespace_pos = self._find_whitespace_near_position(driver, optimal_pos)
            split_positions.append(whitespace_pos)

        if len(split_positions) >= 2:
            last_section_height = total_height - split_positions[-1]
            if last_section_height < self.min_last_page_height:
                split_positions.pop()

        return sorted(split_positions)

    @staticmethod
    def _find_potential_breakpoints(driver: webdriver.Chrome) -> List[int]:
        script = """
        function findPotentialBreakpoints() {
            const breakpoints = [0];
            const elements = document.querySelectorAll('p, div, h1, h2, h3, h4, h5, h6, section, article, li, blockquote');
            const scrollHeight = document.body.scrollHeight;

            // Add the position after each element
            elements.forEach(el => {
                const rect = el.getBoundingClientRect();
                const bottomPos = window.scrollY + rect.bottom;
                if (bottomPos > 0 && bottomPos < scrollHeight) {
                    breakpoints.push(Math.round(bottomPos));
                }
            });

            // Sort and remove duplicates
            return [...new Set(breakpoints)].sort((a, b) => a - b);
        }
        return findPotentialBreakpoints();
        """
        return driver.execute_script(script)

    @staticmethod
    def _find_nearest_whitespace(driver: webdriver.Chrome, optimal_pos: int) -> int:
        whitespace_script = f"""
        function findNearestWhitespace(targetY) {{
            const scanRange = 200; // Pixels to scan up and down
            const minY = Math.max(0, targetY - scanRange);
            const maxY = Math.min(document.body.scrollHeight, targetY + scanRange);

            // Create a temporary invisible element to detect text
            const detector = document.createElement('div');
            detector.style.position = 'absolute';
            detector.style.width = '1px';
            detector.style.height = '1px';
            detector.style.pointerEvents = 'none';
            detector.style.opacity = '0';
            document.body.appendChild(detector);

            let bestY = targetY;
            let bestScore = -1;

            // Scan for whitespace
            for (let y = minY; y <= maxY; y += 10) {{
                detector.style.top = y + 'px';
                const elementsAtPoint = document.elementsFromPoint(detector.offsetLeft, y);

                // Check if we're in between paragraphs or inside padding
                const inText = elementsAtPoint.some(el =>
                    ['P', 'H1', 'H2', 'H3', 'H4', 'H5', 'H6', 'LI', 'SPAN'].includes(el.tagName) &&
                    !['hidden', 'none'].includes(window.getComputedStyle(el).display)
                );

                // Calculate how good this position is
                // (lower score for text, higher for whitespace, and closer to target)
                const proximity = 1 - Math.abs(y - targetY) / scanRange;
                const score = (inText ? 0 : 1) + proximity * 0.5;

                if (score > bestScore) {{
                    bestScore = score;
                    bestY = y;
                }}
            }}

            document.body.removeChild(detector);
            return bestY;
        }}
        return findNearestWhitespace({optimal_pos});
        """
        return driver.execute_script(whitespace_script)

    @staticmethod
    def _find_whitespace_near_position(driver: webdriver.Chrome, position: int) -> int:
        script = f"""
        function findWhitespaceNearPosition(position) {{
            // Simple scan up and down from the target position
            const scanRange = 200;
            for (let offset = 0; offset <= scanRange; offset += 10) {{
                // Try below first (preferred)
                let testPos = position + offset;
                let elem = document.elementFromPoint(window.innerWidth / 2, testPos);
                if (!elem || ['BODY', 'DIV', 'SECTION'].includes(elem.tagName)) {{
                    return testPos;
                }}

                // Then try above
                testPos = position - offset;
                if (testPos <= 0) continue;
                elem = document.elementFromPoint(window.innerWidth / 2, testPos);
                if (!elem || ['BODY', 'DIV', 'SECTION'].includes(elem.tagName)) {{
                    return testPos;
                }}
            }}
            return position; // Fallback to original position
        }}
        return findWhitespaceNearPosition({position});
        """
        return driver.execute_script(script)

    @staticmethod
    def _crop_screenshot(
        screenshot: Image.Image,
        scroll_top: int,
        current_height: int,
        total_height: int,
    ) -> Image.Image:
        img_width, img_height = screenshot.size
        viewport_offset = 0

        crop_top = (
            img_height - (total_height - scroll_top)
            if scroll_top + img_height > total_height
            else viewport_offset
        )

        crop_height = min(current_height, img_height - crop_top)
        crop_box = (0, crop_top, img_width, crop_top + crop_height)

        return screenshot.crop(crop_box)

    def merge_images(
        self, image_paths: List[str], output_file: str, vertical: bool = True
    ) -> str:
        if not image_paths:
            raise ValueError("No images to merge")

        try:
            images = [Image.open(img_path) for img_path in image_paths]

            merged_img = (
                self._merge_vertically(images)
                if vertical
                else self._merge_horizontally(images)
            )

            output_path = self.output_dir / output_file
            merged_img.save(str(output_path))
            logger.info("Merged images saved to: %s", output_path)

            return str(output_path)
        except Exception as e:
            logger.error("Failed to merge images: %s", str(e))
            raise

    @staticmethod
    def _merge_vertically(images: List[Image.Image]) -> Image.Image:
        total_width = max(img.width for img in images)
        total_height = sum(img.height for img in images)
        merged_img = Image.new("RGB", (total_width, total_height))

        y_offset = 0
        for img in images:
            merged_img.paste(img, (0, y_offset))
            y_offset += img.height

        return merged_img

    @staticmethod
    def _merge_horizontally(images: List[Image.Image]) -> Image.Image:
        total_width = sum(img.width for img in images)
        total_height = max(img.height for img in images)
        merged_img = Image.new("RGB", (total_width, total_height))

        x_offset = 0
        for img in images:
            merged_img.paste(img, (x_offset, 0))
            x_offset += img.width

        return merged_img
