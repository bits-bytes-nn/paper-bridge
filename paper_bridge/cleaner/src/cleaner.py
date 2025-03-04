from typing import Dict, List, Optional, Any
import boto3
from .aws_helpers import NeptuneClient, OpenSearchClient
from .logger import logger


class Cleaner:
    def __init__(
        self,
        neptune_endpoint: str,
        opensearch_endpoint: str,
        opensearch_indexes: List[str],
        region_name: str = "us-west-2",
        profile_name: Optional[str] = None,
        boto3_session: Optional[boto3.Session] = None,
    ):
        self.boto3_session = boto3_session or boto3.Session(
            region_name=region_name, profile_name=profile_name
        )
        self.region_name = region_name

        self.neptune_client = NeptuneClient(neptune_endpoint)

        self.opensearch_indexes = opensearch_indexes
        self.opensearch_clients = {}
        for index in opensearch_indexes:
            self.opensearch_clients[index] = OpenSearchClient(
                host=opensearch_endpoint,
                port=443,
                index=index,
                region_name=region_name,
                boto3_session=self.boto3_session,
            )

    def delete_documents_by_date_range(
        self, start_date: str, end_date: Optional[str] = None
    ) -> Dict[str, Any]:
        if end_date is None:
            end_date = start_date

        neptune_results = self.neptune_client.delete_documents_by_date_range(
            start_date, end_date
        )
        logger.info(f"Neptune deletion result: {neptune_results}")

        opensearch_results = {}
        for index in self.opensearch_indexes:
            try:
                result = self.opensearch_clients[index].delete_documents_by_date_range(
                    start_date, end_date
                )
                opensearch_results[index] = result
                logger.info(f"OpenSearch deletion result for index '{index}': {result}")

            except Exception as e:
                error_msg = f"Error deleting documents from OpenSearch index '{index}': {str(e)}"
                logger.error(error_msg)
                opensearch_results[index] = {"status": "error", "error": error_msg}

        return {
            "neptune": neptune_results,
            "opensearch": opensearch_results,
            "date_range": f"{start_date} to {end_date}",
        }
