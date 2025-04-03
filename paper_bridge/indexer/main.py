import argparse
import sys
import boto3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from pprint import pformat
from typing import List, Optional

sys.path.append(str(Path(__file__).parent.parent.parent))
from paper_bridge.indexer.configs import Config
from paper_bridge.indexer.src import (
    EnvVars,
    NULL_STRING,
    Paper,
    PaperFetcher,
    is_aws_env,
    logger,
    run_extract_and_build,
)


class DateFormatError(Exception):
    pass


class IndexingError(Exception):
    pass


def main(
    target_date: Optional[str],
    days_to_fetch: int,
    arxiv_ids: Optional[List[str]],
) -> None:
    boto3_session = None
    papers: List[Paper] = []
    success = False
    error_message = None

    try:
        config = Config.load()
        profile_name = EnvVars.AWS_PROFILE_NAME.value

        boto3_session = boto3.Session(
            region_name=config.resources.default_region_name,
            profile_name=profile_name,
        )

        target_datetime = parse_target_date(target_date)

        if arxiv_ids and isinstance(arxiv_ids, list) and len(arxiv_ids) > 0:
            arxiv_ids = [id for id in arxiv_ids if id and id.lower() != NULL_STRING]
            if not arxiv_ids:
                arxiv_ids = None

        papers = fetch_papers(
            config,
            boto3_session,
            profile_name,
            target_datetime,
            days_to_fetch,
            arxiv_ids,
        )

        if not papers:
            logger.warning("No papers found to process")
            success = True
            return

        logger.info("Found %d papers to process", len(papers))
        logger.debug("Paper details: %s", pformat(papers))

        run_extract_and_build(
            papers,
            config,
            boto3_session,
            output_dir="/tmp/output" if is_aws_env() else None,
            enable_batch_inference=config.indexing.enable_batch_inference,
        )
        success = True

    except DateFormatError as e:
        logger.error("Date format error: %s", e)
        error_message = str(e)
        success = False
        raise
    except Exception as e:
        logger.error("Failed to process papers: %s", e)
        error_message = str(e)
        success = False
        raise IndexingError(f"Failed to process papers: {e}")

    finally:
        topic_arn = EnvVars.TOPIC_ARN.value
        target_datetime = None
        try:
            target_datetime = parse_target_date(target_date)
        except DateFormatError:
            pass

        if is_aws_env() and topic_arn and not success and boto3_session:
            send_failure_notification(
                boto3_session,
                topic_arn,
                target_datetime,
                papers,
                error_message,
            )


def parse_target_date(date_str: Optional[str]) -> Optional[datetime]:
    if not date_str or date_str.lower() == NULL_STRING:
        return None
    try:
        return datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError as e:
        logger.error("Invalid date format: %s", e)
        raise DateFormatError(f"Invalid date format: {e}")


def fetch_papers(
    config: Config,
    boto3_session: boto3.Session,
    profile_name: Optional[str],
    target_datetime: Optional[datetime],
    days_to_fetch: Optional[int],
    arxiv_ids: Optional[List[str]],
) -> List[Paper]:
    fetcher = PaperFetcher(
        config, boto3_session=boto3_session, profile_name=profile_name
    )
    return (
        fetcher.fetch_papers_by_arxiv_ids(
            arxiv_ids,
            config.indexing.use_llama_parse,
        )
        if arxiv_ids
        else fetcher.fetch_papers_for_date_range(
            target_datetime,
            days_to_fetch,
            config.indexing.use_llama_parse,
        )
    )


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
        f"Paper indexing failed\n"
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
    args = parser.parse_args()

    target_date = (
        None
        if args.target_date and args.target_date.lower() == NULL_STRING
        else args.target_date
    )

    arxiv_ids = None
    if args.arxiv_ids is not None:
        if len(args.arxiv_ids) == 1 and args.arxiv_ids[0].lower() == NULL_STRING:
            arxiv_ids = None
        else:
            arxiv_ids = args.arxiv_ids

    logger.info(
        "Processing indexing with target_date='%s', days_to_fetch='%s', arxiv_ids='%s'",
        target_date or "",
        args.days_to_fetch,
        ", ".join(arxiv_ids) if arxiv_ids else "",
    )

    try:
        main(target_date, args.days_to_fetch, arxiv_ids)
    except (DateFormatError, IndexingError) as e:
        logger.error("Application failed: %s", e)
        sys.exit(1)
    except Exception as e:
        logger.error("Unexpected error: %s", e)
        sys.exit(1)
