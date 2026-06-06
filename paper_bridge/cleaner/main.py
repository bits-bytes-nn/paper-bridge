import argparse
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import boto3

sys.path.append(str(Path(__file__).parent.parent.parent))

from paper_bridge.cleaner.configs import Config
from paper_bridge.cleaner.src import (
    NULL_STRING,
    Cleaner,
    EnvVars,
    SSMParams,
    get_ssm_param_value,
    is_running_in_aws,
)

# Import the logger INSTANCE explicitly (not via the package's lazy __getattr__),
# which is ambiguous with the ``logger`` submodule depending on import order —
# see the note in summarizer/main.py.
from paper_bridge.cleaner.src.logger import logger


class DateFormatError(Exception):
    pass


def setup_dependencies() -> tuple[Config, boto3.Session]:
    config = Config.load()
    profile_name = EnvVars.AWS_PROFILE_NAME.env_value
    boto_session = (
        boto3.Session(region_name=EnvVars.DEFAULT_REGION_NAME.env_value)
        if is_running_in_aws()
        else boto3.Session(
            region_name=config.resources.default_region_name, profile_name=profile_name
        )
    )

    try:
        sts = boto_session.client("sts")
        caller_identity = sts.get_caller_identity()
        logger.info("Python session AWS identity: '%s'", caller_identity.get("Arn"))
        logger.info("Profile name used: '%s'", profile_name)
        logger.info("Is AWS env: '%s'", is_running_in_aws())
    except Exception as e:
        logger.error("Failed to get session identity: %s", e)

    return config, boto_session


def parse_event_params(
    event: dict[str, Any],
) -> tuple[str | None, int | None, int | None]:
    def get_optional_str(key: str) -> str | None:
        val = event.get(key)
        return (
            None
            if not val or (isinstance(val, str) and val.lower() == NULL_STRING)
            else str(val)
        )

    def get_optional_int(key: str) -> int | None:
        val = get_optional_str(key)
        if val is None:
            return None
        try:
            return int(val)
        except (ValueError, TypeError):
            logger.warning("Invalid integer value for '%s': %s. Ignoring.", key, val)
            return None

    target_date_str = get_optional_str("TARGET_DATE")
    days_back = get_optional_int("DAYS_BACK")
    days_range = get_optional_int("DAYS_RANGE")

    return target_date_str, days_back, days_range


def parse_target_date(date_str: str | None) -> datetime:
    if not date_str:
        return datetime.now(UTC).replace(
            hour=0, minute=0, second=0, microsecond=0
        ) - timedelta(days=1)
    try:
        return datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=UTC)
    except ValueError as e:
        raise DateFormatError(
            f"Invalid date format for TARGET_DATE: '{date_str}'. Use 'YYYY-MM-DD'."
        ) from e


def calculate_date_range(
    config: Config,
    target_date: datetime,
    days_back: int | None,
    days_range: int | None,
) -> tuple[str, str]:
    effective_days_back = (
        days_back if days_back is not None else config.cleaner.days_back
    )
    effective_days_range = (
        days_range if days_range is not None else config.cleaner.days_range
    )

    end_date = target_date - timedelta(days=effective_days_back)
    start_date = end_date - timedelta(days=effective_days_range - 1)

    return start_date.strftime("%Y-%m-%d"), end_date.strftime("%Y-%m-%d")


def send_failure_notification(
    session: boto3.Session, topic_arn: str, date_range: str, error: Exception
) -> None:
    sns = session.client("sns")
    message = (
        f"Paper Bridge Cleaner Failed\n\n"
        f"Date Range: {date_range}\n"
        f"Error: {error}"
    )
    sns.publish(
        TopicArn=topic_arn, Message=message, Subject="Paper Bridge Cleaner Failure"
    )


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    start_date, end_date = "", ""
    config, boto_session = setup_dependencies()
    try:
        target_date_str, days_back, days_range = parse_event_params(event)

        target_datetime = parse_target_date(target_date_str)
        start_date, end_date = calculate_date_range(
            config, target_datetime, days_back, days_range
        )
        logger.info("Calculated deletion range: '%s' to '%s'", start_date, end_date)

        base_path = f"/{config.resources.project_name}-{config.resources.stage}"
        neptune_endpoint = get_ssm_param_value(
            boto_session, f"{base_path}/{SSMParams.NEPTUNE_ENDPOINT.value}"
        )
        opensearch_endpoint = get_ssm_param_value(
            boto_session, f"{base_path}/{SSMParams.OPENSEARCH_ENDPOINT.value}"
        )

        cleaner = Cleaner(
            boto_session,
            neptune_endpoint,
            opensearch_endpoint.replace("https://", ""),
            config.cleaner.opensearch_indexes,
            region_name=config.resources.default_region_name,
        )

        # Diagnostic escape hatch: run an arbitrary Gremlin query and return its
        # result. Used to isolate which traversal trips Neptune's memory limit.
        # Guarded behind an explicit event key so it never runs on the schedule.
        diag_query = event.get("DIAG_QUERY")
        if diag_query:
            logger.info("DIAG query: %s", diag_query)
            diag_result = cleaner.neptune_client._submit_query(diag_query)
            return {"statusCode": 200, "body": "diag", "result": str(diag_result)[:2000]}

        result = cleaner.delete_documents_by_date_range(
            start_date=start_date, end_date=end_date
        )
        logger.info("Deletion process completed. Result: '%s'", result)

        return {"statusCode": 200, "body": "Success", "result": result}

    except Exception as e:
        error_message = (
            f"Failed to clean documents for range '{start_date}' to '{end_date}': {e}"
        )
        logger.exception(error_message)

        topic_arn = EnvVars.TOPIC_ARN.env_value
        if is_running_in_aws() and topic_arn:
            send_failure_notification(
                boto_session, topic_arn, f"'{start_date}' to '{end_date}'", e
            )

        return {"statusCode": 500, "body": error_message}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Clean old documents from Paper Bridge."
    )
    parser.add_argument(
        "--target-date",
        type=str,
        help="Target date in 'YYYY-MM-DD' format. Defaults to yesterday.",
    )
    parser.add_argument(
        "--days-back", type=int, help="Number of days to go back from target date."
    )
    parser.add_argument(
        "--days-range", type=int, help="Number of days in the deletion range."
    )
    args = parser.parse_args()

    event_payload = {
        "TARGET_DATE": args.target_date,
        "DAYS_BACK": args.days_back,
        "DAYS_RANGE": args.days_range,
    }
    logger.info("Running cleaner locally with payload: '%s'", event_payload)

    result = lambda_handler(event_payload, None)
    sys.exit(0 if result["statusCode"] == 200 else 1)
