import argparse
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union
import boto3

sys.path.append(str(Path(__file__).parent.parent.parent))
from paper_bridge.cleaner.configs import Config
from paper_bridge.cleaner.src import Cleaner, EnvVars, get_ssm_param_value, logger


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Union[int, str]]:
    boto3_session = None
    target_date = None

    try:
        config = Config.load()
        profile_name = EnvVars.AWS_PROFILE_NAME.value

        target_date_str = event.get("TARGET_DATE")
        days_back = event.get("DAYS_BACK")
        days_range = event.get("DAYS_RANGE")

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

        start_date, end_date = calculate_date_range(
            target_date, days_back, days_range, config
        )

        logger.info(f"Deleting documents from {start_date} to {end_date}")

        opensearch_indexes = getattr(
            config.cleaner, "opensearch_indexes", ["chunk", "statement"]
        )

        cleaner = Cleaner(
            neptune_endpoint=neptune_endpoint,
            opensearch_endpoint=opensearch_endpoint,
            opensearch_indexes=opensearch_indexes,
        )

        deletion_result = cleaner.delete_documents_by_date_range(
            start_date=start_date, end_date=end_date
        )

        logger.info(f"Deletion result: {deletion_result}")
        result = {"status": 200, "message": "Success", "result": deletion_result}
        return result

    except Exception as e:
        error_message = f"Failed to clean documents: {e}"
        logger.error(error_message)

        topic_arn = EnvVars.TOPIC_ARN.value
        if is_aws_env() and topic_arn and boto3_session:
            send_failure_notification(boto3_session, topic_arn, target_date)

        result = {"status": 500, "message": error_message}
        return result


def parse_target_date(date_str: Optional[str]) -> datetime:
    if not date_str:
        return datetime.now(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0
        ).astimezone(timezone.utc) - timedelta(days=1)

    try:
        return datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError as e:
        logger.error(f"Invalid date format: {e}")
        sys.exit(1)


def calculate_date_range(
    target_date: datetime,
    days_back: Optional[int],
    days_range: Optional[int],
    config: Config,
) -> Tuple[str, str]:
    days_back = days_back if days_back is not None else config.cleaner.days_back
    days_range = days_range if days_range is not None else config.cleaner.days_range

    end_date = target_date - timedelta(days=days_back)
    start_date = end_date - timedelta(days=days_range)

    return start_date.strftime("%Y-%m-%d"), end_date.strftime("%Y-%m-%d")


def is_aws_env() -> bool:
    aws_env_vars: List[str] = [
        "AWS_BATCH_JOB_ID",
        "AWS_LAMBDA_FUNCTION_NAME",
        "ECS_CONTAINER_METADATA_URI",
    ]
    return any(env_var in os.environ for env_var in aws_env_vars)


def send_failure_notification(
    boto3_session: boto3.Session, topic_arn: str, target_date: Optional[datetime]
) -> None:
    sns = boto3_session.client("sns")

    date_str = (
        target_date.strftime("%Y-%m-%d")
        if target_date
        else (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    )

    message = f"Paper cleaning failed\n" f"Date: {date_str}\n"

    sns.publish(TopicArn=topic_arn, Message=message, Subject="Paper Bridge Failure")


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
    parser.add_argument(
        "--days-back",
        type=int,
        help="Number of days to go back from target date",
        default=None,
    )
    parser.add_argument(
        "--days-range",
        type=int,
        help="Number of days range to delete",
        default=None,
    )

    args = parser.parse_args()
    event = {
        "TARGET_DATE": args.target_date,
        "DAYS_BACK": args.days_back,
        "DAYS_RANGE": args.days_range,
    }

    result = lambda_handler(event, None)
    exit_code = 0 if result["status"] == 200 else 1
    sys.exit(exit_code)
