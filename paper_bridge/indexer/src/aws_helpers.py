import boto3
from .logger import logger


def get_cross_inference_model_id(
    boto3_session: boto3.Session, model_id: str, region_name: str
) -> str:
    if region_name.startswith("ap-"):
        prefix = "apac"
    else:
        prefix = region_name[:2]

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
