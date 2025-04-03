import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple, Union
import boto3

sys.path.append(str(Path(__file__).parent.parent.parent))
from paper_bridge.cleaner.configs import Config
from paper_bridge.cleaner.src import (
    Cleaner,
    EnvVars,
    NULL_STRING,
    SSMParams,
    get_ssm_param_value,
    is_aws_env,
    logger,
)

DEFAULT_BOTO3_SESSION: boto3.Session = boto3.Session(
    region_name=EnvVars.DEFAULT_REGION_NAME.value,
)


class DateFormatError(Exception):
    pass


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Union[int, str]]:
    target_date = None

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

        target_date_str = event.get("TARGET_DATE")
        if isinstance(target_date_str, str) and target_date_str.lower() == NULL_STRING:
            target_date_str = None

        days_back = event.get("DAYS_BACK")
        if isinstance(days_back, str):
            try:
                days_back = int(days_back) if days_back.lower() != NULL_STRING else None
            except ValueError:
                logger.warning("Invalid days_back value: %s. Using default.", days_back)
                days_back = None

        days_range = event.get("DAYS_RANGE")
        if isinstance(days_range, str):
            try:
                days_range = (
                    int(days_range) if days_range.lower() != NULL_STRING else None
                )
            except ValueError:
                logger.warning(
                    "Invalid days_range value: %s. Using default.", days_range
                )
                days_range = None

        try:
            target_datetime = parse_target_date(target_date_str)
        except DateFormatError as e:
            return {"status": 400, "message": str(e)}

        base_path = f"/{config.resources.project_name}-{config.resources.stage}"
        neptune_endpoint = get_ssm_param_value(
            default_boto3_session, f"{base_path}/{SSMParams.NEPTUNE_ENDPOINT.value}"
        )
        opensearch_endpoint = get_ssm_param_value(
            default_boto3_session, f"{base_path}/{SSMParams.OPENSEARCH_ENDPOINT.value}"
        )

        if neptune_endpoint is None or opensearch_endpoint is None:
            raise ValueError(
                "Neptune or OpenSearch endpoint not found in SSM parameters"
            )

        start_date, end_date = parse_date_range(
            config, target_datetime, days_back, days_range
        )

        logger.info("Deleting documents from '%s' to '%s'", start_date, end_date)

        opensearch_indexes = getattr(
            config.cleaner, "opensearch_indexes", ["chunk", "statement"]
        )

        cleaner = Cleaner(
            default_boto3_session,
            neptune_endpoint,
            opensearch_endpoint,
            opensearch_indexes,
            region_name=config.resources.default_region_name,
        )

        deletion_result = cleaner.delete_documents_by_date_range(
            start_date=start_date, end_date=end_date
        )

        logger.info("Deletion result: %s", deletion_result)
        return {"status": 200, "message": "Success"}

    except Exception as e:
        error_message = f"Failed to clean documents: {e}"
        logger.error(error_message)

        topic_arn = EnvVars.TOPIC_ARN.value
        if is_aws_env() and topic_arn:
            send_failure_notification(
                DEFAULT_BOTO3_SESSION, topic_arn, target_date, error_message
            )
        return {"status": 500, "message": error_message}


def parse_target_date(date_str: Optional[str]) -> datetime:
    if not date_str or date_str.lower() == NULL_STRING:
        return datetime.now(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0
        ).astimezone(timezone.utc) - timedelta(days=1)

    try:
        return datetime.strptime(date_str, "%Y-%m-%d").astimezone(timezone.utc)
    except ValueError as e:
        logger.error("Invalid date format: %s", e)
        raise DateFormatError(f"Invalid date format: {e}")


def parse_date_range(
    config: Config,
    target_date: datetime,
    days_back: Optional[int],
    days_range: Optional[int],
) -> Tuple[str, str]:
    days_back = days_back if days_back is not None else config.cleaner.days_back
    days_range = days_range if days_range is not None else config.cleaner.days_range

    end_date = target_date - timedelta(days=days_back)
    start_date = end_date - timedelta(days=days_range)

    return start_date.strftime("%Y-%m-%d"), end_date.strftime("%Y-%m-%d")


def get_formatted_date(target_date: Optional[datetime]) -> str:
    if target_date:
        return target_date.strftime("%Y-%m-%d")

    return (
        datetime.now(timezone.utc)
        .replace(hour=0, minute=0, second=0, microsecond=0)
        .astimezone(timezone.utc)
        - timedelta(days=1)
    ).strftime("%Y-%m-%d")


def send_failure_notification(
    boto3_session: boto3.Session,
    topic_arn: str,
    target_date: Optional[str],
    error_message: Optional[str] = None,
) -> None:
    sns = boto3_session.client("sns")
    date_str = target_date if isinstance(target_date, str) else get_formatted_date(None)

    message = (
        f"Paper cleaning failed\n"
        f"Date: {date_str}\n"
        f"Error: {error_message or 'Unknown error'}"
    )

    sns.publish(TopicArn=topic_arn, Message=message, Subject="Paper Bridge Failure")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Paper Bridge: A service that curates and summarizes arXiv papers daily"
    )
    parser.add_argument(
        "--target-date",
        type=str,
        default=None,
        help="Target date to fetch papers in 'YYYY-MM-DD' format",
    )
    parser.add_argument(
        "--days-back",
        type=int,
        default=None,
        help="Number of days to go back from target date",
    )
    parser.add_argument(
        "--days-range",
        type=int,
        default=None,
        help="Number of days range to delete",
    )
    args = parser.parse_args()

    target_date_str = (
        None
        if args.target_date and args.target_date.lower() == NULL_STRING
        else args.target_date
    )
    days_back = (
        None
        if args.days_back is not None and str(args.days_back).lower() == NULL_STRING
        else args.days_back
    )
    days_range = (
        None
        if args.days_range is not None and str(args.days_range).lower() == NULL_STRING
        else args.days_range
    )

    logger.info(
        "Processing cleaner with target_date='%s', days_back='%s', days_range='%s'",
        target_date_str or "",
        days_back or "",
        days_range or "",
    )

    event = {
        "TARGET_DATE": target_date_str,
        "DAYS_BACK": days_back,
        "DAYS_RANGE": days_range,
    }

    result = lambda_handler(event, None)
    exit_code = 0 if result["status"] == 200 else 1
    sys.exit(exit_code)
