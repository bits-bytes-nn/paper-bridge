"""Summarization pipeline orchestration.

This module holds the cohesive, individually testable building blocks that the
thin :func:`paper_bridge.summarizer.main.main` CLI orchestrator wires together:

- session construction,
- paper resolution (manual URL vs. automatic HuggingFace/arXiv fetch) plus
  figure enrichment,
- the summarize -> retrieve -> process_results core,
- output dispatch (GitHub PR vs. Slack), and
- failure notification helpers.

Behavior is intentionally identical to the previous in-line implementation in
``main.py``; this is a structural extraction only.
"""

import asyncio
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import boto3

from paper_bridge.shared import extract_unique_urls
from paper_bridge.summarizer.configs import Config
from paper_bridge.summarizer.src import (
    ArxivInputHandler,
    BaseInputHandler,
    Figure,
    Format,
    GenericPDFHandler,
    GitHubOutputHandler,
    Language,
    Paper,
    PaperFetcher,
    PaperRetriever,
    Result,
    S3Paths,
    SlackOutputHandler,
    logger,
    upload_dir_to_s3,
)
from paper_bridge.summarizer.src.summarizer import PaperSummarizer


def build_sessions(
    config: Config, profile_name: str | None = None
) -> tuple[boto3.Session, boto3.Session]:
    """Build the default and Bedrock boto3 sessions for a run.

    Returns:
        A ``(default_session, bedrock_session)`` tuple. The default session uses
        ``config.resources.default_region_name``; the Bedrock session uses
        ``config.resources.bedrock_region_name``.
    """
    default_boto3_session = boto3.Session(
        region_name=config.resources.default_region_name,
        profile_name=profile_name,
    )
    bedrock_boto3_session = boto3.Session(
        region_name=config.resources.bedrock_region_name,
        profile_name=profile_name,
    )
    return default_boto3_session, bedrock_boto3_session


def resolve_papers(
    config: Config,
    bedrock_boto3_session: boto3.Session,
    papers_dir: Path,
    profile_name: str | None = None,
    url: str | None = None,
    target_date: str | None = None,
    target_datetime: datetime | None = None,
    days_to_fetch: int | None = None,
    arxiv_ids: list[str] | None = None,
) -> list[Paper]:
    """Resolve the papers to process, branching on manual URL vs. auto mode.

    Returns the fetched (and figure-enriched) papers, or an empty list when auto
    mode is disabled and no URL is provided.
    """
    if url:
        return asyncio.run(
            fetch_paper_from_url(
                url,
                config,
                bedrock_boto3_session,
                papers_dir,
                profile_name,
            )
        )

    if config.trigger.auto_mode.enabled or arxiv_ids or target_date:
        return fetch_and_enrich_papers(
            config,
            bedrock_boto3_session,
            papers_dir,
            profile_name=profile_name,
            target_datetime=target_datetime,
            days_to_fetch=days_to_fetch,
            arxiv_ids=arxiv_ids,
        )

    logger.info("Auto mode disabled and no URL provided. Exiting.")
    return []


async def fetch_paper_from_url(
    url: str,
    config: Config,
    boto3_session: boto3.Session,
    papers_dir: Path,
    profile_name: str | None = None,
) -> list[Paper]:
    """Fetch paper from URL using appropriate handler.

    Args:
        url: URL to fetch (arXiv or generic PDF)
        config: Application config
        boto3_session: Boto3 session
        papers_dir: Directory to store downloaded files
        profile_name: AWS profile name

    Returns:
        List containing fetched Paper object
    """
    if BaseInputHandler.is_arxiv_url(url):
        logger.info("Detected arXiv URL: %s", url)
        handler = ArxivInputHandler(
            config=config,
            boto3_session=boto3_session,
            profile_name=profile_name,
        )
        paper = await handler.fetch_paper(url, papers_dir)
    else:
        logger.info("Using generic PDF handler for URL: %s", url)
        handler = GenericPDFHandler(config=config.input)
        paper = await handler.fetch_paper(url, papers_dir)
        # The generic handler downloads the PDF but does not parse it during
        # fetch; extract the text now so the summarizer has content to work with.
        parsed = await handler.parse_content(paper, papers_dir)
        paper.content = parsed.text
        paper.figures = parsed.figures

    # Enrich content if available
    if paper.content and paper.figures:
        enriched_content = _enrich_content_with_figures(paper.content, paper.figures)
        paper.content = enriched_content

    return [paper]


