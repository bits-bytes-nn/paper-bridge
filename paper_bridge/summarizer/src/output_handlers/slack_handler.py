"""Slack output handler for HTML + Slack workflow."""

import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

import boto3

from paper_bridge.shared import convert_markdown_to_slack_links, extract_unique_urls

from ..aws_helpers import get_ssm_param_value, upload_to_s3
from ..constants import EnvVars, Format, SSMParams
from ..logger import is_aws_env, logger
from ..utils import send_files_to_slack
from .base import BaseOutputHandler

# NOTE: ``renderer`` (and its transitive ``fetcher`` import) pulls in the heavy
# ML/headless-browser stack. It is imported lazily inside the methods that
# actually render so that the pure Slack-formatting logic stays importable and
# unit-testable without those dependencies installed.

if TYPE_CHECKING:
    from ...configs.config import Config
    from ..fetcher import Paper
    from ..renderer import Result


# Slack Block Kit limits.
SLACK_HEADER_MAX_CHARS: int = 150
SLACK_SECTION_MAX_CHARS: int = 3000
SLACK_MAX_BLOCKS: int = 50


class SlackOutputHandler(BaseOutputHandler):
    """Handler for HTML rendering and Slack output."""

    def __init__(
        self,
        config: "Config",
        boto3_session: boto3.Session | None = None,
        templates_dir: Path | None = None,
        target_date: str | None = None,
        language: str | None = None,
    ):
        super().__init__(config, boto3_session)
        self.templates_dir = templates_dir
        self.target_date = target_date
        self.language = language
        self._slack_credentials: dict[str, dict[str, str | None]] | None = None

    @property
    def slack_credentials(self) -> dict[str, dict[str, str | None]]:
        """Lazy-load Slack credentials."""
        if self._slack_credentials is None:
            self._slack_credentials = {
                "personal": {
                    "token": self._get_slack_token(is_business=False),
                    "channel": self._get_slack_channel(is_business=False),
                },
                "business": {
                    "token": self._get_slack_token(is_business=True),
                    "channel": self._get_slack_channel(is_business=True),
                },
            }
        return self._slack_credentials

    async def process(
        self,
        papers: list["Paper"],
        results: list["Result"],
        output_dir: Path,
        retrievals: dict[str, dict[str, str]] | None = None,
        apply_retrieval: bool = False,
        send_business_slack: bool = False,
    ) -> None:
        """Process papers and send to Slack.

        Args:
            papers: List of Paper objects
            results: List of Result objects with summaries
            output_dir: Directory for output files
            retrievals: Optional retrieval results
            apply_retrieval: Whether to include retrieval in output
            send_business_slack: Whether to send to business Slack channel
        """
        if not self.templates_dir:
            raise ValueError("templates_dir is required for Slack output")

        # Lazy import: keeps the heavy renderer/fetcher stack out of import time.
        from ..renderer import HtmlToImageConverter, PaperDocumentBuilder

        output_dir.mkdir(parents=True, exist_ok=True)

        output_format = (
            Format(self.config.retrieval.output_format)
            if self.config.retrieval.output_format
            else None
        )

        document_builder = PaperDocumentBuilder(
            self.templates_dir,
            output_dir,
            self.config.resources.stage,
            self.target_date,
            self.language,
        )
        html_paths = document_builder.create_batch_documents(papers, results)

        converter = HtmlToImageConverter(output_dir)
        s3_output_key = self._get_s3_key(
            self.config.resources.s3_prefix,
            self.config.resources.s3_outputs_path or "outputs",
        )

        for html_path, paper in zip(html_paths, papers, strict=False):
            if self.boto3_session:
                upload_to_s3(
                    self.boto3_session,
                    html_path,
                    self.config.resources.s3_bucket_name,
                    s3_output_key,
                )

            image_paths = converter.convert(
                html_path,
                output_dir / f"{html_path.stem}.png",
                split_pages=True,
            )

            if not image_paths:
                continue

            image_paths = (
                image_paths if isinstance(image_paths, list) else [image_paths]
            )

            for image_path in image_paths:
                if self.boto3_session:
                    upload_to_s3(
                        self.boto3_session,
                        image_path,
                        self.config.resources.s3_bucket_name,
                        s3_output_key,
                    )

            retrieval = None
            if apply_retrieval and retrievals and output_format == Format.SLACK:
                retrieval = retrievals.get(paper.arxiv_id, {})

            message = self._create_slack_message(paper, retrieval)
            blocks = self._create_slack_blocks(paper, retrieval)
            logger.debug("Slack message: %s", message)

            creds = self.slack_credentials
            if creds["personal"]["token"] and creds["personal"]["channel"]:
                send_files_to_slack(
                    image_paths,
                    creds["personal"]["token"],
                    creds["personal"]["channel"],
                    message,
                    blocks=blocks,
                )

            if send_business_slack:
                if creds["business"]["token"] and creds["business"]["channel"]:
                    send_files_to_slack(
                        image_paths,
                        creds["business"]["token"],
                        creds["business"]["channel"],
                        message,
                        blocks=blocks,
                    )

    async def send_single(
        self,
        paper: "Paper",
        result: "Result",
        output_path: Path,
        retrieval: dict[str, str] | None = None,
    ) -> bool:
        """Send a single paper to Slack.

        Args:
            paper: Paper object
            result: Result object with summary
            output_path: Path to HTML output file
            retrieval: Optional retrieval result

        Returns:
            True if successful, False otherwise
        """
        try:
            if not output_path.exists():
                logger.error("Output file not found: %s", output_path)
                return False

            # Lazy import: keeps the heavy renderer/fetcher stack out of import time.
            from ..renderer import HtmlToImageConverter

            output_dir = output_path.parent
            converter = HtmlToImageConverter(output_dir)
            image_paths = converter.convert(
                output_path,
                output_dir / f"{output_path.stem}.png",
                split_pages=True,
            )

            if not image_paths:
                logger.error("Failed to convert HTML to images")
                return False

            image_paths = (
                image_paths if isinstance(image_paths, list) else [image_paths]
            )
            message = self._create_slack_message(paper, retrieval)
            blocks = self._create_slack_blocks(paper, retrieval)

            creds = self.slack_credentials
            if creds["personal"]["token"] and creds["personal"]["channel"]:
                send_files_to_slack(
                    image_paths,
                    creds["personal"]["token"],
                    creds["personal"]["channel"],
                    message,
                    blocks=blocks,
                )
                return True

            logger.warning("No Slack credentials available")
            return False

        except Exception as e:
            logger.error("Failed to send to Slack: %s", e)
            return False

    def _get_slack_token(self, is_business: bool = False) -> str | None:
        """Get Slack token from SSM or environment."""
        base_path = (
            f"/{self.config.resources.project_name}-{self.config.resources.stage}"
        )
        ssm_param = (
            SSMParams.BUSINESS_SLACK_BOT_TOKEN
            if is_business
            else SSMParams.PERSONAL_SLACK_BOT_TOKEN
        )
        env_var = (
            EnvVars.BUSINESS_SLACK_BOT_TOKEN
            if is_business
            else EnvVars.PERSONAL_SLACK_BOT_TOKEN
        )

        if is_aws_env() and self.boto3_session:
            return get_ssm_param_value(
                self.boto3_session,
                f"{base_path}/{ssm_param.value}",
            )
        return env_var.env_value

    def _get_slack_channel(self, is_business: bool = False) -> str | None:
        """Get Slack channel from SSM or environment."""
        base_path = (
            f"/{self.config.resources.project_name}-{self.config.resources.stage}"
        )
        ssm_param = (
            SSMParams.BUSINESS_SLACK_CHANNEL_ID
            if is_business
            else SSMParams.PERSONAL_SLACK_CHANNEL_ID
        )
        env_var = (
            EnvVars.BUSINESS_SLACK_CHANNEL_ID
            if is_business
            else EnvVars.PERSONAL_SLACK_CHANNEL_ID
        )

        if is_aws_env() and self.boto3_session:
            return get_ssm_param_value(
                self.boto3_session,
                f"{base_path}/{ssm_param.value}",
            )
        return env_var.env_value

    @staticmethod
    def _get_s3_key(s3_prefix: str | None, path_value: str) -> str:
        """Generate S3 key with optional prefix."""
        return f"{s3_prefix}/{path_value}" if s3_prefix else path_value

    @staticmethod
    def _create_slack_message(
        paper: "Paper", retrieval: dict[str, str] | None = None
    ) -> str:
        """Create Slack message for a paper."""
        clean_title = " ".join(paper.title.split())
        date_str = paper.published_at.strftime("%Y-%m-%d")
        message = f"🗞️ '{date_str}'에 발행된 논문 <{paper.pdf_url}|{clean_title}>의 요약입니다."

        if paper.upvotes > 0:
            message += f" (👍 +{paper.upvotes})"

        if retrieval:
            summary = "아래 메시지는 Graph RAG 기반으로 작성되었습니다.\n"
            urls = ""

            if isinstance(retrieval, dict):
                summary += retrieval.get("summary", "")
                urls = retrieval.get("urls", "")
            else:
                summary += retrieval

            formatted_summary = SlackOutputHandler._convert_markdown_to_slack_links(
                summary
            )
            message += f"\n\n{formatted_summary}"

            if urls:
                formatted_urls = []
                for url in SlackOutputHandler._extract_unique_urls(urls):
                    url = url.strip()
                    # Regex converter handles display text with parentheses;
                    # see the note in _create_slack_blocks.
                    converted = SlackOutputHandler._convert_markdown_to_slack_links(url)
                    if converted.startswith("<") and "|" in converted:
                        formatted_urls.append(f"• {converted}")

                if formatted_urls:
                    message += "\n\n📚 *참고 문헌*\n" + "\n".join(formatted_urls)

        return message

    @staticmethod
    def _truncate_header(text: str, limit: int = SLACK_HEADER_MAX_CHARS) -> str:
        """Truncate header text to Slack's plain_text limit with an ellipsis.

        Slack header blocks accept plain_text up to ``limit`` characters. If the
        text is longer, it is cut at a word boundary where possible and an
        ellipsis is appended so the total length never exceeds ``limit``.
        """
        text = " ".join(text.split())
        if len(text) <= limit:
            return text

        ellipsis = "..."
        budget = max(0, limit - len(ellipsis))
        truncated = text[:budget]
        # Prefer cutting at the last whitespace to avoid breaking mid-word.
        cut = truncated.rfind(" ")
        if cut > 0:
            truncated = truncated[:cut]
        return f"{truncated.rstrip()}{ellipsis}"

    @staticmethod
    def _split_for_section(
        text: str, limit: int = SLACK_SECTION_MAX_CHARS
    ) -> list[str]:
        """Split text into chunks that each fit a Slack section mrkdwn field.

        Splitting prefers paragraph (``\\n\\n``) then line (``\\n``) then
        sentence then whitespace boundaries so chunks never break mid-word. As
        a last resort an overly long token is hard-split at ``limit``. Returns
        an empty list for empty input and never raises.
        """
        text = text.strip()
        if not text:
            return []
        if len(text) <= limit:
            return [text]

        # Ordered separators: paragraph, line, sentence-ish, then whitespace.
        separators = ["\n\n", "\n", ". ", "다. ", " "]

        def _split(segment: str) -> list[str]:
            if len(segment) <= limit:
                return [segment] if segment else []

            for sep in separators:
                if sep not in segment:
                    continue
                parts = segment.split(sep)
                chunks: list[str] = []
                current = ""
                for i, part in enumerate(parts):
                    piece = part if i == len(parts) - 1 else part + sep
                    if not current:
                        current = piece
                    elif len(current) + len(piece) <= limit:
                        current += piece
                    else:
                        chunks.append(current)
                        current = piece
                if current:
                    chunks.append(current)

                # Recurse into any chunk still over the limit (long token).
                result: list[str] = []
                for chunk in chunks:
                    if len(chunk) > limit:
                        result.extend(_split(chunk))
                    else:
                        result.append(chunk)
                if result:
                    return result

            # No usable separator: hard-split.
            return [segment[i : i + limit] for i in range(0, len(segment), limit)]

        return [chunk.strip() for chunk in _split(text) if chunk.strip()]

    @staticmethod
    def _section_block(text: str) -> dict[str, Any]:
        """Build a single mrkdwn section block."""
        return {"type": "section", "text": {"type": "mrkdwn", "text": text}}

    # A retrieval-insight section header: a bold line led by the question emoji,
    # e.g. ``*🚀 이 논문의...?*``. Used to break the flat insight blob into the
    # per-question sections the headers already imply.
    _INSIGHT_HEADER = re.compile(r"^\s*\*\s*[🚀💎][^\n]*\*\s*$", re.MULTILINE)
    # A standalone horizontal-rule line the LLM sometimes emits between sections
    # (``---``, ``***``, ``___``). Slack renders it literally, so we drop it and
    # use real divider blocks instead.
    _HRULE_LINE = re.compile(r"^\s*([-*_])\1{2,}\s*$", re.MULTILINE)

    @classmethod
    def _split_insight_sections(cls, text: str) -> list[str]:
        """Split a Graph RAG insight body into per-question sections.

        Splits at each ``*🚀 ...*`` / ``*💎 ...*`` header (kept at the top of its
        section), strips stray horizontal-rule lines the LLM inserts, and
        collapses runs of blank lines. Falls back to a single section when no
        header is present so nothing is lost.
        """
        # Drop literal hrule separators; real dividers are added per section.
        text = cls._HRULE_LINE.sub("", text)

        # Find header positions and slice the body at each one.
        starts = [m.start() for m in cls._INSIGHT_HEADER.finditer(text)]
        if not starts:
            cleaned = cls._collapse_blank_lines(text)
            return [cleaned] if cleaned else []

        # Preserve any preamble before the first header as its own section.
        bounds = ([0] if starts[0] > 0 else []) + starts + [len(text)]
        sections: list[str] = []
        for i in range(len(bounds) - 1):
            chunk = cls._collapse_blank_lines(text[bounds[i] : bounds[i + 1]])
            if chunk:
                sections.append(chunk)
        return sections

    @staticmethod
    def _collapse_blank_lines(text: str) -> str:
        """Trim trailing space per line and collapse 3+ newlines into two."""
        lines = [line.rstrip() for line in text.splitlines()]
        collapsed = re.sub(r"\n{3,}", "\n\n", "\n".join(lines))
        return collapsed.strip()

    def _create_slack_blocks(
        self, paper: "Paper", retrieval: dict[str, str] | None = None
    ) -> list[dict[str, Any]]:
        """Build a Slack Block Kit ``blocks`` array for a paper.

        Layout: header -> context (date, upvotes) -> link section -> divider
        -> Graph RAG insight section(s) -> divider -> references section(s).

        All Slack limits are respected defensively: the header is truncated to
        150 chars, section mrkdwn is split into <=3000-char blocks, and the
        total is capped at 50 blocks.
        """
        clean_title = " ".join(paper.title.split())
        date_str = paper.published_at.strftime("%Y-%m-%d")

        blocks: list[dict[str, Any]] = []

        # Header: emoji + (possibly truncated) title.
        blocks.append(
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": self._truncate_header(f"🗞️ {clean_title}"),
                    "emoji": True,
                },
            }
        )

        # Context: publish date, upvotes, and the paper link on one compact row.
        context_elements: list[dict[str, Any]] = [
            {"type": "mrkdwn", "text": f"📅 {date_str}"}
        ]
        if paper.upvotes > 0:
            context_elements.append({"type": "mrkdwn", "text": f"👍 +{paper.upvotes}"})
        context_elements.append(
            {"type": "mrkdwn", "text": f"<{paper.pdf_url}|📄 논문 보기>"}
        )
        blocks.append({"type": "context", "elements": context_elements})

        blocks.append({"type": "divider"})

        # Graph RAG insight section(s).
        if retrieval:
            if isinstance(retrieval, dict):
                summary = retrieval.get("summary", "")
                urls = retrieval.get("urls", "")
            else:
                summary = str(retrieval)
                urls = ""

            summary = (summary or "").strip()
            if summary:
                formatted_summary = self._convert_markdown_to_slack_links(summary)
                # Title for the whole insight area.
                blocks.append(self._section_block("🔗 *Graph RAG 인사이트*"))
                # Split the body on its `*🚀 ...*` / `*💎 ...*` section headers so
                # each question gets its own divider + block — the LLM emits one
                # flat blob (with stray `---` separators), which Slack renders as
                # an undifferentiated wall of text. Per-section blocks restore the
                # visual hierarchy the headers imply.
                for section in self._split_insight_sections(formatted_summary):
                    blocks.append({"type": "divider"})
                    for chunk in self._split_for_section(section):
                        blocks.append(self._section_block(chunk))

            # References section(s).
            if urls:
                formatted_urls: list[str] = []
                for url in self._extract_unique_urls(urls):
                    url = url.strip()
                    # Use the shared regex converter rather than naive find():
                    # display text often contains parentheses (e.g.
                    # "[QuaRot (Hadamard rotation)](url)"), and find(")") would
                    # cut at the FIRST ")" inside the text, mangling the link.
                    converted = self._convert_markdown_to_slack_links(url)
                    if converted.startswith("<") and "|" in converted:
                        formatted_urls.append(f"• {converted}")

                if formatted_urls:
                    blocks.append({"type": "divider"})
                    blocks.append(self._section_block("📚 *참고 문헌*"))
                    for chunk in self._split_for_section("\n".join(formatted_urls)):
                        blocks.append(self._section_block(chunk))

        # Cap at Slack's per-message block limit.
        return blocks[:SLACK_MAX_BLOCKS]

    # Thin wrappers over the shared, tested implementations. Kept as methods so
    # existing call sites (and tests) remain stable while the logic lives once
    # in ``paper_bridge.shared.text_utils``.
    _convert_markdown_to_slack_links = staticmethod(convert_markdown_to_slack_links)
    _extract_unique_urls = staticmethod(extract_unique_urls)
