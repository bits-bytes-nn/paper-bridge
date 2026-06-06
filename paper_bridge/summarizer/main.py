import argparse
import sys
from datetime import datetime
from pathlib import Path
from pprint import pformat

import boto3

from paper_bridge.summarizer.configs import Config
from paper_bridge.summarizer.src import (
    NULL_STRING,
    EnvVars,
    Format,
    Language,
    LocalPaths,
    Paper,
    arg_as_bool,
    is_aws_env,
)

# Import the logger INSTANCE from its module explicitly. Going through the
# package's lazy __getattr__ ("from ...src import logger") is ambiguous: once any
# other import registers the ``logger`` submodule as an attribute of the package,
# a later "from ...src import logger" resolves to the submodule object (which has
# no .info) instead of the Logger instance — import order in the container vs.
# locally then decides whether it works. The explicit path is unambiguous.
from paper_bridge.summarizer.src.logger import logger
from paper_bridge.summarizer.src.pipeline import (
    build_sessions,
    dispatch_output,
    resolve_papers,
    run_summarization_pipeline,
    send_failure_notification,
    upload_papers_dir,
)

ROOT_DIR: Path = Path("/tmp") if is_aws_env() else Path(__file__).parent.parent
UPLOAD_PAPERS_DIR: bool = False


class DateFormatError(Exception):
    pass


class SummarizationError(Exception):
    pass


def parse_target_date(date_str: str | None) -> datetime | None:
    if not date_str or date_str.lower() == NULL_STRING:
        return None
    try:
        return datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError as e:
        logger.error("Invalid date format: %s", e)
        raise DateFormatError(f"Invalid date format: {e}") from e


def main(
    target_date: str | None,
    days_to_fetch: int,
    arxiv_ids: list[str] | None,
    language: str | None,
    apply_retrieval: bool,
    send_business_slack: bool,
    url: str | None = None,
    output_mode: str | None = None,
) -> None:
    default_boto3_session: boto3.Session | None = None
    papers: list[Paper] = []
    success = False
    error_message = None

    try:
        config = Config.load()
        profile_name = EnvVars.AWS_PROFILE_NAME.env_value

        default_boto3_session, bedrock_boto3_session = build_sessions(
            config, profile_name
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
        papers_dir.mkdir(parents=True, exist_ok=True)

        # Determine effective output mode
        effective_output_mode = output_mode or config.output.mode

        papers = resolve_papers(
            config,
            bedrock_boto3_session,
            papers_dir,
            profile_name=profile_name,
            url=url,
            target_date=target_date,
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
            upload_papers_dir(config, default_boto3_session, papers_dir)

        results, retrievals = run_summarization_pipeline(
            config,
            papers,
            default_boto3_session,
            profile_name,
            language_enum,
            output_format_enum,
            apply_retrieval,
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

        dispatch_output(
            effective_output_mode,
            config,
            default_boto3_session,
            ROOT_DIR,
            templates_dir,
            outputs_dir,
            papers,
            results,
            retrievals,
            apply_retrieval,
            target_date,
            language_enum,
            send_business_slack,
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
        raise SummarizationError(f"Failed to summarize papers: {e}") from e

    finally:
        _notify_failure_if_needed(
            default_boto3_session, target_date, papers, success, error_message
        )


def _notify_failure_if_needed(
    default_boto3_session: boto3.Session | None,
    target_date: str | None,
    papers: list[Paper],
    success: bool,
    error_message: str | None,
) -> None:
    """Publish an SNS failure notification when running in AWS and the run failed."""
    topic_arn = EnvVars.TOPIC_ARN.env_value
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


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Paper Bridge: A service that curates and summarizes arXiv papers daily"
    )
    parser.add_argument(
        "--target-date",
        type=str,
        default=None,
        help="Target date to fetch papers (YYYY-MM-DD)",
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
    parser.add_argument(
        "--url",
        type=str,
        default=None,
        help="PDF URL to process (manual trigger mode)",
    )
    parser.add_argument(
        "--output-mode",
        type=str,
        choices=["slack", "github"],
        default=None,
        help="Output mode: 'slack' (HTML + Slack) or 'github' (Markdown + PR)",
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
    url = None if args.url and args.url.lower() == NULL_STRING else args.url

    arxiv_ids = None
    if args.arxiv_ids is not None:
        if len(args.arxiv_ids) == 1 and args.arxiv_ids[0].lower() == NULL_STRING:
            arxiv_ids = None
        else:
            arxiv_ids = args.arxiv_ids

    logger.info(
        "Processing papers with target_date='%s', days_to_fetch='%s', arxiv_ids='%s', "
        "language='%s', apply_retrieval='%s', send_business_slack='%s', url='%s', output_mode='%s'",
        target_date or "",
        args.days_to_fetch,
        ", ".join(arxiv_ids) if arxiv_ids else "",
        language or "",
        args.apply_retrieval,
        args.send_business_slack,
        url or "",
        args.output_mode or "",
    )

    try:
        main(
            target_date,
            args.days_to_fetch,
            arxiv_ids,
            language,
            args.apply_retrieval,
            args.send_business_slack,
            url,
            args.output_mode,
        )
    except (DateFormatError, SummarizationError) as e:
        logger.error("Application failed: %s", e)
        sys.exit(1)
    except Exception as e:
        logger.error("Unexpected error: %s", e)
        sys.exit(1)