def fetch_and_enrich_papers(
    config: Config,
    boto3_session: boto3.Session,
    papers_dir: Path,
    profile_name: str | None = None,
    target_datetime: datetime | None = None,
    days_to_fetch: int | None = None,
    arxiv_ids: list[str] | None = None,
) -> list[Paper]:
    fetcher = PaperFetcher(
        config,
        boto3_session=boto3_session,
        profile_name=profile_name,
        timeout=600,
    )

    if arxiv_ids:
        papers = fetcher.fetch_papers_by_arxiv_ids(
            papers_dir, arxiv_ids, config.summarization.parse_pdf
        )
    else:
        papers = fetcher.fetch_papers_for_date_range(
            papers_dir, target_datetime, days_to_fetch, config.summarization.parse_pdf
        )

    enriched_papers = []
    for paper in papers:
        enriched_content = _enrich_content_with_figures(paper.content, paper.figures)
        paper.content = enriched_content
        enriched_papers.append(paper)

    return enriched_papers


def _enrich_content_with_figures(text: str, figures: list[Figure]) -> str:
    if not figures:
        return text

    figures_by_id = {fig.figure_id: fig for fig in figures}
    image_pattern = r"\[Image:\s*alt=(.*?),\s*src=(.*?)\]"

    enriched_text = text
    matches = list(re.finditer(image_pattern, text))

    # Rewrite right-to-left so earlier match spans stay valid as we splice.
    for match in reversed(matches):
        alt_text = match.group(1).strip()

        # Match strictly by the figure id parsed from the alt text. Positional
        # guessing (pairing the i-th placeholder with the i-th figure) was
        # removed: it silently mis-associated captions whenever the placeholder
        # and figure counts/order diverged. If the id can't be resolved, leave
        # the placeholder untouched rather than attach a wrong caption.
        figure_id_match = re.search(r"Figure\s+(\d+)", alt_text)
        if not figure_id_match:
            continue
        matched_figure = figures_by_id.get(figure_id_match.group(1))
        if matched_figure is None:
            continue

        replacement = f'[Image: alt={alt_text}, src={str(matched_figure.path)}, caption="{matched_figure.analysis}"]'
        start, end = match.span()
        enriched_text = enriched_text[:start] + replacement + enriched_text[end:]

    return enriched_text


def run_summarization_pipeline(
    config: Config,
    papers: list[Paper],
    default_boto3_session: boto3.Session,
    profile_name: str | None,
    language_enum: Language | None,
    output_format_enum: Format | None,
    apply_retrieval: bool,
) -> tuple[list[Result], dict[str, dict[str, str] | str]]:
    """Summarize papers, optionally retrieve, and assemble results.

    Runs the batch summarizer, then (when ``apply_retrieval`` is set) the batch
    retriever, tolerating retrieval failures by proceeding with summaries only.

    Returns:
        A ``(results, retrievals)`` tuple. ``retrievals`` is needed by the
        output dispatch step and is empty when retrieval is disabled or failed.
    """
    summarizer = PaperSummarizer(
        config,
        boto3_session=default_boto3_session,
        profile_name=profile_name,
        language=language_enum,
    )
    summaries = asyncio.run(summarizer.summarize_batch(papers))

    retrievals: dict[str, dict[str, str] | str] = {}
    try:
        if apply_retrieval:
            retriever = PaperRetriever(
                config,
                boto3_session=default_boto3_session,
                profile_name=profile_name,
                language=language_enum,
                output_format=output_format_enum,
            )
            retrievals = asyncio.run(retriever.retrieve_batch(papers))
    except Exception as e:
        logger.warning("Retrieval failed: %s. Proceeding with summaries only.", e)

    results = process_results(
        summaries,
        retrievals,
        apply_retrieval and (output_format_enum == Format.HTML),
    )
    return results, retrievals


