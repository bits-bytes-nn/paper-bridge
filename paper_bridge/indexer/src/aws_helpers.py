import time

import boto3
from botocore.exceptions import ClientError

# NeptuneClient/OpenSearchClient now live in paper_bridge.shared as a single
# implementation shared with the cleaner (they had drifted into two divergent
# copies). Re-exported here so existing imports
# (``from .aws_helpers import NeptuneClient``) keep working.
from paper_bridge.shared.neptune_client import (
    NeptuneClient,
    summarize_deletion_results,
)
from paper_bridge.shared.opensearch_client import OpenSearchClient

from .logger import logger

__all__ = [
    "NeptuneClient",
    "OpenSearchClient",
    "summarize_deletion_results",
    "get_account_id",
    "get_cross_inference_model_id",
    "get_ssm_param_value",
    "submit_batch_job",
    "wait_for_batch_job_completion",
]


def get_account_id(boto3_session: boto3.Session) -> str:
    try:
        sts_client = boto3_session.client("sts")
        return sts_client.get_caller_identity()["Account"]
    except ClientError as e:
        logger.error("Failed to get account ID: '%s'", str(e))
        raise


def get_cross_inference_model_id(
    boto3_session: boto3.Session, model_id: str, region_name: str
) -> str:
    if not all([boto3_session, model_id, region_name]):
        raise ValueError("All parameters must be provided")

    prefix = "apac" if region_name.startswith("ap-") else region_name[:2]
    cr_model_id = f"{prefix}.{model_id}"

    try:
        bedrock_client = boto3_session.client("bedrock", region_name=region_name)
        response = bedrock_client.list_inference_profiles(
            maxResults=1000, typeEquals="SYSTEM_DEFINED"
        )
        profile_list = [
            p["inferenceProfileId"] for p in response["inferenceProfileSummaries"]
        ]

        if cr_model_id in profile_list:
            return cr_model_id

    except Exception as e:
        logger.error("Error checking cross-inference support: %s", str(e))

    return model_id


def get_ssm_param_value(boto3_session: boto3.Session, param_name: str) -> str:
    ssm_client = boto3_session.client("ssm")
    try:
        response = ssm_client.get_parameter(Name=param_name, WithDecryption=True)
        return response["Parameter"]["Value"]
    except ClientError as error:
        logger.error("Failed to get SSM parameter value: %s", str(error))
        raise error


def submit_batch_job(
    boto3_session: boto3.Session,
    job_name: str,
    job_queue_name: str,
    job_definition_name: str,
    parameters: dict[str, str] | None = None,
) -> str:
    batch_client = boto3_session.client("batch")
    try:
        response = batch_client.submit_job(
            jobName=job_name,
            jobQueue=job_queue_name,
            jobDefinition=job_definition_name,
            parameters=parameters or {},
        )
        job_id = response["jobId"]
        logger.info(
            "Successfully submitted batch job '%s' (Job ID: %s)",
            job_name,
            job_id,
        )
        return job_id

    except ClientError as error:
        logger.error("Failed to submit batch job '%s': %s", job_name, str(error))
        raise


def wait_for_batch_job_completion(boto3_session: boto3.Session, job_id: str) -> bool:
    batch_client = boto3_session.client("batch")

    logger.info("Waiting for batch job '%s' to complete", job_id)
    while True:
        response = batch_client.describe_jobs(jobs=[job_id])
        if not response["jobs"]:
            logger.warning("Batch job '%s' not found", job_id)
            return False

        status = response["jobs"][0]["status"]
        if status == "SUCCEEDED":
            logger.info("Batch job '%s' completed successfully", job_id)
            return True
        if status in ("FAILED", "CANCELLED"):
            logger.error("Batch job '%s' %s", job_id, status.lower())
            return False

        time.sleep(30)
