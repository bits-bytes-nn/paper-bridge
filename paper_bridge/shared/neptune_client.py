"""Shared Neptune (Gremlin) client for the lexical graph.

Single implementation used by both the indexer (re-index cleanup) and the
cleaner (scheduled date-range deletion). It supersedes the two divergent copies
that previously lived in ``indexer/src/aws_helpers.py`` and
``cleaner/src/aws_helpers.py``; this one keeps the memory-safe behaviour that was
hard-won in the cleaner:

- two-phase delete (collect ids with per-hop ``dedup`` -> drop by id in batches),
  which stays under the Neptune Serverless memory limit where the old single-shot
  multi-hop ``.drop()`` blew ``MemoryLimitExceededException``;
- strict ``paper_id`` validation, since the id is inlined into Gremlin (this
  endpoint rejects bindings);
- date filtering in Python over a streamed ``valueMap`` instead of a server-side
  ``between(...)`` predicate that full-scans an un-indexed property.
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any

from gremlin_python.driver import client, serializer

from .graph_schema import Edge, Vertex

# Use a module logger that inherits the handlers/level configured by whichever
# app (indexer/cleaner) imports this; avoids each app having to inject its own.
logger = logging.getLogger(__name__)

# Neptune Serverless can transiently raise MemoryLimitExceededException under a
# burst of queries (it has not scaled up its NCU yet). Retrying with backoff
# gives it time to scale — the same query then succeeds.
_MEM_LIMIT_MARKER = "MemoryLimitExceededException"
_MEM_RETRY_MAX = 6
_MEM_RETRY_BASE_DELAY = 3  # seconds; exponential: 3, 6, 12, 24, 48, ...

# A paper_id is inlined into Gremlin (bindings unsupported on this endpoint), so
# it must be validated to a safe character set to prevent query injection.
_PAPER_ID_RE = re.compile(r"^[a-zA-Z0-9_.:-]+$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _is_valid_date_format(date_str: str) -> bool:
    return bool(date_str) and bool(_DATE_RE.match(date_str))


def summarize_deletion_results(
    results: list[dict[str, Any]], **context: Any
) -> dict[str, Any]:
    """Aggregate per-paper deletion results into a single summary dict."""
    success_count = sum(1 for r in results if r.get("status") == "success")
    summary: dict[str, Any] = {
        "status": "completed",
        "total_documents": len(results),
        "success_count": success_count,
        "error_count": len(results) - success_count,
        "details": results,
    }
    summary.update(context)
    return summary


class NeptuneClient:
    DEFAULT_PORT: int = 8182
    DEFAULT_PROTOCOL: str = "wss"

    # Drop at most this many vertices per query: a small, index-backed
    # g.V(id...).drop() stays well under the Neptune Serverless memory limit.
    _DROP_BATCH_SIZE = 50

    def __init__(self, neptune_endpoint: str):
        if not neptune_endpoint:
            raise ValueError("Neptune endpoint must be provided.")
        self.endpoint = neptune_endpoint
        self._gremlin_client: client.Client | None = None
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
        self,
        query: str,
        bindings: dict[str, Any] | None = None,
        sleep=time.sleep,
    ) -> list[Any]:
        """Submit a Gremlin query, retrying on transient memory-limit errors.

        Neptune Serverless can raise MemoryLimitExceededException during a burst
        (before it scales NCU up). We retry with exponential backoff so the same
        query succeeds once capacity is available; other errors propagate
        immediately. ``sleep`` is injectable for tests.
        """
        for attempt in range(_MEM_RETRY_MAX):
            try:
                return self.client.submit(query, bindings=bindings).all().result()
            except Exception as e:
                if _MEM_LIMIT_MARKER not in str(e) or attempt == _MEM_RETRY_MAX - 1:
                    raise
                delay = _MEM_RETRY_BASE_DELAY * (2**attempt)
                logger.warning(
                    "Neptune memory limit hit (attempt %d/%d); retrying in %ds",
                    attempt + 1,
                    _MEM_RETRY_MAX,
                    delay,
                )
                sleep(delay)
        # Unreachable: the loop either returns or raises.
        raise RuntimeError("unreachable")

    def _drop_vertices_by_id(self, vertex_ids: list[Any]) -> None:
        """Drop vertices by id in small batches (memory-safe, index-backed)."""
        for i in range(0, len(vertex_ids), self._DROP_BATCH_SIZE):
            batch = vertex_ids[i : i + self._DROP_BATCH_SIZE]
            # Neptune string ids; quote each and inline (bindings unsupported).
            id_args = ",".join("'" + str(v).replace("'", "\\'") + "'" for v in batch)
            self._submit_query(f"g.V({id_args}).drop()")

    def delete_document(self, paper_id: str) -> dict[str, Any]:
        if not paper_id:
            raise ValueError("'paper_id' must not be empty.")
        if not _PAPER_ID_RE.match(paper_id):
            raise ValueError(f"Invalid 'paper_id' format: {paper_id}")

        src = Vertex.SOURCE.value
        base_query = f"g.V().has('{src}', 'paper_id', '{paper_id}')"

        try:
            # Two-phase deletion to stay within Neptune Serverless memory limits.
            #
            # The previous single-shot traversals (four chained .in() hops
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
            # must survive) is enforced in phase 1 with the count()==1 guard,
            # evaluated while collecting ids rather than during the drop.
            #
            # CRITICAL: dedup() after EVERY .in() hop. Chained .in().in().in()
            # without intermediate dedup multiplies duplicate traversal paths and
            # blows MemoryLimitExceededException even though the distinct node
            # counts are tiny. Per-hop dedup keeps each step bounded to the
            # distinct set. (Verified via diagnostics: the un-deduped chain OOMs at
            # 32 NCU; the per-hop-deduped chain returns instantly.)
            ef = Edge.EXTRACTED_FROM.value
            mi = Edge.MENTIONED_IN.value
            bt = Edge.BELONGS_TO.value
            sup = Edge.SUPPORTS.value
            subj = Edge.SUBJECT.value
            obj = Edge.OBJECT.value
            stmt = Vertex.STATEMENT.value
            topic = Vertex.TOPIC.value
            fact = Vertex.FACT.value
            entity = Vertex.ENTITY.value

            # Graph schema (edge direction is out -> in), verified against the
            # live graph:
            #   Chunk     -EXTRACTED_FROM-> Source     (chunk belongs to 1 source)
            #   Statement -MENTIONED_IN->   Chunk      (stmt extracted from a chunk)
            #   Statement -BELONGS_TO->     Topic      (topic shared by many stmts)
            #   Fact      -SUPPORTS->       Statement  (fact may support many stmts)
            #   Entity    -SUBJECT/OBJECT-> ...        (entity shared by many facts)
            #
            # So from a Source we walk UP the in-edges: source.in(EXTRACTED_FROM)
            # = chunks; chunk.in(MENTIONED_IN) = statements; then statements fan
            # out to topics (out BELONGS_TO) and up to facts (in SUPPORTS), and
            # facts out to entities. chunks + statements are source-owned (1:1);
            # topics/facts/entities are shared, so each is deleted only when its
            # sole inbound owner is a statement/fact of THIS source (count()==1).
            chunks = f"{base_query}.in('{ef}').dedup()"
            statements = f"{chunks}.in('{mi}').dedup().hasLabel('{stmt}')"
            collect_queries = {
                "chunks": f"{chunks}.id().fold()",
                "statements": f"{statements}.id().fold()",
                "topics": f"""
                    {statements}.out('{bt}').dedup().hasLabel('{topic}')
                    .where(in('{bt}').count().is(1)).id().fold()
                """,
                "facts": f"""
                    {statements}.in('{sup}').dedup().hasLabel('{fact}')
                    .where(out('{sup}').count().is(1)).id().fold()
                """,
                "entities": f"""
                    {statements}.in('{sup}').dedup().hasLabel('{fact}')
                    .union(out('{subj}'), out('{obj}')).dedup().hasLabel('{entity}')
                    .where(in('{subj}', '{obj}').count().is(1)).id().fold()
                """,
            }

            # Best-effort per stage: a stage that exhausts its memory-limit
            # retries must not abort the others. Otherwise one OOM-prone stage
            # leaves the rest of this paper's subgraph behind AND the caller
            # rebuilds on top of it, compounding orphans. Each stage records its
            # deleted count or an "error" marker; the source vertex is always
            # attempted last so the paper at least stops being discoverable.
            deleted: dict[str, Any] = {}
            errors: list[str] = []
            for name, query in collect_queries.items():
                try:
                    logger.info(
                        "Collecting '%s' ids for paper_id '%s'", name, paper_id
                    )
                    result = self._submit_query(query)
                    ids = result[0] if result and result[0] else []
                    logger.info("Collected %d '%s' ids; dropping", len(ids), name)
                    self._drop_vertices_by_id(ids)
                    deleted[name] = len(ids)
                    logger.info(
                        "Deleted %d '%s' for paper_id: '%s'", len(ids), name, paper_id
                    )
                except Exception as e:
                    logger.error(
                        "Failed to delete '%s' for paper_id '%s': %s", name, paper_id, e
                    )
                    deleted[name] = "error"
                    errors.append(name)

            # The source vertex itself is single and cheap to drop directly.
            try:
                self._submit_query(f"{base_query}.drop()")
                deleted["source"] = 1
            except Exception as e:
                logger.error("Failed to drop source for '%s': %s", paper_id, e)
                deleted["source"] = "error"
                errors.append("source")

            return {
                "status": "error" if errors else "success",
                "paper_id": paper_id,
                "deleted_nodes": deleted,
                **({"failed_stages": errors} if errors else {}),
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
                results.append(self.delete_document(paper_id))
            except Exception as e:
                logger.error("Failed to delete document '%s': %s", paper_id, e)
                results.append(
                    {"status": "error", "paper_id": paper_id, "error": str(e)}
                )
        return results

    def _find_paper_ids_in_range(
        self, start_date: str, end_date: str
    ) -> list[str]:
        """Return paper_ids whose base_date falls in [start_date, end_date].

        Enumerates __Source__ vertices and filters by date in Python rather than
        with a server-side between(datetime(...)) predicate: on the un-indexed
        base_date property that predicate makes Neptune full-scan + materialize
        and blow MemoryLimitExceededException. __Source__ is one-per-paper (tiny).
        valueMap streams one small map per source (no fold/project/closures).
        """
        if not (_is_valid_date_format(start_date) and _is_valid_date_format(end_date)):
            raise ValueError("Date values must be in 'YYYY-MM-DD' format.")

        try:
            id_rows = self._submit_query(
                f"g.V().hasLabel('{Vertex.SOURCE.value}')"
                ".valueMap('paper_id', 'base_date')"
            )
            paper_ids: list[str] = []
            seen: set[str] = set()
            for row in id_rows:
                # valueMap returns each property as a single-element list.
                pid_v = row.get("paper_id")
                bd_v = row.get("base_date")
                pid = pid_v[0] if isinstance(pid_v, list) and pid_v else pid_v
                bd = bd_v[0] if isinstance(bd_v, list) and bd_v else bd_v
                if pid is None or bd is None:
                    continue
                if start_date <= str(bd)[:10] <= end_date and pid not in seen:
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

    def delete_documents_by_date(self, base_date: str) -> dict[str, Any]:
        """Delete every paper whose base_date is exactly ``base_date``."""
        if not _is_valid_date_format(base_date):
            raise ValueError("'base_date' must be in the format 'YYYY-MM-DD'.")

        paper_ids = self._find_paper_ids_in_range(base_date, base_date)
        if not paper_ids:
            logger.warning("No documents found with 'base_date': '%s'", base_date)
            return {"status": "success", "base_date": base_date, "deleted": 0}

        results = self.batch_delete_documents(paper_ids)
        return summarize_deletion_results(results, base_date=base_date)

    def delete_documents_by_date_range(
        self, start_date: str, end_date: str
    ) -> dict[str, Any]:
        """Delete every paper whose base_date is in [start_date, end_date]."""
        paper_ids = self._find_paper_ids_in_range(start_date, end_date)
        if not paper_ids:
            return {
                "status": "success",
                "deleted_count": 0,
                "date_range": f"'{start_date}' to '{end_date}'",
            }

        results = self.batch_delete_documents(paper_ids)
        return summarize_deletion_results(
            results, date_range=f"{start_date} to {end_date}"
        )

    def delete_all_documents(self) -> dict[str, Any]:
        try:
            vertex_count = self._submit_query("g.V().count()")[0]
            edge_count = self._submit_query("g.E().count()")[0]
            logger.info("Deleting %d vertices and %d edges.", vertex_count, edge_count)

            # Drop edges before vertices so no dangling-edge scan is needed.
            self._submit_query("g.E().drop()")
            self._submit_query("g.V().drop()")

            remaining = self._submit_query("g.V().count()")[0]
            if remaining:
                logger.warning("%d vertices remain after deletion.", remaining)
            else:
                logger.info("Successfully deleted all graph data.")

            return {
                "status": "completed",
                "vertices_deleted": vertex_count,
                "edges_deleted": edge_count,
            }
        except Exception as e:
            error_msg = f"Error deleting all documents: {e}"
            logger.error(error_msg)
            return {"status": "error", "error": error_msg}
