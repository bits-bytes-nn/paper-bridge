import argparse
import asyncio
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from pprint import pformat
from typing import Dict, List, Optional, Union
import boto3
from paper_bridge.summarizer.configs import Config
from paper_bridge.summarizer.src import (
    EnvVars,
    Format,
    Figure,
    HtmlToImageConverter,
    Language,
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
UPLOAD_PAPERS_DIR: bool = False


class DateFormatError(Exception):
    pass


class SummarizationError(Exception):
    pass


def parse_target_date(date_str: Optional[str]) -> Optional[datetime]:
    if not date_str or date_str.lower() == NULL_STRING:
        return None
    try:
        return datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError as e:
        logger.error("Invalid date format: %s", e)
        raise DateFormatError(f"Invalid date format: {e}")


def main(
    target_date: Optional[str],
    days_to_fetch: int,
    arxiv_ids: Optional[List[str]],
    language: Optional[str],
    apply_retrieval: bool,
    send_business_slack: bool,
) -> None:
    default_boto3_session = None
    papers: List[Paper] = []
    success = False
    error_message = None

    try:
        config = Config.load()
        profile_name = EnvVars.AWS_PROFILE_NAME.value

        default_boto3_session = boto3.Session(
            region_name=config.resources.default_region_name,
            profile_name=profile_name,
        )

        bedrock_boto3_session = boto3.Session(
            region_name=config.resources.bedrock_region_name,
            profile_name=profile_name,
        )

        target_datetime = parse_target_date(target_date)

        if arxiv_ids:
            arxiv_ids = [
                arxiv_id
                for arxiv_id in arxiv_ids
                if arxiv_id and arxiv_id.lower() != NULL_STRING
            ]
            if not arxiv_ids:
                arxiv_ids = None

        language_enum = None
        if language and language.lower() != NULL_STRING:
            language_enum = Language(language)

        output_format_enum = (
            Format(config.retrieval.output_format)
            if config.retrieval.output_format
            else None
        )

        papers_dir = ROOT_DIR / LocalPaths.PAPERS_DIR.value
        papers = fetch_and_enrich_papers(
            config,
            bedrock_boto3_session,
            papers_dir,
            profile_name=profile_name,
            target_datetime=target_datetime,
            days_to_fetch=days_to_fetch,
            arxiv_ids=arxiv_ids,
        )
        logger.info("Found %d papers to process", len(papers))
        logger.debug("Paper details: %s", pformat(papers))

        if not papers:
            logger.info("No papers to process")
            success = True
            return

        if UPLOAD_PAPERS_DIR or is_aws_env():
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
            language=language_enum,
        )
        summaries = asyncio.run(summarizer.summarize_batch(papers))

        retrievals = {}
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

        logger.info(
            "Successfully processed %d papers with summaries and retrievals",
            len(results),
        )
        logger.debug("Results: %s", pformat(results))

        templates_dir = (
            Path(__file__).parent / "paper_bridge" if is_aws_env() else ROOT_DIR
        ) / LocalPaths.TEMPLATES_DIR.value
        outputs_dir = (
            ROOT_DIR if is_aws_env() else ROOT_DIR.parent
        ) / LocalPaths.OUTPUTS_DIR.value
        outputs_dir.mkdir(parents=True, exist_ok=True)

        document_builder = PaperDocumentBuilder(
            templates_dir,
            outputs_dir,
            config.resources.stage,
            target_date,
            language_enum,
        )
        html_paths = document_builder.create_batch_documents(papers, results)

        converter = HtmlToImageConverter(outputs_dir)
        s3_output_key = _get_s3_key(config.resources.s3_prefix, S3Paths.OUTPUTS.value)

        slack_credentials = {
            "personal": {
                "token": _get_slack_token(
                    config, default_boto3_session, is_business=False
                ),
                "channel": _get_slack_channel(
                    config, default_boto3_session, is_business=False
                ),
            },
            "business": {
                "token": _get_slack_token(
                    config, default_boto3_session, is_business=True
                ),
                "channel": _get_slack_channel(
                    config, default_boto3_session, is_business=True
                ),
            },
        }

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

            if not image_paths:
                continue

            image_paths = (
                image_paths if isinstance(image_paths, list) else [image_paths]
            )

            for image_path in image_paths:
                upload_to_s3(
                    default_boto3_session,
                    image_path,
                    config.resources.s3_bucket_name,
                    s3_output_key,
                )

            retrieval = None
            if apply_retrieval and (output_format_enum == Format.SLACK):
                retrieval = retrievals.get(paper.arxiv_id, {})

            message = _create_slack_message(paper, retrieval)
            logger.debug("Slack message: %s", message)

            if (
                slack_credentials["personal"]["token"]
                and slack_credentials["personal"]["channel"]
            ):
                send_files_to_slack(
                    image_paths,
                    slack_credentials["personal"]["token"],
                    slack_credentials["personal"]["channel"],
                    message,
                )

            if (
                send_business_slack
                and slack_credentials["business"]["token"]
                and slack_credentials["business"]["channel"]
            ):
                send_files_to_slack(
                    image_paths,
                    slack_credentials["business"]["token"],
                    slack_credentials["business"]["channel"],
                    message,
                )

        success = True

    except DateFormatError as e:
        logger.error("Date format error: %s", e)
        error_message = str(e)
        success = False
        raise
    except Exception as e:
        logger.error("Failed to summarize papers: %s", e)
        error_message = str(e)
        success = False
        raise SummarizationError(f"Failed to summarize papers: {e}")

    finally:
        topic_arn = EnvVars.TOPIC_ARN.value
        target_datetime = None
        try:
            target_datetime = parse_target_date(target_date)
        except DateFormatError:
            pass

        if is_aws_env() and topic_arn and not success and default_boto3_session:
            send_failure_notification(
                default_boto3_session,
                topic_arn,
                target_datetime,
                papers,
                error_message,
            )


