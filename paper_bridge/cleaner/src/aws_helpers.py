import re
from typing import Any

import boto3
from botocore.exceptions import ClientError
from gremlin_python.driver import client, serializer
from opensearchpy import NotFoundError, OpenSearch, RequestsHttpConnection
from requests_aws4auth import AWS4Auth

from .logger import logger


def _is_valid_date_format(date_str: str) -> bool:
    if not date_str:
        return False
    return bool(re.match(r"^\d{4}-\d{2}-\d{2}$", date_str))


def _summarize_batch_results(
    results: list[dict[str, Any]], **context: Any
) -> dict[str, Any]:
    success_count = sum(1 for r in results if r.get("status") == "success")
    error_count = len(results) - success_count
    summary = {
        "status": "completed",
        "total_documents": len(results),
        "success_count": success_count,
        "error_count": error_count,
        "details": results,
    }
    summary.update(context)
    return summary


class NeptuneClient:
    DEFAULT_PORT: int = 8182
    DEFAULT_PROTOCOL: str = "wss"

    def __init__(self, neptune_endpoint: str):
        if not neptune_endpoint:
            raise ValueError("Neptune endpoint must be provided.")
        self.endpoint = neptune_endpoint
        self._gremlin_client = None
        logger.info("Neptune endpoint: '%s'", self.endpoint)

    @property
    def client(self) -> client.Client:
        if not self._gremlin_client:
            try:
                url = f"{self.DEFAULT_PROTOCOL}://{self.endpoint}:{self.DEFAULT_PORT}/gremlin"
                self._gremlin_client = client.Client(
                    url, "g", message_serializer=serializer.GraphSONSerializersV2d0()
                )
            except Exception as e:
                logger.error("Failed to initialize Neptune client: %s", e)
                raise
        return self._gremlin_client

    def _submit_query(
        self, query: str, bindings: dict[str, Any] | None = None
    ) -> list[Any]:
        """Submit a Gremlin query with optional parameter bindings."""
        return self.client.submit(query, bindings=bindings).all().result()

    # Drop at most this many vertices per query: a small, index-backed
    # g.V(id...).drop() stays well under the Neptune Serverless memory limit.
    _DROP_BATCH_SIZE = 50

    def _drop_vertices_by_id(self, vertex_ids: list[Any]) -> None:
        """Drop vertices by id in small batches (memory-safe, index-backed)."""
        for i in range(0, len(vertex_ids), self._DROP_BATCH_SIZE):
            batch = vertex_ids[i : i + self._DROP_BATCH_SIZE]
            # Neptune string ids; quote each and inline (bindings unsupported).
            id_args = ",".join(
                "'" + str(v).replace("'", "\\'") + "'" for v in batch
            )
            self._submit_query(f"g.V({id_args}).drop()")

    def delete_document(self, paper_id: str) -> dict[str, Any]:
        if not paper_id:
            raise ValueError("'paper_id' must not be empty.")
        if not re.match(r"^[a-zA-Z0-9_.:-]+$", paper_id):
            raise ValueError(f"Invalid 'paper_id' format: {paper_id}")

        # Neptune rejects Gremlin variable bindings on this endpoint, so inline
        # the paper_id. Injection is prevented by the strict regex validation
        # above (^[a-zA-Z0-9_.:-]+$).
        base_query = f"g.V().has('__Source__', 'paper_id', '{paper_id}')"

        try:
            # Two-phase deletion to stay within Neptune Serverless memory limits.
            #
            # The previous single-shot traversals (e.g. four chained .in() hops
            # followed by a per-candidate .where(in(...).count())) materialized the
            # whole candidate set plus a sub-traversal per element in one query and
            # blew MemoryLimitExceededException even at 16 NCU.
            #
            # Instead: (1) collect the vertex ids to delete with cheap, streamed
            # id() reads (one hop class at a time), then (2) drop them by id in
            # small batches. Dropping by id is index-backed and light, and the
            # collection queries never buffer whole vertices.
            #
            # Shared-node safety (entities/facts that still belong to OTHER papers
            # must survive) is enforced in phase 1 with the same count()==1 guard,
            # but evaluated while collecting ids rather than during the drop.
            # CRITICAL: dedup() after EVERY .in() hop. Chained .in().in().in()
            # without intermediate dedup multiplies duplicate traversal paths
            # (14 chunks -> their topics -> their statements fans out into
            # hundreds of thousands of redundant paths) and blows
            # MemoryLimitExceededException even though the distinct node counts
            # are tiny (14 / 416 / 365). Deduping per hop keeps each step bounded
            # to the distinct set. (Verified via diagnostics: the un-deduped 3-hop
            # chain OOMs at 32 NCU; the per-hop-deduped chain returns instantly.)
            collect_queries = {
                # statements/topics/chunks belong to exactly one source, so every
                # one reachable from this source is deletable.
                "chunks": f"{base_query}.in('__EXTRACTED_FROM__').dedup().id().fold()",
                "topics": f"{base_query}.in('__EXTRACTED_FROM__').dedup().in('__MENTIONED_IN__').dedup().id().fold()",
                "statements": f"{base_query}.in('__EXTRACTED_FROM__').dedup().in('__MENTIONED_IN__').dedup().in('__BELONGS_TO__').dedup().id().fold()",
                # facts/entities may be shared: keep only those whose sole link is
                # to statements/entities of THIS source (count()==1).
                "facts": f"""
                    {base_query}.in('__EXTRACTED_FROM__').dedup().in('__MENTIONED_IN__').dedup().in('__BELONGS_TO__').dedup()
                    .where(out('__SUPPORTS__').count().is(1)).in('__SUPPORTS__').dedup().id().fold()
                """,
                "entities": f"""
                    {base_query}.in('__EXTRACTED_FROM__').dedup().in('__MENTIONED_IN__').dedup().in('__BELONGS_TO__').dedup().in('__SUPPORTS__').dedup()
                    .union(out('__SUBJECT__'), out('__OBJECT__')).hasLabel('__Entity__').dedup()
                    .where(in('__SUBJECT__', '__OBJECT__').count().is(1)).dedup().id().fold()
                """,
            }

            deleted = {}
            for name, query in collect_queries.items():
                logger.info("Collecting '%s' ids for paper_id '%s'", name, paper_id)
                result = self._submit_query(query)
                ids = result[0] if result and result[0] else []
                logger.info("Collected %d '%s' ids; dropping", len(ids), name)
                self._drop_vertices_by_id(ids)
                deleted[name] = len(ids)
                logger.info(
                    "Deleted %d '%s' for paper_id: '%s'", len(ids), name, paper_id
                )

            # The source vertex itself is single and cheap to drop directly.
            self._submit_query(f"{base_query}.drop()")
            deleted["source"] = 1

            return {
                "status": "success",
                "paper_id": paper_id,
                "deleted_nodes": deleted,
            }

        except Exception as e:
            logger.error("Error deleting document '%s': %s", paper_id, e)
            raise

    def batch_delete_documents(self, paper_ids: list[str]) -> list[dict[str, Any]]:
        if not paper_ids:
            logger.warning("No 'paper_id's provided for batch deletion.")
            return []

        results = []
        for paper_id in paper_ids:
            try:
                result = self.delete_document(paper_id)
                results.append(result)
            except Exception as e:
                logger.error("Failed to delete document '%s': %s", paper_id, e)
                results.append(
                    {"status": "error", "paper_id": paper_id, "error": str(e)}
                )
        return results

    def _find_paper_ids_by_date_range(
        self, start_date: str, end_date: str
    ) -> list[str]:
        if not (_is_valid_date_format(start_date) and _is_valid_date_format(end_date)):
            raise ValueError("Date values must be in 'YYYY-MM-DD' format.")

        try:
            # Enumerate __Source__ vertices and filter by date in Python rather
            # than with a server-side between(datetime(...)) range predicate: on
            # the un-indexed base_date property that predicate makes Neptune
            # full-scan + materialize and blow MemoryLimitExceededException even at
            # higher NCU. __Source__ is one-per-paper (a tiny set).
            #
            # Use a plain valueMap streamed one vertex at a time (no fold(), no
            # project(), no Groovy closures): fold() buffers the entire result
            # server-side and the earlier between(datetime(...)) range predicate
            # full-scanned + materialized base_date, which tipped the memory
            # limit. valueMap emits one small map per __Source__ vertex (a tiny,
            # one-per-paper set). (Bindings are unsupported on this endpoint;
            # inlined values are validated to YYYY-MM-DD above to prevent
            # injection.)
            id_rows = self._submit_query(
                "g.V().hasLabel('__Source__').valueMap('paper_id', 'base_date')"
            )

            paper_ids = []
            seen = set()
            for row in id_rows:
                # valueMap returns each property as a single-element list.
                pid_v = row.get("paper_id")
                bd_v = row.get("base_date")
                pid = pid_v[0] if isinstance(pid_v, list) and pid_v else pid_v
                bd = bd_v[0] if isinstance(bd_v, list) and bd_v else bd_v
                if pid is None or bd is None:
                    continue
                day = str(bd)[:10]
                if start_date <= day <= end_date and pid not in seen:
                    seen.add(pid)
                    paper_ids.append(pid)

            logger.info(
                "Found %d Neptune documents between '%s' and '%s'",
                len(paper_ids),
                start_date,
                end_date,
            )
            return paper_ids
        except Exception as e:
            logger.error("Error finding paper_ids in Neptune: %s", e)
            raise

    def delete_documents_by_date_range(
        self, start_date: str, end_date: str
    ) -> dict[str, Any]:
        paper_ids = self._find_paper_ids_by_date_range(start_date, end_date)
        if not paper_ids:
            return {
                "status": "success",
                "deleted_count": 0,
                "date_range": f"'{start_date}' to '{end_date}'",
            }

        results = self.batch_delete_documents(paper_ids)
        return _summarize_batch_results(
            results, date_range=f"{start_date} to {end_date}"
        )

    def delete_all_documents(self) -> dict[str, Any]:
        try:
            vertex_count = self._submit_query("g.V().count()")[0]
            edge_count = self._submit_query("g.E().count()")[0]
            logger.info("Deleting %d vertices and %d edges.", vertex_count, edge_count)

            self._submit_query("g.E().drop()")
            self._submit_query("g.V().drop()")

            remaining_vertices = self._submit_query("g.V().count()")[0]
            if remaining_vertices == 0:
                logger.info("Successfully deleted all graph data.")
            else:
                logger.warning("%d vertices remain after deletion.", remaining_vertices)

            return {
                "status": "completed",
                "vertices_deleted": vertex_count,
                "edges_deleted": edge_count,
            }
        except Exception as e:
            error_msg = f"Error deleting all documents: {e}"
            logger.error(error_msg)
            return {"status": "error", "error": error_msg}


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
            logger.debug("AWS credentials configured successfully")

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

    def delete_documents_by_date_range(
        self, start_date: str, end_date: str
    ) -> dict[str, Any]:
        if not (_is_valid_date_format(start_date) and _is_valid_date_format(end_date)):
            raise ValueError("Date values must be in 'YYYY-MM-DD' format.")

        if not self._check_index_exists():
            logger.warning("Index '%s' does not exist. Skipping deletion.", self.index)
            return {"status": "skipped", "reason": f"Index '{self.index}' not found."}

        query = {
            "query": {
                "range": {
                    "metadata.source.metadata.base_date": {
                        "gte": start_date,
                        "lte": end_date,
                    }
                }
            }
        }

        try:
            logger.info(
                "Deleting documents from index '%s' between '%s' and '%s'.",
                self.index,
                start_date,
                end_date,
            )
            response = self.client.delete_by_query(index=self.index, body=query)
            logger.info(
                "OpenSearch deletion response for index '%s': %s", self.index, response
            )

            return {
                "status": "success",
                "deleted": response.get("deleted", 0),
                "total": response.get("total", 0),
                "failures": response.get("failures", []),
                "date_range": f"{start_date} to {end_date}",
            }
        except NotFoundError:
            logger.warning("Index '%s' not found during delete operation.", self.index)
            return {"status": "skipped", "reason": f"Index '{self.index}' not found."}
        except Exception as e:
            error_msg = f"Error deleting documents from index '{self.index}': {e}"
            logger.error(error_msg)
            return {"status": "error", "error": error_msg}


def get_ssm_param_value(boto3_session: boto3.Session, param_name: str) -> str:
    ssm_client = boto3_session.client("ssm")
    try:
        response = ssm_client.get_parameter(Name=param_name, WithDecryption=True)
        return response["Parameter"]["Value"]
    except ClientError as error:
        logger.error("Failed to get SSM parameter '%s': %s", param_name, error)
        raise error
