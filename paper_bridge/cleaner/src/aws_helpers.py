import boto3
from botocore.exceptions import ClientError

# NeptuneClient/OpenSearchClient now live in paper_bridge.shared as a single
# implementation shared with the indexer (they had drifted into two divergent
# copies). Re-exported here so existing imports
# (``from .aws_helpers import NeptuneClient``) keep working.
from paper_bridge.shared.neptune_client import NeptuneClient
from paper_bridge.shared.opensearch_client import OpenSearchClient

from .logger import logger

__all__ = [
    "NeptuneClient",
    "OpenSearchClient",
    "get_ssm_param_value",
]


def get_ssm_param_value(boto3_session: boto3.Session, param_name: str) -> str:
    ssm_client = boto3_session.client("ssm")
    try:
        response = ssm_client.get_parameter(Name=param_name, WithDecryption=True)
        return response["Parameter"]["Value"]
    except ClientError as error:
        logger.error("Failed to get SSM parameter '%s': %s", param_name, error)
        raise error