def fetch_and_enrich_papers(
    config: Config,
    boto3_session: boto3.Session,
    papers_dir: Path,
    profile_name: Optional[str] = None,
    target_datetime: Optional[datetime] = None,
    days_to_fetch: Optional[int] = None,
    arxiv_ids: Optional[List[str]] = None,
) -> List[Paper]:
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
            matched_figure = figures_by_id.get(fig_id)

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
    add_retrievals: bool,
) -> List[Result]:
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


def create_result_from_summary(
    arxiv_id: str, summary: Union[str, Dict[str, str]]
) -> Result:
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


def extract_unique_urls(urls_str: str) -> List[str]:
    if not urls_str or not urls_str.strip():
        return []

    cleaned_urls = [url.strip() for url in urls_str.split(",") if url.strip()]
    unique_urls = []
    seen_urls = set()

    for url in cleaned_urls:
        url_match = url.rfind("](")
        if url_match != -1:
            actual_url = url[url_match + 2 : -1]
            if actual_url and actual_url not in seen_urls:
                seen_urls.add(actual_url)
                unique_urls.append(url)
        elif url and url not in seen_urls:
            seen_urls.add(url)
            unique_urls.append(url)

    return unique_urls


def _get_s3_key(s3_prefix: Optional[str], path_value: str) -> str:
    return f"{s3_prefix}/{path_value}" if s3_prefix else path_value


def _get_slack_token(
    config: Config, boto3_session: boto3.Session, is_business: bool = False
) -> Optional[str]:
    base_path = f"/{config.resources.project_name}-{config.resources.stage}"
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

    if is_aws_env():
        return get_ssm_param_value(
            boto3_session,
            f"{base_path}/{ssm_param.value}",
        )
    return env_var.value


def _get_slack_channel(
    config: Config, boto3_session: boto3.Session, is_business: bool = False
) -> Optional[str]:
    base_path = f"/{config.resources.project_name}-{config.resources.stage}"
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

    if is_aws_env():
        return get_ssm_param_value(
            boto3_session,
            f"{base_path}/{ssm_param.value}",
        )
    return env_var.value


def _create_slack_message(
    paper: Paper, retrieval: Optional[Union[str, Dict[str, str]]] = None
) -> str:
    clean_title = " ".join(paper.title.split())
    date_str = paper.published_at.strftime("%Y-%m-%d")
    message = (
        f"🗞️ '{date_str}'에 발행된 논문 <{paper.pdf_url}|{clean_title}>의 요약입니다."
    )

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

        formatted_summary = convert_markdown_to_slack_links(
            summary.replace("다:", "다.")
        )
        message += f"\n\n{formatted_summary}"

        if urls:
            formatted_urls = []
            for url in extract_unique_urls(urls):
                url = url.strip()
                if "[" in url and "](" in url:
                    text = url[url.find("[") + 1 : url.find("]")]
                    link = url[url.find("](") + 2 : url.find(")")]
                    formatted_urls.append(f"• <{link}|{text}>")

            if formatted_urls:
                message += "\n\n📚 *참고 문헌*\n" + "\n".join(formatted_urls)

    return message


def convert_markdown_to_slack_links(text: str) -> str:
    pattern = re.compile(r"\[([^]]+)]\(([^)]+)\)")
    return pattern.sub(r"<\2|\1>", text)


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
        default=0,
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
    parser.add_argument(
        "--send-business-slack",
        type=arg_as_bool,
        default=False,
        help="Whether to send business slack",
    )
    args = parser.parse_args()

    target_date = (
        None
        if args.target_date and args.target_date.lower() == NULL_STRING
        else args.target_date
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
            arxiv_ids = args.arxiv_ids

    logger.info(
        "Processing papers with target_date='%s', days_to_fetch='%s', arxiv_ids='%s', language='%s', apply_retrieval='%s', send_business_slack='%s'",
        target_date or "",
        args.days_to_fetch,
        ", ".join(arxiv_ids) if arxiv_ids else "",
        language or "",
        args.apply_retrieval,
        args.send_business_slack,
    )

    try:
        main(
            target_date,
            args.days_to_fetch,
            arxiv_ids,
            language,
            args.apply_retrieval,
            args.send_business_slack,
        )
    except (DateFormatError, SummarizationError) as e:
        logger.error("Application failed: %s", e)
        sys.exit(1)
    except Exception as e:
        logger.error("Unexpected error: %s", e)
        sys.exit(1)