def process_results(
    summaries: dict[str, dict[str, str] | str],
    retrievals: dict[str, dict[str, str] | str],
    add_retrievals: bool,
) -> list[Result]:
    results = []

    for arxiv_id, summary in summaries.items():
        result = create_result_from_summary(arxiv_id, summary)

        if add_retrievals and arxiv_id in retrievals:
            retrieval = retrievals[arxiv_id]
            result.retrieval = (
                retrieval.get("summary", "")
                if isinstance(retrieval, dict)
                else retrieval
            )

            if isinstance(retrieval, dict) and retrieval.get("urls"):
                result.urls = extract_unique_urls(retrieval["urls"])

        results.append(result)

    return results


def create_result_from_summary(arxiv_id: str, summary: str | dict[str, str]) -> Result:
    if isinstance(summary, dict):
        return Result(
            arxiv_id=arxiv_id,
            summary=summary.get("summary", ""),
            tags=summary.get("tags", "").split(",") if summary.get("tags") else None,
            urls=(
                extract_unique_urls(summary.get("urls", ""))
                if summary.get("urls")
                else None
            ),
        )
    else:
        return Result(
            arxiv_id=arxiv_id,
            summary=summary,
        )


def dispatch_output(
    effective_output_mode: str,
    config: Config,
    default_boto3_session: boto3.Session,
    root_dir: Path,
    templates_dir: Path,
    outputs_dir: Path,
    papers: list[Paper],
    results: list[Result],
    retrievals: dict[str, dict[str, str] | str],
    apply_retrieval: bool,
    target_date: str | None,
    language_enum: Language | None,
    send_business_slack: bool,
) -> None:
    """Dispatch results to the configured output handler (GitHub PR or Slack)."""
    if effective_output_mode == "github":
        # GitHub PR output mode
        output_handler = GitHubOutputHandler(
            config=config,
            boto3_session=default_boto3_session,
            root_dir=root_dir,
        )
        asyncio.run(
            output_handler.process(
                papers=papers,
                results=results,
                output_dir=outputs_dir,
                retrievals=retrievals if apply_retrieval else None,
            )
        )
    else:
        # Slack output mode (default)
        output_handler = SlackOutputHandler(
            config=config,
            boto3_session=default_boto3_session,
            templates_dir=templates_dir,
            target_date=target_date,
            language=language_enum,
        )
        asyncio.run(
            output_handler.process(
                papers=papers,
                results=results,
                output_dir=outputs_dir,
                retrievals=retrievals if apply_retrieval else None,
                apply_retrieval=apply_retrieval,
                send_business_slack=send_business_slack,
            )
        )


def upload_papers_dir(
    config: Config,
    default_boto3_session: boto3.Session,
    papers_dir: Path,
) -> None:
    """Upload the local papers directory to S3 under the inputs prefix."""
    s3_input_key = _get_s3_key(config.resources.s3_prefix, S3Paths.INPUTS.value)
    upload_dir_to_s3(
        default_boto3_session,
        str(papers_dir),
        config.resources.s3_bucket_name,
        s3_input_key,
    )


def _get_s3_key(s3_prefix: str | None, path_value: str) -> str:
    return f"{s3_prefix}/{path_value}" if s3_prefix else path_value


def send_failure_notification(
    boto3_session: boto3.Session,
    topic_arn: str,
    target_date: datetime | None,
    papers: list[Paper],
    error_message: str | None = None,
) -> None:
    sns = boto3_session.client("sns")
    date_str = get_formatted_date(target_date)
    paper_ids = [paper.arxiv_id for paper in papers]

    message = (
        f"Paper summarization failed\n"
        f"Date: {date_str}\n"
        f"Paper IDs: {', '.join(paper_ids)}\n"
        f"Error: {error_message or 'Unknown error'}"
    )
    sns.publish(TopicArn=topic_arn, Message=message, Subject="Paper Bridge Failure")


def get_formatted_date(target_date: datetime | None) -> str:
    if target_date:
        return target_date.strftime("%Y-%m-%d")

    return (
        datetime.now(UTC)
        .replace(hour=0, minute=0, second=0, microsecond=0)
        .astimezone(UTC)
        - timedelta(days=1)
    ).strftime("%Y-%m-%d")
