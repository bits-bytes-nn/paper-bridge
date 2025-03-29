import argparse
import asyncio
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from pprint import pformat
from typing import Any, Dict, List, Optional, Union
import boto3
from paper_bridge.summarizer.configs import Config
from paper_bridge.summarizer.src import (
    EnvVars,
    Figure,
    HtmlToImageConverter,
    LocalPaths,
    NULL_STRING,
    Paper,
    PaperDocumentBuilder,
    PaperFetcher,
    PaperRetriever,
    PaperSummarizer,
    Result,
    S3Paths,
    SSMParams,
    arg_as_bool,
    get_ssm_param_value,
    is_aws_env,
    logger,
    send_files_to_slack,
    upload_dir_to_s3,
    upload_to_s3,
)

ROOT_DIR: Path = Path("/tmp") if is_aws_env() else Path(__file__).parent.parent
DEFAULT_BOTO3_SESSION: boto3.Session = boto3.Session(
    region_name=EnvVars.DEFAULT_REGION_NAME.value
)
BEDROCK_BOTO3_SESSION: boto3.Session = boto3.Session(
    region_name=EnvVars.BEDROCK_REGION_NAME.value
)


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Union[int, str]]:
    target_date = None
    papers: List[Paper] = []

    try:
        config = Config.load()
        profile_name = EnvVars.AWS_PROFILE_NAME.value

        default_boto3_session = (
            DEFAULT_BOTO3_SESSION
            if is_aws_env()
            else boto3.Session(
                region_name=config.resources.default_region_name,
                profile_name=profile_name,
            )
        )
        bedrock_boto3_session = (
            BEDROCK_BOTO3_SESSION
            if is_aws_env()
            else boto3.Session(
                region_name=config.resources.bedrock_region_name,
                profile_name=profile_name,
            )
        )

        target_date = event.get("TARGET_DATE")
        days_to_fetch = event.get("DAYS_TO_FETCH")
        arxiv_ids = event.get("ARXIV_IDS")
        language = event.get("LANGUAGE")
        apply_retrieval = arg_as_bool(event.get("APPLY_RETRIEVAL", False))

        papers_dir = ROOT_DIR / LocalPaths.PAPERS_DIR.value
        target_datetime = parse_target_date(target_date)

        papers = fetch_and_enrich_papers(
            config,
            bedrock_boto3_session,
            profile_name,
            papers_dir,
            target_datetime,
            days_to_fetch,
            arxiv_ids,
        )
        logger.info("Found %d papers to process", len(papers))
        logger.debug("Paper details: %s", pformat(papers))

        if not papers:
            logger.info("No papers to process")
            return {"status": 200, "message": "No papers to process"}

        s3_input_key = _get_s3_key(config.resources.s3_prefix, S3Paths.INPUTS.value)
        upload_dir_to_s3(
            default_boto3_session,
            str(papers_dir),
            config.resources.s3_bucket_name,
            s3_input_key,
        )

        summarizer = PaperSummarizer(
            config,
            boto3_session=default_boto3_session,
            profile_name=profile_name,
            language=language,
        )
        summaries = asyncio.run(summarizer.summarize_batch(papers))

        retrievals = {}
        if apply_retrieval:
            retriever = PaperRetriever(
                config,
                boto3_session=default_boto3_session,
                profile_name=profile_name,
                language=language,
            )
            retrievals = asyncio.run(retriever.retrieve_batch(papers))

        results = process_results(summaries, retrievals, apply_retrieval)

        logger.info(
            "Successfully processed %d papers with summaries and retrievals",
            len(results),
        )
        logger.debug("Results: %s", pformat(results))

        templates_dir = ROOT_DIR / LocalPaths.TEMPLATES_DIR.value
        outputs_dir = (
            ROOT_DIR if is_aws_env() else ROOT_DIR.parent
        ) / LocalPaths.OUTPUTS_DIR.value
        outputs_dir.mkdir(parents=True, exist_ok=True)

        document_builder = PaperDocumentBuilder(
            templates_dir,
            outputs_dir,
            config.resources.stage,
            target_date,
            language,
        )
        html_paths = document_builder.create_batch_documents(papers, results)

        converter = HtmlToImageConverter(outputs_dir)
        s3_output_key = _get_s3_key(config.resources.s3_prefix, S3Paths.OUTPUTS.value)

        slack_token = _get_slack_token(default_boto3_session)
        slack_channel = _get_slack_channel(default_boto3_session)

        for html_path, paper in zip(html_paths, papers):
            upload_to_s3(
                default_boto3_session,
                html_path,
                config.resources.s3_bucket_name,
                s3_output_key,
            )

            image_paths = converter.convert(
                html_path,
                outputs_dir / f"{html_path.stem}.png",
                split_pages=True,
            )

            if image_paths:
                image_paths = (
                    image_paths if isinstance(image_paths, list) else [image_paths]
                )

                for image_path in image_paths:
                    upload_to_s3(
                        default_boto3_session,
                        image_path,
                        config.resources.s3_bucket_name,
                        config.resources.s3_prefix,
                    )

                if slack_token and slack_channel:
                    message = _create_slack_message(paper)
                    send_files_to_slack(
                        image_paths,
                        slack_token,
                        slack_channel,
                        message,
                    )

        return {"status": 200, "message": "Success"}

    except Exception as e:
        error_message = f"Failed to summarize papers: {e}"
        logger.error(error_message)

        topic_arn = EnvVars.TOPIC_ARN.value
        if is_aws_env() and topic_arn:
            send_failure_notification(
                DEFAULT_BOTO3_SESSION, topic_arn, target_date, papers, error_message
            )

        return {"status": 500, "message": error_message}


