from typing import Any

import boto3

from .aws_helpers import NeptuneClient, OpenSearchClient
from .logger import logger


class Cleaner:
    def __init__(
        self,
        boto_session: boto3.Session,
        neptune_endpoint: str,
        opensearch_endpoint: str,
        opensearch_indexes: list[str],
        region_name: str,
    ):
        self.neptune_client = NeptuneClient(neptune_endpoint)
        self.opensearch_clients = {
            index: OpenSearchClient(
                opensearch_endpoint, 443, index, boto_session, region_name
            )
            for index in opensearch_indexes
        }

    def delete_documents_by_date_range(
        self, start_date: str, end_date: str
    ) -> dict[str, Any]:
        logger.info(
            "Starting deletion process for date range: '%s' to '%s'",
            start_date,
            end_date,
        )

        neptune_results = self.neptune_client.delete_documents_by_date_range(
            start_date, end_date
        )
        logger.info("Neptune deletion result: '%s'", neptune_results)

        opensearch_results = {}
        for index, client in self.opensearch_clients.items():
            try:
                result = client.delete_documents_by_date_range(start_date, end_date)
                opensearch_results[index] = result
                logger.info(
                    "OpenSearch deletion result for index '%s': '%s'", index, result
                )
            except Exception as e:
                error_msg = (
                    f"Unhandled error deleting from OpenSearch index '{index}': {e}"
                )
                logger.error(error_msg)
                opensearch_results[index] = {"status": "error", "error": error_msg}

        return {
            "neptune": neptune_results,
            "opensearch": opensearch_results,
            "date_range": f"'{start_date}' to '{end_date}'",
        }
