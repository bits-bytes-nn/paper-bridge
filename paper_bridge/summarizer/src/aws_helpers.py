import os
import time
from pathlib import Path
from typing import Dict, List, Optional
import boto3
from boto3.s3.transfer import TransferConfig
from botocore.exceptions import ClientError

from .logger import logger


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
        logger.error(f"Error checking cross-inference support: {str(e)}")

    return model_id


def get_ssm_param_value(boto3_session: boto3.Session, param_name: str) -> Optional[str]:
    if not param_name:
        raise ValueError("Parameter name must not be empty")

    ssm_client = boto3_session.client("ssm")
    try:
        response = ssm_client.get_parameter(Name=param_name, WithDecryption=True)
        return response["Parameter"]["Value"]

    except ClientError as error:
        logger.error("Failed to get SSM parameter value: %s", str(error))
        return None


def submit_batch_job(
    boto3_session: boto3.Session,
    job_name: str,
    job_queue_name: str,
    job_definition_name: str,
    parameters: Optional[Dict[str, str]] = None,
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


def upload_dir_to_s3(
    boto3_session: boto3.Session,
    local_dir: str,
    bucket_name: str,
    prefix: str,
    file_ext_to_incl: Optional[List[str]] = None,
    public_readable: bool = False,
) -> int:
    try:
        s3_client = boto3_session.client("s3")
        file_ext_to_incl = file_ext_to_incl or []

        config = TransferConfig(
            multipart_threshold=1024 * 25,
            max_concurrency=10,
            multipart_chunksize=1024 * 25,
            use_threads=True,
        )

        extra_args = {"ACL": "public-read"} if public_readable else {}
        upload_count = 0

        for root, _, files in os.walk(local_dir):
            for filename in files:
                if not file_ext_to_incl or filename.split(".")[-1] in file_ext_to_incl:
                    local_path = os.path.join(root, filename)
                    relative_path = os.path.relpath(local_path, local_dir)
                    s3_path = os.path.join(prefix, relative_path).replace("\\", "/")

                    s3_client.upload_file(
                        local_path,
                        bucket_name,
                        s3_path,
                        Config=config,
                        ExtraArgs=extra_args,
                    )
                    upload_count += 1
                    logger.info(
                        "Uploaded '%s' to 's3://%s/%s'",
                        relative_path,
                        bucket_name,
                        s3_path,
                    )

        logger.info("Successfully uploaded %d files", upload_count)
        return upload_count

    except Exception as e:
        logger.error("Failed to upload directory to S3: %s", str(e))
        return 0


def upload_to_s3(
    boto3_session: boto3.Session,
    file_path: Path,
    s3_bucket_name: str,
    s3_prefix: Optional[str] = None,
) -> bool:
    if not file_path.exists():
        logger.error("File not found: %s", file_path)
        return False

    if not s3_bucket_name:
        logger.error("S3 bucket name is required")
        return False

    try:
        s3_client = boto3_session.client("s3")

        prefix = s3_prefix.strip("/") + "/" if s3_prefix else ""
        s3_key = f"{prefix}{file_path.name}"

        s3_client.upload_file(str(file_path), s3_bucket_name, s3_key)

        logger.info(
            "Successfully uploaded '%s' to 's3://%s/%s'",
            file_path.name,
            s3_bucket_name,
            s3_key,
        )
        return True

    except ClientError as e:
        error_code = e.response.get("Error", {}).get("Code", "Unknown")
        error_msg = e.response.get("Error", {}).get("Message", str(e))
        logger.error(
            "Failed to upload '%s' to S3: %s - %s", file_path, error_code, error_msg
        )
        return False


def wait_for_batch_job_completion(boto3_session: boto3.Session, job_id: str) -> bool:
    batch_client = boto3_session.client("batch")

    print("Waiting for job completion", end="", flush=True)
    while True:
        response = batch_client.describe_jobs(jobs=[job_id])
        if not response["jobs"]:
            print("\nJob not found")
            return False

        status = response["jobs"][0]["status"]
        if status == "SUCCEEDED":
            print("\nJob completed successfully!")
            return True
        elif status in ["FAILED", "CANCELLED"]:
            print(f"\nJob {status.lower()}!")
            return False

        print(".", end="", flush=True)
        time.sleep(30)