def parse_target_date(date_str: Optional[str]) -> Optional[datetime]:
    if not date_str or date_str.lower() == NULL_STRING:
        return None
    try:
        return datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError as e:
        logger.error("Invalid date format: %s", e)
        sys.exit(1)


def fetch_and_enrich_papers(
    config: Config,
    boto3_session: boto3.Session,
    profile_name: Optional[str],
    papers_dir: Path,
    target_date: Optional[datetime],
    days_to_fetch: Optional[int],
    arxiv_ids: Optional[List[str]],
) -> List[Paper]:
    fetcher = PaperFetcher(
        config,
        boto3_session=boto3_session,
        profile_name=profile_name,
    )
    if arxiv_ids:
        papers = fetcher.fetch_papers_by_arxiv_ids(
            papers_dir, arxiv_ids, config.summarization.parse_pdf
        )
    else:
        papers = fetcher.fetch_papers_for_date_range(
            papers_dir, target_date, days_to_fetch, config.summarization.parse_pdf
        )

    enriched_papers = []
    for paper in papers:
        enriched_content = _enrich_content_with_figures(paper.content, paper.figures)
        paper.content = enriched_content
        enriched_papers.append(paper)

    return enriched_papers


def _enrich_content_with_figures(text: str, figures: List[Figure]) -> str:
    if not figures:
        return text

    figures_by_id = {fig.figure_id: fig for fig in figures}
    image_pattern = r"\[Image:\s*alt=(.*?),\s*src=(.*?)\]"

    enriched_text = text
    matches = list(re.finditer(image_pattern, text))

    for i, match in enumerate(reversed(matches)):
        alt_text = match.group(1).strip()

        matched_figure = None
        figure_id_match = re.search(r"Figure\s+(\d+)", alt_text)

        if figure_id_match:
            fig_id = figure_id_match.group(1)
            if fig_id in figures_by_id:
                matched_figure = figures_by_id[fig_id]

        if matched_figure is None and i < len(figures):
            matched_figure = figures[len(figures) - i - 1]

        if matched_figure:
            replacement = f'[Image: alt={alt_text}, src={str(matched_figure.path)}, caption="{matched_figure.analysis}"]'
            start, end = match.span()
            enriched_text = enriched_text[:start] + replacement + enriched_text[end:]

    return enriched_text


def process_results(
    summaries: Dict[str, Union[Dict[str, str], str]],
    retrievals: Dict[str, Union[Dict[str, str], str]],
    apply_retrieval: bool,
) -> List[Result]:
    results = []

    for arxiv_id, summary in summaries.items():
        result = create_result_from_summary(arxiv_id, summary)

        if apply_retrieval and arxiv_id in retrievals:
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


def create_result_from_summary(
    arxiv_id: str, summary: Union[str, Dict[str, str]]
) -> Result:
    return Result(
        arxiv_id=arxiv_id,
        summary=(summary.get("summary", "") if isinstance(summary, dict) else summary),
        tags=(
            summary.get("tags", "").split(",")
            if isinstance(summary, dict) and summary.get("tags")
            else None
        ),
        urls=(
            summary.get("urls", "").split(",")
            if isinstance(summary, dict) and summary.get("urls")
            else None
        ),
    )


