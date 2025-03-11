from typing import Optional
import boto3
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
