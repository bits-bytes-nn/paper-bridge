import argparse
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional, Tuple
import boto3
from pytz import timezone

sys.path.append(str(Path(__file__).parent.parent.parent))
from paper_bridge.indexer.configs import Config
from paper_bridge.indexer.src import (
    EnvVars,
    SSMParams,
    arg_as_bool,
    get_ssm_param_value,
    logger,
    submit_batch_job,
    wait_for_batch_job_completion,
)


def main(
    target_date: Optional[str] = None,
    days_to_fetch: Optional[int] = None,
    enable_batch_inference: bool = False,
) -> None:
    config = Config.load()
    profile_name = EnvVars.AWS_PROFILE_NAME.value

    boto3_session = boto3.Session(
        region_name=config.resources.default_region_name,
        profile_name=profile_name,
    )

    job_queue_name, job_definition_name = get_batch_job_names(boto3_session, config)

    timestamp = datetime.now(timezone("UTC")).strftime("%Y%m%d%H%M%S")
    job_name = (
        f"{config.resources.project_name}"
        f"-{config.resources.stage}"
        f"-indexing-{timestamp}"
    )

    logger.info(
        "Submitting batch job '%s' with parameters: target_date=%s, days_to_fetch=%s, enable_batch_inference=%s",
        job_name,
        target_date,
        days_to_fetch,
        enable_batch_inference,
    )

    job_params = create_job_parameters(
        target_date, days_to_fetch, enable_batch_inference
    )
    job_id = submit_batch_job(
        boto3_session,
        job_name,
        job_queue_name,
        job_definition_name,
        parameters=job_params,
    )

    wait_for_batch_job_completion(boto3_session, job_id)


def get_batch_job_names(
    boto3_session: boto3.Session,
    config: Config,
) -> Tuple[str, str]:
    base_path = f"/{config.resources.project_name}-{config.resources.stage}"

    job_queue = get_ssm_param_value(
        boto3_session,
        f"{base_path}/{SSMParams.BATCH_JOB_QUEUE.value}",
    )
    job_definition = get_ssm_param_value(
        boto3_session,
        f"{base_path}/{SSMParams.BATCH_JOB_DEFINITION.value}",
    )

    if not job_queue or not job_definition:
        raise ValueError("Failed to retrieve batch job configuration from SSM")

    return job_queue, job_definition


def create_job_parameters(
    target_date: Optional[str],
    days_to_fetch: Optional[int],
    enable_batch_inference: bool,
) -> Dict[str, str]:
    return {
        "target_date": target_date or "None",
        "days_to_fetch": days_to_fetch or "0",
        "enable_batch_inference": str(enable_batch_inference),
    }


if __name__ == "__main__":
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

    args = parser.parse_args()
    main(args.target_date, args.days_to_fetch, args.enable_batch_inference)
