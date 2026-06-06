import re
import time
from typing import Any

import boto3
from botocore.exceptions import ClientError
from gremlin_python.driver import client, serializer
from opensearchpy import NotFoundError, OpenSearch, RequestsHttpConnection
from requests_aws4auth import AWS4Auth

from .logger import logger


class NeptuneClient:
    DEFAULT_PORT: int = 8182
    DEFAULT_PROTOCOL: str = "wss"

    def __init__(self, neptune_endpoint: str):
        if not neptune_endpoint:
            raise ValueError("Neptune endpoint must be provided")
        self.endpoint = neptune_endpoint
        self._client = None

    @property
    def client(self):
        if not self._client:
            try:
                self._client = client.Client(
                    f"{self.DEFAULT_PROTOCOL}://{self.endpoint}:{self.DEFAULT_PORT}/gremlin",
                    "g",
                    message_serializer=serializer.GraphSONSerializersV2d0(),
                )
            except Exception as e:
                logger.error("Failed to initialize Neptune client: %s", str(e))
                raise
        return self._client

    def delete_document(self, paper_id: str) -> dict[str, Any]:
        if not paper_id:
            raise ValueError("'paper_id' must not be empty")

        try:
            # dedup() after EVERY .in() hop. Without it, chained .in().in().in()
            # multiplies duplicate traversal paths and blows Neptune
            # MemoryLimitExceededException even when the distinct node counts are
            # tiny (same failure mode fixed in the cleaner). Per-hop dedup bounds
            # each step to the distinct set.
            connect_queries = {
                "connected_facts": f"""
                g.V().has('__Source__', 'paper_id', '{paper_id}')
                .in('__EXTRACTED_FROM__').dedup().hasLabel('__Chunk__')
                .in('__MENTIONED_IN__').dedup().hasLabel('__Topic__')
                .in('__BELONGS_TO__').dedup().hasLabel('__Statement__')
                .in('__SUPPORTS__').dedup().hasLabel('__Fact__')
                .count()
                """,
                "connected_entities": f"""
                g.V().has('__Source__', 'paper_id', '{paper_id}')
                .in('__EXTRACTED_FROM__').dedup().hasLabel('__Chunk__')
                .in('__MENTIONED_IN__').dedup().hasLabel('__Topic__')
                .in('__BELONGS_TO__').dedup().hasLabel('__Statement__')
                .in('__SUPPORTS__').dedup().hasLabel('__Fact__')
                .union(
                    __.out('__SUBJECT__'),
                    __.out('__OBJECT__')
                ).hasLabel('__Entity__')
                .dedup()
                .count()
                """,
            }

            delete_queries = {
                "entities": f"""
                g.V().has('__Source__', 'paper_id', '{paper_id}')
                .in('__EXTRACTED_FROM__').dedup().hasLabel('__Chunk__')
                .in('__MENTIONED_IN__').dedup().hasLabel('__Topic__')
                .in('__BELONGS_TO__').dedup().hasLabel('__Statement__')
                .in('__SUPPORTS__').dedup().hasLabel('__Fact__')
                .filter(
                    __.out('__SUPPORTS__')
                    .filter(
                        __.in('__BELONGS_TO__')
                        .in('__MENTIONED_IN__')
                        .in('__EXTRACTED_FROM__')
                        .has('__Source__', 'paper_id', '{paper_id}')
                    )
                    .count()
                    .is(__.out('__SUPPORTS__').count())
                )
                .union(
                    __.out('__SUBJECT__'),
                    __.out('__OBJECT__')
                ).hasLabel('__Entity__')
                .filter(
                    __.in('__SUBJECT__', '__OBJECT__')
                    .count()
                    .is(1)
                )
                .dedup()
                .drop()
                """,
                "facts": f"""
                g.V().has('__Source__', 'paper_id', '{paper_id}')
                .in('__EXTRACTED_FROM__').dedup().hasLabel('__Chunk__')
                .in('__MENTIONED_IN__').dedup().hasLabel('__Topic__')
                .in('__BELONGS_TO__').dedup().hasLabel('__Statement__')
                .in('__SUPPORTS__').dedup().hasLabel('__Fact__')
                .filter(
                    __.out('__SUPPORTS__')
                    .filter(
                        __.in('__BELONGS_TO__')
                        .in('__MENTIONED_IN__')
                        .in('__EXTRACTED_FROM__')
                        .has('__Source__', 'paper_id', '{paper_id}')
                    )
                    .count()
                    .is(__.out('__SUPPORTS__').count())
                )
                .dedup()
                .drop()
                """,
                "statements": f"""
                g.V().has('__Source__', 'paper_id', '{paper_id}')
                .in('__EXTRACTED_FROM__').dedup().hasLabel('__Chunk__')
                .in('__MENTIONED_IN__').dedup().hasLabel('__Topic__')
                .in('__BELONGS_TO__').dedup().hasLabel('__Statement__')
                .drop()
                """,
                "topics": f"""
                g.V().has('__Source__', 'paper_id', '{paper_id}')
                .in('__EXTRACTED_FROM__').dedup().hasLabel('__Chunk__')
                .in('__MENTIONED_IN__').dedup().hasLabel('__Topic__')
                .drop()
                """,
                "chunks": f"""
                g.V().has('__Source__', 'paper_id', '{paper_id}')
                .in('__EXTRACTED_FROM__').dedup().hasLabel('__Chunk__')
                .drop()
                """,
                "source": f"""
                g.V().has('__Source__', 'paper_id', '{paper_id}').drop()
                """,
            }

            connection_stats = {}
            for name, query in connect_queries.items():
                try:
                    result = self.client.submit(query).all().result()
                    count_value = result[0] if result else 0
                    connection_stats[name] = count_value
                    logger.debug("%s: %d", name, count_value)
                except Exception as e:
                    logger.warning("Failed to get %s: %s", name, str(e))
                    connection_stats[name] = "Error"

            results = {}
            execution_order = [
                "entities",
                "facts",
                "statements",
                "topics",
                "chunks",
                "source",
            ]

            for entity_type in execution_order:
                delete_query = delete_queries[entity_type]
                # Run drop() directly (no count()-then-drop() pair): the pair ran
                # each heavy multi-hop traversal twice, doubling the load. Wrap
                # each stage in its own try/except so this stays best-effort: a
                # single stage that trips Neptune's intermittent
                # MemoryLimitExceededException no longer aborts the whole cleanup
                # (which only runs to clear a prior version before re-indexing).
                try:
                    self.client.submit(delete_query).all().result()
                    results[entity_type] = "dropped"
                except Exception as e:
                    logger.warning(
                        "Failed to drop '%s' for paper_id '%s': %s",
                        entity_type,
                        paper_id,
                        str(e),
                    )
                    results[entity_type] = "error"

            if all(v == "error" for v in results.values()):
                logger.warning(
                    "All delete stages errored for 'paper_id': '%s'", paper_id
                )

            logger.debug(
                "Completed delete for 'paper_id': '%s' -> %s", paper_id, results
            )

            return {
                "status": "success",
                "paper_id": paper_id,
                "deleted": results,
                # connection_stats are diagnostic counts of how many fact/entity
                # nodes were connected before deletion ("Error" string if that
                # probe query itself failed); surfaced as-is for observability.
                "connected": connection_stats,
            }

        except Exception as e:
            logger.error("Error deleting document '%s': %s", paper_id, str(e))
            raise

    def batch_delete_documents(self, paper_ids: list[str]) -> list[dict[str, Any]]:
        if not paper_ids:
            logger.warning("No 'paper_id's provided for batch deletion")
            return []

        results = []
        for paper_id in paper_ids:
            try:
                result = self.delete_document(paper_id)
                results.append(result)
            except Exception as e:
                logger.error("Failed to delete document '%s': %s", paper_id, str(e))
                results.append(
                    {"status": "error", "paper_id": paper_id, "error": str(e)}
                )

        return results

    def delete_documents_by_date(self, base_date: str) -> dict[str, Any]:
        if not self._is_valid_date_format(base_date):
            raise ValueError("'base_date' must be in the format 'YYYY-MM-DD'")

        paper_ids = self._find_paper_ids_by_date(base_date)

        if not paper_ids:
            logger.warning("No documents found with 'base_date': '%s'", base_date)
            return {"status": "success", "base_date": base_date, "deleted": 0}

        results = self.batch_delete_documents(paper_ids)
        return self.summarize_deletion_results(results, base_date=base_date)

    def _find_paper_ids_by_date(self, base_date: str) -> list[str]:
        if not self._is_valid_date_format(base_date):
            raise ValueError("'base_date' must be in the format 'YYYY-MM-DD'")

        try:
            # base_date is stored as a datetime (the build Cypher uses
            # datetime(...)), so a string equality has('base_date', '2026-06-03')
            # never matches. Pull (paper_id, base_date) per __Source__ and compare
            # the YYYY-MM-DD prefix in Python. __Source__ is one-per-paper (small).
            query = (
                "g.V().hasLabel('__Source__')"
                ".valueMap('paper_id', 'base_date')"
            )
            rows = self.client.submit(query).all().result()

            paper_ids = []
            seen = set()
            for row in rows:
                pid_v = row.get("paper_id")
                bd_v = row.get("base_date")
                pid = pid_v[0] if isinstance(pid_v, list) and pid_v else pid_v
                bd = bd_v[0] if isinstance(bd_v, list) and bd_v else bd_v
                if pid is None or bd is None:
                    continue
                if str(bd)[:10] == base_date and pid not in seen:
                    seen.add(pid)
                    paper_ids.append(pid)

            logger.info(
                "Found %d documents with 'base_date': %s", len(paper_ids), base_date
            )
            return paper_ids
        except Exception as e:
            logger.error(
                "Error finding 'paper_id's with 'base_date' '%s': %s",
                base_date,
                str(e),
            )
            raise

    def delete_documents_by_date_range(
        self, start_date: str, end_date: str
    ) -> dict[str, Any]:
        if not (
            self._is_valid_date_format(start_date)
            and self._is_valid_date_format(end_date)
        ):
            raise ValueError("Date values must be in the format 'YYYY-MM-DD'")

        paper_ids = self._find_paper_ids_by_date_range(start_date, end_date)

        if not paper_ids:
            logger.warning(
                "No documents found between '%s' and '%s'", start_date, end_date
            )
            return {
                "status": "success",
                "date_range": f"{start_date} to {end_date}",
                "deleted_count": 0,
            }

        results = self.batch_delete_documents(paper_ids)
        return self.summarize_deletion_results(
            results, date_range=f"{start_date} to {end_date}"
        )

    def _find_paper_ids_by_date_range(
        self, start_date: str, end_date: str
    ) -> list[str]:
        if not (
            self._is_valid_date_format(start_date)
            and self._is_valid_date_format(end_date)
        ):
            raise ValueError("Date values must be in the format 'YYYY-MM-DD'")

        try:
            query = f"""
            g.V().hasLabel('__Source__')
              .has('base_date', between('{start_date}', '{end_date}'))
              .values('paper_id')
              .dedup()
              .fold()
            """

            result = self.client.submit(query).all().result()
            paper_ids = result[0] if result else []
            logger.info(
                "Found %d documents between '%s' and '%s'",
                len(paper_ids),
                start_date,
                end_date,
            )

            return paper_ids
        except Exception as e:
            logger.error(
                "Error finding 'paper_id's between '%s' and '%s': %s",
                start_date,
                end_date,
                str(e),
            )
            raise

    @staticmethod
    def summarize_deletion_results(
        results: list[dict[str, Any]], **context: Any
    ) -> dict[str, Any]:
        success_count = sum(
            1 for result in results if result.get("status") == "success"
        )
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

    @staticmethod
    def _is_valid_date_format(date_str: str) -> bool:
        if not date_str:
            return False
        pattern = r"^\d{4}-\d{2}-\d{2}$"
        return bool(re.match(pattern, date_str))

    def delete_all_documents(self) -> dict[str, Any]:
        try:
            vertex_query = "g.V().count()"
            edge_query = "g.E().count()"
            drop_query = "g.V().drop().iterate(); g.E().drop().iterate()"

            vertex_count = self.client.submit(vertex_query).all().result()[0]
            edge_count = self.client.submit(edge_query).all().result()[0]
            total_count = vertex_count + edge_count

            logger.info(
                "Found %d vertices and %d edges to delete", vertex_count, edge_count
            )

            self.client.submit(drop_query).all().result()

            remaining_count = self.client.submit(vertex_query).all().result()[0]

            return {
                "status": "completed",
                "total_documents": total_count,
                "deleted_count": total_count - remaining_count,
                "vertices_deleted": vertex_count,
                "edges_deleted": edge_count,
            }

        except Exception as e:
            error_msg = f"Error deleting all documents: {str(e)}"
            logger.error(error_msg)
            return {
                "status": "error",
                "error": error_msg,
                "total_documents": 0,
                "deleted_count": 0,
                "error_count": 0,
            }


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
            raise ValueError("All OpenSearch connection parameters must be provided")

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
            self.index = index
        except Exception as e:
            logger.error("Failed to initialize OpenSearch client: %s", str(e))
            raise

    def delete_document(self, paper_id: str) -> dict[str, Any]:
        if not paper_id:
            raise ValueError("'paper_id' must not be empty")

        try:
            search_query = {
                "query": {"match": {"metadata.source.metadata.paper_id": paper_id}}
            }

            result = self.client.search(
                index=self.index, body=search_query, params={"_source": False}
            )

            doc_ids = [hit["_id"] for hit in result["hits"]["hits"]]

            if not doc_ids:
                logger.warning("No documents found with 'paper_id': '%s'", paper_id)
                return {"status": "success", "paper_id": paper_id, "deleted": 0}

            deleted_count = 0
            for doc_id in doc_ids:
                try:
                    self.client.delete(index=self.index, id=doc_id)
                    deleted_count += 1
                    logger.debug("Deleted document '%s'", doc_id)
                except Exception as delete_err:
                    logger.warning(
                        "Failed to delete document '%s': %s", doc_id, str(delete_err)
                    )

            logger.debug(
                "Successfully deleted documents with 'paper_id': '%s'", paper_id
            )

            return {
                "status": "success",
                "paper_id": paper_id,
                "deleted": deleted_count,
                "total": len(doc_ids),
            }

        except Exception as e:
            logger.error(
                "Error deleting documents with 'paper_id': '%s': %s",
                paper_id,
                str(e),
            )
            return {
                "status": "error",
                "paper_id": paper_id,
                "error": str(e),
            }

    def batch_delete_documents(self, paper_ids: list[str]) -> list[dict[str, Any]]:
        if not paper_ids:
            logger.warning("No 'paper_id's provided for batch deletion")
            return []

        results = []
        for paper_id in paper_ids:
            try:
                result = self.delete_document(paper_id)
                results.append(result)
            except Exception as e:
                logger.error("Error deleting document '%s': %s", paper_id, str(e))
                results.append(
                    {"status": "error", "paper_id": paper_id, "error": str(e)}
                )

        return results

    @staticmethod
    def summarize_deletion_results(
        results: list[dict[str, Any]], **context: Any
    ) -> dict[str, Any]:
        success_count = sum(
            1 for result in results if result.get("status") == "success"
        )
        error_count = len(results) - success_count

        summary = {
            "status": "completed",
            "total_documents": len(results),
            "deleted_count": success_count,
            "error_count": error_count,
            "details": results,
        }

        summary.update(context)

        return summary

    def delete_documents_by_date(self, base_date: str) -> dict[str, Any]:
        if not self._is_valid_date_format(base_date):
            raise ValueError("'base_date' must be in the format 'YYYY-MM-DD'")

        paper_ids = self._find_paper_ids_by_date(base_date)

        if not paper_ids:
            logger.warning("No documents found with 'base_date': '%s'", base_date)
            return {
                "status": "success",
                "deleted": 0,
                "total": 0,
                "failures": [],
                "base_date": base_date,
            }

        results = self.batch_delete_documents(paper_ids)
        return self.summarize_deletion_results(results, base_date=base_date)

    def _find_paper_ids_by_date(self, base_date: str) -> list[str]:
        if not self._is_valid_date_format(base_date):
            raise ValueError("'base_date' must be in the format 'YYYY-MM-DD'")

        try:
            query = {
                "query": {"match": {"metadata.source.metadata.base_date": base_date}}
            }
            return self._get_paper_ids_from_query(query)
        except Exception as e:
            logger.error(
                "Error finding 'paper_id's with 'base_date': '%s': %s",
                base_date,
                str(e),
            )
            raise

    def _get_paper_ids_from_query(self, query: dict[str, Any]) -> list[str]:
        if not self._check_index_exists():
            logger.warning("Index '%s' does not exist", self.index)
            return []

        try:
            result = self.client.search(
                index=self.index,
                body=query,
                params={
                    "_source": ["metadata.source.metadata.paper_id"],
                    "size": 10000,
                },
            )

            hits = result["hits"]["hits"]
            if not hits:
                return []

            paper_ids = []
            for hit in hits:
                try:
                    paper_id = hit["_source"]["metadata"]["source"]["metadata"][
                        "paper_id"
                    ]
                    paper_ids.append(paper_id)
                except KeyError:
                    logger.warning("Document '%s' missing 'paper_id' field", hit["_id"])

            return list(set(paper_ids))

        except NotFoundError:
            logger.warning("Index '%s' not found", self.index)
            return []
        except Exception as e:
            logger.error("Error retrieving 'paper_id's: %s", str(e))
            raise

    def _check_index_exists(self) -> bool:
        try:
            return self.client.indices.exists(index=self.index)
        except Exception as e:
            logger.error("Error checking if index exists: %s", str(e))
            return False

    def delete_documents_by_date_range(
        self, start_date: str, end_date: str
    ) -> dict[str, Any]:
        if not (
            self._is_valid_date_format(start_date)
            and self._is_valid_date_format(end_date)
        ):
            raise ValueError("Date values must be in the format 'YYYY-MM-DD'")

        paper_ids = self._find_paper_ids_by_date_range(start_date, end_date)

        if not paper_ids:
            logger.info("No documents found between '%s' to '%s'", start_date, end_date)
            return {
                "status": "success",
                "deleted": 0,
                "total": 0,
                "failures": [],
                "date_range": f"{start_date} to {end_date}",
            }

        results = self.batch_delete_documents(paper_ids)
        return self.summarize_deletion_results(
            results, date_range=f"{start_date} to {end_date}"
        )

    def _find_paper_ids_by_date_range(
        self, start_date: str, end_date: str
    ) -> list[str]:
        if not (
            self._is_valid_date_format(start_date)
            and self._is_valid_date_format(end_date)
        ):
            raise ValueError("Date values must be in the format 'YYYY-MM-DD'")

        logger.info("Finding 'paper_id's between '%s' and '%s'", start_date, end_date)

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

        return self._get_paper_ids_from_query(query)

    @staticmethod
    def _is_valid_date_format(date_str: str) -> bool:
        if not date_str:
            return False
        pattern = r"^\d{4}-\d{2}-\d{2}$"
        return bool(re.match(pattern, date_str))


def get_account_id(boto3_session: boto3.Session) -> str:
    try:
        sts_client = boto3_session.client("sts")
        return sts_client.get_caller_identity()["Account"]
    except ClientError as e:
        logger.error("Failed to get account ID: '%s'", str(e))
        raise


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
        logger.error("Error checking cross-inference support: %s", str(e))

    return model_id


def get_ssm_param_value(boto3_session: boto3.Session, param_name: str) -> str:
    ssm_client = boto3_session.client("ssm")
    try:
        response = ssm_client.get_parameter(Name=param_name, WithDecryption=True)
        return response["Parameter"]["Value"]
    except ClientError as error:
        logger.error("Failed to get SSM parameter value: %s", str(error))
        raise error


def submit_batch_job(
    boto3_session: boto3.Session,
    job_name: str,
    job_queue_name: str,
    job_definition_name: str,
    parameters: dict[str, str] | None = None,
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
