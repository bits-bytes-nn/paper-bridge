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

    def _collect_owned(self, project_query: str, owned: set[Any]) -> list[Any]:
        """Collect ids of shared candidates wholly owned by this paper.

        ``project_query`` returns a folded list of {'id': <node id>, 'owners':
        [<owner ids>]} maps. A candidate (fact/entity) is deletable only when
        EVERY owner that references it is in ``owned`` (this paper's collected
        statement/fact ids) — i.e. no other paper still uses it. This is the
        correct test for "owned only by this paper"; a Gremlin count() cannot
        express it (it would also count this paper's own repeated references).
        """
        result = self._submit_query(project_query)
        rows = result[0] if result and result[0] else []
        kept: list[Any] = []
        for row in rows:
            owners = row.get("owners") or []
            if owners and all(o in owned for o in owners):
                kept.append(row["id"])
        return kept

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
            #   Statement -BELONGS_TO->     Topic      (topic id hashes source_id
            #                                           => per-source, NOT shared)
            #   Fact      -SUPPORTS->       Statement  (fact id = hash(value)
            #                                           => SHARED across papers)
            #   Entity    -SUBJECT/OBJECT-> ...        (entity id = hash(value,cls)
            #                                           => SHARED across papers)
            #
            # chunks/statements/topics are per-source: every one reachable from
            # this Source is deletable. facts/entities are shared, so a node is
            # deletable ONLY if every owner that references it belongs to THIS
            # paper. We cannot express "owned only by this paper" with a Gremlin
            # count() (count()==1 wrongly keeps any node referenced twice WITHIN
            # this paper, and wrongly compares against the global ref count). So
            # we collect each shared candidate together with ALL of its owners and
            # decide in Python: delete iff its owner set is a SUBSET of this
            # paper's collected statement/fact ids.
            chunks = f"{base_query}.in('{ef}').dedup()"
            statements = f"{chunks}.in('{mi}').dedup().hasLabel('{stmt}')"
            simple_queries = {
                "chunks": f"{chunks}.id().fold()",
                "statements": f"{statements}.id().fold()",
                "topics": (
                    f"{statements}.out('{bt}').dedup().hasLabel('{topic}')"
                    ".id().fold()"
                ),
            }
            # For facts/entities: project (candidate_id, [owner_ids]) so Python
            # can keep only those wholly owned by this paper.
            fact_owners_query = (
                f"{statements}.in('{sup}').dedup().hasLabel('{fact}')"
                f".project('id', 'owners')"
                f".by(id()).by(out('{sup}').id().fold())"
                ".fold()"
            )
            entity_owners_query = (
                f"{statements}.in('{sup}').dedup().hasLabel('{fact}')"
                f".union(out('{subj}'), out('{obj}')).dedup().hasLabel('{entity}')"
                f".project('id', 'owners')"
                f".by(id()).by(in('{subj}', '{obj}').id().fold())"
                ".fold()"
            )

            # TWO PHASES, STRICTLY ORDERED: collect ALL ids first, THEN drop.
            # The collect queries walk down from chunks (statements =
            # chunk.in(MENTIONED_IN), etc.), so we must NOT drop chunks before
            # the later stages have collected — dropping chunks first severs the
            # traversal and every downstream stage collects 0 (the bug that left
            # statements/topics/facts/entities behind).
            #
            # Best-effort per stage: a stage that errors (e.g. exhausts its
            # memory-limit retries) is recorded as "error" and the others still
            # run, so a re-index removes as much of the prior version as possible.
            deleted: dict[str, Any] = {}
            errors: list[str] = []
            ids_by_stage: dict[str, list[Any]] = {}

            # Phase 1a: per-source stages (chunks/statements/topics).
            for name, query in simple_queries.items():
                try:
                    logger.info("Collecting '%s' ids for paper_id '%s'", name, paper_id)
                    result = self._submit_query(query)
                    ids_by_stage[name] = result[0] if result and result[0] else []
                    logger.info("Collected %d '%s' ids", len(ids_by_stage[name]), name)
                except Exception as e:
                    logger.error(
                        "Failed to collect '%s' for paper_id '%s': %s",
                        name,
                        paper_id,
                        e,
                    )
                    deleted[name] = "error"
                    errors.append(name)

            # Phase 1b: shared stages (facts/entities) — keep only nodes wholly
            # owned by this paper (owner set subset of this paper's ids).
            owned_statements = set(ids_by_stage.get("statements", []))
            try:
                logger.info("Collecting 'facts' ids for paper_id '%s'", paper_id)
                fact_ids = self._collect_owned(fact_owners_query, owned_statements)
                ids_by_stage["facts"] = fact_ids
                logger.info("Collected %d 'facts' ids", len(fact_ids))
            except Exception as e:
                logger.error("Failed to collect 'facts' for '%s': %s", paper_id, e)
                deleted["facts"] = "error"
                errors.append("facts")
                fact_ids = []

            try:
                logger.info("Collecting 'entities' ids for paper_id '%s'", paper_id)
                # An entity is deletable iff every fact referencing it is one of
                # THIS paper's facts. Use the set of facts reachable from this
                # paper's statements (the candidate facts before the owned-filter)
                # as the ownership universe, intersected with what we will delete.
                entity_ids = self._collect_owned(entity_owners_query, set(fact_ids))
                ids_by_stage["entities"] = entity_ids
                logger.info("Collected %d 'entities' ids", len(entity_ids))
            except Exception as e:
                logger.error("Failed to collect 'entities' for '%s': %s", paper_id, e)
                deleted["entities"] = "error"
                errors.append("entities")

            # Phase 2: drop the collected ids by id (order no longer matters —
            # the traversal is done).
            for name, ids in ids_by_stage.items():
                try:
                    self._drop_vertices_by_id(ids)
                    deleted[name] = len(ids)
                    logger.info(
                        "Deleted %d '%s' for paper_id: '%s'", len(ids), name, paper_id
                    )
                except Exception as e:
                    logger.error(
                        "Failed to drop '%s' for paper_id '%s': %s", name, paper_id, e
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

    def _find_paper_ids_in_range(self, start_date: str, end_date: str) -> list[str]:
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
