import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional, Tuple, Any
import boto3

sys.path.append(str(Path(__file__).parent.parent.parent))
from paper_bridge.summarizer.configs import Config
from paper_bridge.summarizer.src import (
    EnvVars,
    NULL_STRING,
    SSMParams,
    arg_as_bool,
    get_ssm_param_value,
    logger,
    submit_batch_job,
    wait_for_batch_job_completion,
)


def main(job_prefix: str, **kwargs) -> None:
    config = Config.load()
    profile_name = EnvVars.AWS_PROFILE_NAME.value

    boto3_session = boto3.Session(
        region_name=config.resources.default_region_name,
        profile_name=profile_name,
    )

    job_queue_name, job_definition_name = get_batch_job_names(boto3_session, config)
    if not job_queue_name or not job_definition_name:
        raise ValueError(
            "Batch job queue or definition name not found in SSM parameters"
        )

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    job_name = (
        f"{config.resources.project_name}"
        f"-{config.resources.stage}"
        f"-{job_prefix}-{timestamp}"
    )

    sanitized_kwargs = sanitize_parameters(kwargs)
    logger.info(
        "Submitting batch job '%s' with parameters: %s",
        job_name,
        sanitized_kwargs,
    )

    job_id = submit_batch_job(
        boto3_session,
        job_name,
        job_queue_name,
        job_definition_name,
        parameters=sanitized_kwargs,
    )

    if not job_id:
        raise ValueError("Failed to submit batch job")

    logger.info("Batch job submitted with ID '%s'", job_id)
    wait_for_batch_job_completion(boto3_session, job_id)


def sanitize_parameters(params: Dict[str, Any]) -> Dict[str, str]:
    result = {}
    for key, value in params.items():
        if value is None:
            result[key] = NULL_STRING
        elif isinstance(value, list):
            if not value:
                result[key] = NULL_STRING
            else:
                result[key] = ",".join(str(v) for v in value)
        else:
            result[key] = str(value)
    return result


def get_batch_job_names(
    boto3_session: boto3.Session,
    config: Config,
) -> Tuple[Optional[str], Optional[str]]:
    base_path = f"/{config.resources.project_name}-{config.resources.stage}"
    return (
        get_ssm_param_value(
            boto3_session,
            f"{base_path}/{SSMParams.BATCH_JOB_QUEUE_SUMMARIZER.value}",
        ),
        get_ssm_param_value(
            boto3_session,
            f"{base_path}/{SSMParams.BATCH_JOB_DEFINITION_SUMMARIZER.value}",
        ),
    )


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
    parser.add_argument(
        "--send-business-slack",
        type=arg_as_bool,
        default=False,
        help="Whether to send business slack",
    )
    args = parser.parse_args()

    arxiv_ids = args.arxiv_ids
    target_date = args.target_date if args.target_date is not None else NULL_STRING
    days_to_fetch = str(args.days_to_fetch) if args.days_to_fetch is not None else "0"
    language = args.language if args.language is not None else NULL_STRING
    apply_retrieval = str(args.apply_retrieval)
    send_business_slack = str(args.send_business_slack)

    try:
        main(
            "summarizer",
            target_date=target_date,
            days_to_fetch=days_to_fetch,
            arxiv_ids=arxiv_ids,
            language=language,
            apply_retrieval=apply_retrieval,
            send_business_slack=send_business_slack,
        )
    except Exception as e:
        logger.error("Failed to submit or execute batch job: %s", e)
        sys.exit(1)
