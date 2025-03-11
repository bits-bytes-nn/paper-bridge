import argparse
import sys
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Union
import boto3
from paper_bridge.summarizer.configs import Config
from paper_bridge.summarizer.src import (
    EnvVars,
    PaperFetcher,
    get_ssm_param_value,
    logger,
)


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Union[int, str]]:
    boto3_session = None
    target_date = None

    try:
        config = Config.load()
        profile_name = EnvVars.AWS_PROFILE_NAME.value

        target_date_str = event.get("TARGET_DATE")

        target_date = parse_target_date(target_date_str)

        boto3_session = boto3.Session(
            region_name=config.resources.default_region_name,
            profile_name=profile_name,
        )

        project_name = config.resources.project_name
        stage = config.resources.stage

        neptune_endpoint = get_ssm_param_value(
            boto3_session, f"/{project_name}-{stage}/neptune/endpoint"
        )
        opensearch_endpoint = get_ssm_param_value(
            boto3_session, f"/{project_name}-{stage}/opensearch/endpoint"
        )

        if neptune_endpoint is None or opensearch_endpoint is None:
            raise ValueError(
                "Neptune or OpenSearch endpoint not found in SSM parameters"
            )

        fetcher = PaperFetcher(config)
        papers = fetcher.fetch_papers_for_date_range(
            target_date,
            config.summarization.days_to_fetch,
        )
        flattened_papers = [
            paper for papers_list in papers.values() for paper in papers_list
        ]

        logger.info(f"Found {len(flattened_papers)} papers to process")

        return {"status": 200, "message": "Success"}

    except Exception as e:
        logger.error(f"Error: {e}")
        return {"status": 500, "message": str(e)}


def parse_target_date(date_str: Optional[str]) -> datetime:
    if not date_str:
        return datetime.now(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0
        ).astimezone(timezone.utc) - timedelta(days=1)

    try:
        return datetime.strptime(date_str, "%Y-%m-%d").astimezone(timezone.utc)
    except ValueError as e:
        logger.error(f"Invalid date format: {e}")
        sys.exit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Paper Bridge: A service that curates and summarizes arXiv papers daily"
    )
    parser.add_argument(
        "--target-date",
        type=str,
        help="Target date in 'YYYY-MM-DD' format",
        default=None,
    )

    args = parser.parse_args()
    event = {"TARGET_DATE": args.target_date}

    result = lambda_handler(event, None)
    exit_code = 0 if result["status"] == 200 else 1
    sys.exit(exit_code)
