import re
from typing import Any, Dict, List, Optional
import boto3
from botocore.exceptions import ClientError
from gremlin_python.driver import client, serializer
from opensearchpy import OpenSearch, RequestsHttpConnection, NotFoundError
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
                logger.error(f"Failed to initialize Neptune client: {str(e)}")
                raise
        return self._client

    def delete_document(self, paper_id: str) -> Dict[str, Any]:
        if not paper_id:
            raise ValueError("'paper_id' must not be empty")

        try:
            connect_queries = {
                "connected_facts": f"""
                g.V().has('__Source__', 'paper_id', '{paper_id}')
                .in('__EXTRACTED_FROM__').hasLabel('__Chunk__')
                .in('__MENTIONED_IN__').hasLabel('__Topic__')
                .in('__BELONGS_TO__').hasLabel('__Statement__')
                .in('__SUPPORTS__').hasLabel('__Fact__')
                .dedup()
                .count()
                """,
                "connected_entities": f"""
                g.V().has('__Source__', 'paper_id', '{paper_id}')
                .in('__EXTRACTED_FROM__').hasLabel('__Chunk__')
                .in('__MENTIONED_IN__').hasLabel('__Topic__')
                .in('__BELONGS_TO__').hasLabel('__Statement__')
                .in('__SUPPORTS__').hasLabel('__Fact__')
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
                .in('__EXTRACTED_FROM__').hasLabel('__Chunk__')
                .in('__MENTIONED_IN__').hasLabel('__Topic__')
                .in('__BELONGS_TO__').hasLabel('__Statement__')
                .dedup()
                .in('__SUPPORTS__').hasLabel('__Fact__')
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
                .in('__EXTRACTED_FROM__').hasLabel('__Chunk__')
                .in('__MENTIONED_IN__').hasLabel('__Topic__')
                .in('__BELONGS_TO__').hasLabel('__Statement__')
                .dedup()
                .in('__SUPPORTS__').hasLabel('__Fact__')
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
                .in('__EXTRACTED_FROM__').hasLabel('__Chunk__')
                .in('__MENTIONED_IN__').hasLabel('__Topic__')
                .in('__BELONGS_TO__').hasLabel('__Statement__')
                .dedup()
                .drop()
                """,
                "topics": f"""
                g.V().has('__Source__', 'paper_id', '{paper_id}')
                .in('__EXTRACTED_FROM__').hasLabel('__Chunk__')
                .in('__MENTIONED_IN__').hasLabel('__Topic__')
                .drop()
                """,
                "chunks": f"""
                g.V().has('__Source__', 'paper_id', '{paper_id}')
                .in('__EXTRACTED_FROM__').hasLabel('__Chunk__')
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
                    logger.debug(f"{name}: {count_value}")
                except Exception as e:
                    logger.warning(f"Failed to get {name}: {str(e)}")
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
                count_query = delete_query.replace(".drop()", ".count()")
                count = self.client.submit(count_query).all().result()
                count_value = count[0] if count else 0

                if count_value > 0:
                    self.client.submit(delete_query).all().result()
                    results[entity_type] = count_value
                else:
                    results[entity_type] = 0

                logger.debug(
                    f"Deleted {count_value} {entity_type} for 'paper_id': '{paper_id}'"
                )

            if all(count == 0 for count in results.values()):
                logger.warning(f"No data found for 'paper_id': '{paper_id}'")

            logger.debug(f"Successfully deleted document with 'paper_id': '{paper_id}'")

            return {
                "status": "success",
                "paper_id": paper_id,
                "deleted": results,
                "shared_nodes": {
                    "facts": connection_stats.get("connected_facts", 0)
                    - results.get("facts", 0),
                    "entities": connection_stats.get("connected_entities", 0)
                    - results.get("entities", 0),
                },
            }

        except Exception as e:
            logger.error(f"Error deleting document '{paper_id}': {str(e)}")
            raise

    def batch_delete_documents(self, paper_ids: List[str]) -> List[Dict[str, Any]]:
        if not paper_ids:
            logger.warning("No 'paper_id's provided for batch deletion")
            return []

        results = []
        for paper_id in paper_ids:
            try:
                result = self.delete_document(paper_id)
                results.append(result)
            except Exception as e:
                logger.error(f"Failed to delete document '{paper_id}': {str(e)}")
                results.append(
                    {"status": "error", "paper_id": paper_id, "error": str(e)}
                )

        return results

    def delete_documents_by_date(self, base_date: str) -> Dict[str, Any]:
        if not self._is_valid_date_format(base_date):
            raise ValueError("'base_date' must be in the format 'YYYY-MM-DD'")

        paper_ids = self._find_paper_ids_by_date(base_date)

        if not paper_ids:
            logger.warning(f"No documents found with 'base_date': '{base_date}'")
            return {"status": "success", "base_date": base_date, "deleted": 0}

        results = self.batch_delete_documents(paper_ids)
        return self.summarize_deletion_results(results, base_date=base_date)

    def _find_paper_ids_by_date(self, base_date: str) -> List[str]:
        if not self._is_valid_date_format(base_date):
            raise ValueError("'base_date' must be in the format 'YYYY-MM-DD'")

        try:
            query = f"""
            g.V().hasLabel('__Source__')
              .has('base_date', '{base_date}')
              .values('paper_id')
              .dedup()
              .fold()
            """

            result = self.client.submit(query).all().result()
            paper_ids = result[0] if result else []
            logger.info(
                f"Found {len(paper_ids)} documents with 'base_date': {base_date}"
            )

            return paper_ids
        except Exception as e:
            logger.error(
                f"Error finding 'paper_id's with 'base_date' '{base_date}': {str(e)}"
            )
            raise

    def delete_documents_by_date_range(
        self, start_date: str, end_date: str
    ) -> Dict[str, Any]:
        if not (
            self._is_valid_date_format(start_date)
            and self._is_valid_date_format(end_date)
        ):
            raise ValueError("Date values must be in the format 'YYYY-MM-DD'")

        paper_ids = self._find_paper_ids_by_date_range(start_date, end_date)

        if not paper_ids:
            logger.warning(
                f"No documents found between '{start_date}' and '{end_date}'"
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
    ) -> List[str]:
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
                f"Found {len(paper_ids)} documents between '{start_date}' and '{end_date}'"
            )

            return paper_ids
        except Exception as e:
            logger.error(
                f"Error finding 'paper_id's between '{start_date}' and '{end_date}': {str(e)}"
            )
            raise

    @staticmethod
    def summarize_deletion_results(
        results: List[Dict[str, Any]], **context: Any
    ) -> Dict[str, Any]:
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

    @staticmethod
    def _is_valid_date_format(date_str: str) -> bool:
        if not date_str:
            return False
        pattern = r"^\d{4}-\d{2}-\d{2}$"
        return bool(re.match(pattern, date_str))

    def delete_all_documents(self) -> Dict[str, Any]:
        try:
            vertex_query = "g.V().count()"
            edge_query = "g.E().count()"
            drop_query = "g.V().drop().iterate(); g.E().drop().iterate()"

            vertex_count = self.client.submit(vertex_query).all().result()[0]
            edge_count = self.client.submit(edge_query).all().result()[0]
            total_count = vertex_count + edge_count

            logger.info(
                f"Found {vertex_count} vertices and {edge_count} edges to delete"
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
        region_name: str,
        boto3_session: boto3.Session,
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
            self.region_name = region_name
            self.boto3_session = boto3_session
        except Exception as e:
            logger.error(f"Failed to initialize OpenSearch client: {str(e)}")
            raise

    def delete_document(self, paper_id: str) -> Dict[str, Any]:
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
                logger.warning(f"No documents found with 'paper_id': '{paper_id}'")
                return {"status": "success", "paper_id": paper_id, "deleted": 0}

            deleted_count = 0
            for doc_id in doc_ids:
                try:
                    self.client.delete(index=self.index, id=doc_id)
                    deleted_count += 1
                    logger.debug(f"Deleted document '{doc_id}'")
                except Exception as delete_err:
                    logger.warning(
                        f"Failed to delete document '{doc_id}': {str(delete_err)}"
                    )

            logger.debug(f"Successfully deleted documents with 'paper_id': {paper_id}")

            return {
                "status": "success",
                "paper_id": paper_id,
                "deleted": deleted_count,
                "total": len(doc_ids),
            }

        except Exception as e:
            logger.error(
                f"Error deleting documents with 'paper_id': {paper_id}: {str(e)}"
            )
            return {
                "status": "error",
                "paper_id": paper_id,
                "error": str(e),
            }

    def batch_delete_documents(self, paper_ids: List[str]) -> List[Dict[str, Any]]:
        if not paper_ids:
            logger.warning("No 'paper_id's provided for batch deletion")
            return []

        results = []
        for paper_id in paper_ids:
            try:
                result = self.delete_document(paper_id)
                results.append(result)
            except Exception as e:
                logger.error(f"Error deleting document '{paper_id}': {str(e)}")
                results.append(
                    {"status": "error", "paper_id": paper_id, "error": str(e)}
                )

        return results

    @staticmethod
    def summarize_deletion_results(
        results: List[Dict[str, Any]], **context: Any
    ) -> Dict[str, Any]:
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

    def delete_documents_by_date(self, base_date: str) -> Dict[str, Any]:
        if not self._is_valid_date_format(base_date):
            raise ValueError("'base_date' must be in the format 'YYYY-MM-DD'")

        paper_ids = self._find_paper_ids_by_date(base_date)

        if not paper_ids:
            logger.warning(f"No documents found with 'base_date' '{base_date}'")
            return {
                "status": "success",
                "deleted": 0,
                "total": 0,
                "failures": [],
                "base_date": base_date,
            }

        results = self.batch_delete_documents(paper_ids)
        return self.summarize_deletion_results(results, base_date=base_date)

    def _find_paper_ids_by_date(self, base_date: str) -> List[str]:
        if not self._is_valid_date_format(base_date):
            raise ValueError("'base_date' must be in the format 'YYYY-MM-DD'")

        try:
            query = {
                "query": {"match": {"metadata.source.metadata.base_date": base_date}}
            }
            return self._get_paper_ids_from_query(query)
        except Exception as e:
            logger.error(
                f"Error finding 'paper_id's with 'base_date': {base_date}: {str(e)}"
            )
            raise

    def _get_paper_ids_from_query(self, query: Dict[str, Any]) -> List[str]:
        if not self._check_index_exists():
            logger.warning(f"Index '{self.index}' does not exist")
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
                    logger.warning(f"Document '{hit['_id']}' missing 'paper_id' field")

            return list(set(paper_ids))

        except NotFoundError:
            logger.warning(f"Index '{self.index}' not found")
            return []
        except Exception as e:
            logger.error(f"Error retrieving 'paper_id's: {str(e)}")
            raise

    def _check_index_exists(self) -> bool:
        try:
            return self.client.indices.exists(index=self.index)
        except Exception as e:
            logger.error(f"Error checking if index exists: {str(e)}")
            return False

    def delete_documents_by_date_range(
        self, start_date: str, end_date: str
    ) -> Dict[str, Any]:
        if not (
            self._is_valid_date_format(start_date)
            and self._is_valid_date_format(end_date)
        ):
            raise ValueError("Date values must be in the format 'YYYY-MM-DD'")

        paper_ids = self._find_paper_ids_by_date_range(start_date, end_date)

        if not paper_ids:
            logger.info(f"No documents found between '{start_date}' to '{end_date}'")
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
    ) -> List[str]:
        if not (
            self._is_valid_date_format(start_date)
            and self._is_valid_date_format(end_date)
        ):
            raise ValueError("Date values must be in the format 'YYYY-MM-DD'")

        logger.info(f"Finding 'paper_id's between {start_date} and {end_date}")

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