def extract_unique_urls(urls_str: str) -> List[str]:
    cleaned_urls = [url.strip() for url in urls_str.split(",")]
    unique_urls = []
    seen_urls = set()

    for url in cleaned_urls:
        url_match = url.rfind("](")
        if url_match != -1:
            actual_url = url[url_match + 2 : -1]
            if actual_url not in seen_urls:
                seen_urls.add(actual_url)
                unique_urls.append(url)
        elif url not in seen_urls:
            seen_urls.add(url)
            unique_urls.append(url)

    return unique_urls


def _get_s3_key(s3_prefix: Optional[str], path_value: str) -> str:
    return path_value if s3_prefix is None else f"{s3_prefix}/{path_value}"


def _get_slack_token(boto3_session: boto3.Session) -> Optional[str]:
    return (
        get_ssm_param_value(
            boto3_session,
            SSMParams.SLACK_BOT_TOKEN.value,
        )
        if is_aws_env()
        else EnvVars.SLACK_BOT_TOKEN.value
    )


def _get_slack_channel(boto3_session: boto3.Session) -> Optional[str]:
    return (
        get_ssm_param_value(
            boto3_session,
            SSMParams.SLACK_CHANNEL_ID.value,
        )
        if is_aws_env()
        else EnvVars.SLACK_CHANNEL_ID.value
    )


def _create_slack_message(paper: Paper) -> str:
    return f"'{paper.published_at.strftime('%Y-%m-%d')}'에 발행된 논문 <{paper.pdf_url}|'{paper.title}'>의 요약입니다. (득표 수: {paper.upvotes})"


def send_failure_notification(
    boto3_session: boto3.Session,
    topic_arn: str,
    target_date: Optional[datetime],
    papers: List[Paper],
    error_message: Optional[str] = None,
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


def get_formatted_date(target_date: Optional[datetime]) -> str:
    if target_date:
        return target_date.strftime("%Y-%m-%d")

    return (
        datetime.now(timezone.utc)
        .replace(hour=0, minute=0, second=0, microsecond=0)
        .astimezone(timezone.utc)
        - timedelta(days=1)
    ).strftime("%Y-%m-%d")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Paper Bridge: A service that curates and summarizes arXiv papers daily"
    )
    parser.add_argument(
        "--target-date",
        type=str,
        default=None,
        help="Target date to fetch papers",
    )
    parser.add_argument(
        "--days-to-fetch",
        type=int,
        default=None,
        help="Number of days to fetch papers",
    )
    parser.add_argument(
        "--arxiv-ids",
        type=str,
        nargs="+",
        default=None,
        help="Optional list of arXiv IDs to process",
    )
    parser.add_argument(
        "--language",
        type=str,
        default=None,
        help="Language for the newsletter",
    )
    parser.add_argument(
        "--apply-retrieval",
        type=arg_as_bool,
        default=False,
        help="Whether to apply retrieval",
    )
    args = parser.parse_args()

    target_date = (
        None
        if args.target_date and args.target_date.lower() == NULL_STRING
        else args.target_date
    )
    days_to_fetch = (
        None
        if args.days_to_fetch and str(args.days_to_fetch).lower() == NULL_STRING
        else args.days_to_fetch
    )
    language = (
        None
        if args.language and args.language.lower() == NULL_STRING
        else args.language
    )

    arxiv_ids = None
    if args.arxiv_ids is not None:
        if len(args.arxiv_ids) == 1 and args.arxiv_ids[0].lower() == NULL_STRING:
            arxiv_ids = None
        else:
            arxiv_ids = ",".join(args.arxiv_ids)

    logger.info(
        "Processing indexing with target_date='%s', days_to_fetch='%s', arxiv_ids='%s', language='%s', apply_retrieval='%s'",
        target_date or "",
        days_to_fetch or "",
        arxiv_ids or "",
        language or "",
        args.apply_retrieval,
    )

    event = {
        "TARGET_DATE": target_date,
        "DAYS_TO_FETCH": days_to_fetch,
        "ARXIV_IDS": arxiv_ids,
        "LANGUAGE": language,
        "APPLY_RETRIEVAL": args.apply_retrieval,
    }

    result = lambda_handler(event, None)
    exit_code = 0 if result["status"] == 200 else 1
    sys.exit(exit_code)
