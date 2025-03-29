from typing import Any, Dict, List, Optional
import boto3
from .aws_helpers import NeptuneClient, OpenSearchClient
from .logger import logger


class Cleaner:
    def __init__(
        self,
        boto3_session: boto3.Session,
        neptune_endpoint: str,
        opensearch_endpoint: str,
        opensearch_indexes: List[str],
        region_name: Optional[str] = None,
    ):
        self.neptune_client = NeptuneClient(neptune_endpoint)

        self.opensearch_indexes = opensearch_indexes
        self.opensearch_clients = {}
        for index in opensearch_indexes:
            self.opensearch_clients[index] = OpenSearchClient(
                opensearch_endpoint.replace("http://", "").replace("https://", ""),
                443,
                index,
                boto3_session,
                region_name or "us-west-2",
            )

    def delete_documents_by_date_range(
        self, start_date: str, end_date: Optional[str] = None
    ) -> Dict[str, Any]:
        if end_date is None:
            end_date = start_date

        neptune_results = self.neptune_client.delete_documents_by_date_range(
            start_date, end_date
        )
        logger.info("Neptune deletion result: %s", neptune_results)

        opensearch_results = {}
        for index in self.opensearch_indexes:
            try:
                result = self.opensearch_clients[index].delete_documents_by_date_range(
                    start_date, end_date
                )
                opensearch_results[index] = result
                logger.info(
                    "OpenSearch deletion result for index '%s': %s", index, result
                )

            except Exception as e:
                error_msg = f"Error deleting documents from OpenSearch index '{index}': {str(e)}"
                logger.error(error_msg)
                opensearch_results[index] = {"status": "error", "error": error_msg}

        return {
            "neptune": neptune_results,
            "opensearch": opensearch_results,
            "date_range": f"{start_date} to {end_date}",
        }
