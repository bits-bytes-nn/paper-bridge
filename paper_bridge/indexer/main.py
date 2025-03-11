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
    Paper,
    PaperFetcher,
    arg_as_bool,
    is_aws_env,
    logger,
    run_extract_and_build,
)


def main() -> None:
    args = parse_arguments()
    success = False
    target_date = None
    boto3_session = None
    flattened_papers: List[Paper] = []

    try:
        config = Config.load()
        profile_name = EnvVars.AWS_PROFILE_NAME.value

        target_date = parse_target_date(args.target_date)
        boto3_session = boto3.Session(
            region_name=config.resources.default_region_name,
            profile_name=profile_name,
        )

        fetcher = PaperFetcher(config)
        papers = fetcher.fetch_papers_for_date_range(
            target_date,
            args.days_to_fetch,
            config.indexing.use_llama_parse,
        )
        flattened_papers = [
            paper for papers_list in papers.values() for paper in papers_list
        ]

        logger.info(f"Found {len(flattened_papers)} papers to process")
        logger.debug("Paper details: %s", pformat(papers))

        if not flattened_papers:
            logger.warning("No papers found to process")
            success = True
            return

        run_extract_and_build(
            flattened_papers,
            config,
            profile_name=profile_name,
            output_dir=get_output_directory(),
            enable_batch_inference=args.enable_batch_inference,
        )
        success = True

    except Exception as e:
        logger.error(f"Failed to process papers: {e}")
        success = False
        sys.exit(1)

    finally:
        topic_arn = EnvVars.TOPIC_ARN.value
        if is_aws_env() and topic_arn and not success and boto3_session:
            send_failure_notification(
                boto3_session,
                topic_arn,
                target_date,
                flattened_papers,
            )


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Paper Bridge: A service that curates and summarizes arXiv papers daily"
    )
    parser.add_argument(
        "--target-date",
        type=str,
        help="Target date to fetch papers in 'YYYY-MM-DD' format",
        default=None,
    )
    parser.add_argument(
        "--days-to-fetch",
        type=int,
        help="Number of days to fetch papers",
        default=None,
    )
    parser.add_argument(
        "--enable-batch-inference",
        type=arg_as_bool,
        help="Enable batch inference",
        default=False,
    )
    return parser.parse_args()


def parse_target_date(date_str: Optional[str]) -> Optional[datetime]:
    if not date_str or date_str.lower() == "none":
        return None
    try:
        return datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError as e:
        logger.error(f"Invalid date format: {e}")
        sys.exit(1)


def get_output_directory() -> Optional[str]:
    return "/tmp/output" if is_aws_env() else None


def send_failure_notification(
    boto3_session: boto3.Session,
    topic_arn: str,
    target_date: Optional[datetime],
    papers: List[Paper],
) -> None:
    sns = boto3_session.client("sns")
    date_str = (
        target_date.strftime("%Y-%m-%d")
        if target_date
        else (
            datetime.now(timezone.utc)
            .replace(hour=0, minute=0, second=0, microsecond=0)
            .astimezone(timezone.utc)
            - timedelta(days=1)
        ).strftime("%Y-%m-%d")
    )
    paper_ids = [paper.arxiv_id for paper in papers]

    message = (
        f"Paper indexing failed\n"
        f"Date: {date_str}\n"
        f"Paper IDs: {', '.join(paper_ids)}"
    )
    sns.publish(TopicArn=topic_arn, Message=message, Subject="Paper Bridge Failure")


if __name__ == "__main__":
    main()
