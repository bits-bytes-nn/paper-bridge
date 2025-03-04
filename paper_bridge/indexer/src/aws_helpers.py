from typing import Optional, Tuple
import boto3
from botocore.exceptions import ClientError
from gremlin_python.driver import client, serializer
from opensearchpy import OpenSearch, RequestsHttpConnection
from requests_aws4auth import AWS4Auth
from .logger import logger


class NeptuneClient:
    DEFAULT_PORT = 8182
    DEFAULT_PROTOCOL = "wss"

    def __init__(self, neptune_endpoint: str):
        self.endpoint = neptune_endpoint
        self._client = None

    @property
    def client(self):
        if not self._client:
            self._client = client.Client(
                f"{self.DEFAULT_PROTOCOL}://{self.endpoint}:{self.DEFAULT_PORT}/gremlin",
                "g",
                message_serializer=serializer.GraphSONSerializersV2d0(),
            )
        return self._client

    def delete_nodes_by_metadata(self, field: str, value: str) -> None:
        if not field or not value:
            raise ValueError("Field and value must not be empty")

        query = f"g.V().has('{field}', '{value}').drop()"

        try:
            result = self.client.submitAsync(query)
            if result is not None:
                result.result()
                logger.info(f"Successfully deleted nodes with '{field}={value}'")
            else:
                logger.warning(f"No nodes found with '{field}={value}'")
        except Exception as e:
            logger.error(f"Failed to delete nodes with '{field}={value}': {str(e)}")
            raise


class OpenSearchClient:
    def __init__(
        self,
        host: str,
        port: int,
        index: str,
        region_name: str,
        profile_name: Optional[str] = None,
    ):
        credentials = boto3.Session(
            region_name=region_name, profile_name=profile_name
        ).get_credentials()
        awsauth = AWS4Auth(
            credentials.access_key,
            credentials.secret_key,
            region_name,
            "aoss",
            session_token=credentials.token,
        )

        self.client = OpenSearch(
            hosts=[{"host": host, "port": port}],
            http_auth=awsauth,
            use_ssl=True,
            verify_certs=True,
            connection_class=RequestsHttpConnection,
            timeout=30,
            retry_on_timeout=True,
            max_retries=3,
        )
        self.index = index

    def delete_nodes_by_metadata(self, collection: str, field: str, value: str) -> None:
        if not field or not value:
            raise ValueError("Field and value must not be empty")

        query = {"query": {"term": {field: value}}}
        try:
            response = self.client.delete_by_query(
                index=self.index,
                body=query,
                params={"collection": collection},
            )
            logger.info(
                f"Successfully deleted {response['deleted']} documents with '{field}={value}'"
            )
        except Exception as e:
            logger.error(f"Failed to delete documents with '{field}={value}': {str(e)}")
            raise


def get_account_id(boto3_session: boto3.Session) -> str:
    try:
        sts_client = boto3_session.client("sts")
        return sts_client.get_caller_identity()["Account"]
    except ClientError as e:
        logger.error("Failed to get account ID: %s", str(e))
        raise


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


def get_ssm_param_value(boto3_session: boto3.Session, param_name: str) -> str:
    ssm_client = boto3_session.client("ssm")
    try:
        response = ssm_client.get_parameter(Name=param_name, WithDecryption=True)
        return response["Parameter"]["Value"]

    except ClientError as error:
        logger.error("Failed to get SSM parameter value: %s", str(error))
        raise error
