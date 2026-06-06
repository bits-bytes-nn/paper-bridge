"""Shared OpenSearch Serverless (aoss) client for the lexical-graph vectors.

Single implementation used by both the indexer (re-index cleanup, per-paper) and
the cleaner (scheduled date-range deletion). Supersedes the two divergent copies
in ``indexer/src/aws_helpers.py`` and ``cleaner/src/aws_helpers.py``.

All deletions go through ``delete_by_query`` rather than search-then-delete-by-id.
The old indexer ``delete_document`` ran a default-size search (10 hits) and
deleted those ids, silently leaving every chunk/statement beyond the first 10 —
orphan vectors that then polluted retrieval after re-indexing. ``delete_by_query``
removes every match in one server-side call with no hit cap.
"""

from __future__ import annotations

import logging
import re
from typing import Any

import boto3
from opensearchpy import NotFoundError, OpenSearch, RequestsHttpConnection
from requests_aws4auth import AWS4Auth

# Module logger inherits the handlers/level configured by the importing app.
logger = logging.getLogger(__name__)

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_PAPER_ID_FIELD = "metadata.source.metadata.paper_id"
_BASE_DATE_FIELD = "metadata.source.metadata.base_date"


def _is_valid_date_format(date_str: str) -> bool:
    return bool(date_str) and bool(_DATE_RE.match(date_str))


class OpenSearchClient:
    def __init__(
        self,
        host: str,
        port: int,
        index: str,
        boto3_session: boto3.Session,
        region_name: str,
    ):
        if not all([host, port, index, region_name, boto3_session]):
            raise ValueError("All OpenSearch connection parameters must be provided.")
        self.index = index
        try:
            credentials = boto3_session.get_credentials()
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
            logger.info("OpenSearch connection successful for index '%s'", index)
        except Exception as e:
            logger.error(
                "Failed to initialize OpenSearch client for index '%s': %s", index, e
            )
            raise
        logger.info("OpenSearch endpoint: '%s:%s'", host, port)

    def _check_index_exists(self) -> bool:
        try:
            return self.client.indices.exists(index=self.index)
        except Exception as e:
            logger.error("Error checking if index '%s' exists: %s", self.index, e)
            return False

    def _delete_by_query(
        self, body: dict[str, Any], **summary_context: Any
    ) -> dict[str, Any]:
        """Run a delete_by_query and normalize the response/skip/error shape."""
        if not self._check_index_exists():
            logger.warning("Index '%s' does not exist. Skipping deletion.", self.index)
            return {"status": "skipped", "reason": f"Index '{self.index}' not found."}

        try:
            response = self.client.delete_by_query(index=self.index, body=body)
            logger.info(
                "OpenSearch deletion for index '%s': deleted=%s total=%s",
                self.index,
                response.get("deleted", 0),
                response.get("total", 0),
            )
            return {
                "status": "success",
                "deleted": response.get("deleted", 0),
                "total": response.get("total", 0),
                "failures": response.get("failures", []),
                **summary_context,
            }
        except NotFoundError:
            logger.warning("Index '%s' not found during delete.", self.index)
            return {"status": "skipped", "reason": f"Index '{self.index}' not found."}
        except Exception as e:
            error_msg = f"Error deleting from index '{self.index}': {e}"
            logger.error(error_msg)
            return {"status": "error", "error": error_msg, **summary_context}

    def delete_document(self, paper_id: str) -> dict[str, Any]:
        """Delete every vector for a single paper (used by re-index cleanup)."""
        if not paper_id:
            raise ValueError("'paper_id' must not be empty.")
        body = {"query": {"term": {_PAPER_ID_FIELD: paper_id}}}
        return self._delete_by_query(body, paper_id=paper_id)

    def batch_delete_documents(self, paper_ids: list[str]) -> list[dict[str, Any]]:
        if not paper_ids:
            logger.warning("No 'paper_id's provided for batch deletion.")
            return []
        return [self.delete_document(paper_id) for paper_id in paper_ids]

    def delete_documents_by_date(self, base_date: str) -> dict[str, Any]:
        if not _is_valid_date_format(base_date):
            raise ValueError("'base_date' must be in the format 'YYYY-MM-DD'.")
        body = {"query": {"term": {_BASE_DATE_FIELD: base_date}}}
        return self._delete_by_query(body, base_date=base_date)

    def delete_documents_by_date_range(
        self, start_date: str, end_date: str
    ) -> dict[str, Any]:
        if not (_is_valid_date_format(start_date) and _is_valid_date_format(end_date)):
            raise ValueError("Date values must be in 'YYYY-MM-DD' format.")
        body = {
            "query": {"range": {_BASE_DATE_FIELD: {"gte": start_date, "lte": end_date}}}
        }
        return self._delete_by_query(body, date_range=f"{start_date} to {end_date}")
