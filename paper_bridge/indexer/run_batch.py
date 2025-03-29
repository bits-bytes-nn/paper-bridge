import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Tuple
import boto3

sys.path.append(str(Path(__file__).parent.parent.parent))
from paper_bridge.indexer.configs import Config
from paper_bridge.indexer.src import (
    EnvVars,
    NULL_STRING,
    SSMParams,
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

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    job_name = (
        f"{config.resources.project_name}"
        f"-{config.resources.stage}"
        f"-{job_prefix}-{timestamp}"
    )
    logger.info(
        "Submitting batch job '%s' with parameters: %s",
        job_name,
        kwargs,
    )

    job_id = submit_batch_job(
        boto3_session,
        job_name,
        job_queue_name,
        job_definition_name,
        parameters=kwargs,
    )

    wait_for_batch_job_completion(boto3_session, job_id)


def get_batch_job_names(
    boto3_session: boto3.Session,
    config: Config,
) -> Tuple[str, str]:
    base_path = f"/{config.resources.project_name}-{config.resources.stage}"
    return (
        get_ssm_param_value(
            boto3_session,
            f"{base_path}/{SSMParams.BATCH_JOB_QUEUE.value}",
        ),
        get_ssm_param_value(
            boto3_session,
            f"{base_path}/{SSMParams.BATCH_JOB_DEFINITION.value}",
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
        help="Target date to fetch papers in 'YYYY-MM-DD' format",
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

    args = parser.parse_args()

    arxiv_ids = NULL_STRING if args.arxiv_ids is None else " ".join(args.arxiv_ids)

    main(
        "indexing",
        target_date=args.target_date or NULL_STRING,
        days_to_fetch=args.days_to_fetch or NULL_STRING,
        arxiv_ids=arxiv_ids,
    )
