"""Tests for the shared NeptuneClient (used by both indexer and cleaner).

The Gremlin endpoint is mocked: every ``client.submit(q).all().result()`` is
captured so we can assert the two-phase delete behaviour (collect ids -> drop by
id in bounded batches), paper_id validation, and shared-node safety, without a
live Neptune. This class previously had ZERO unit coverage and was the source of
the str-int and MemoryLimitExceeded production bugs.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from paper_bridge.shared.neptune_client import (
    NeptuneClient,
    summarize_deletion_results,
)


def _client_with_submit(side_effect):
    """Build a NeptuneClient whose gremlin client.submit is mocked.

    ``side_effect`` maps each submitted query string to the list result that
    ``.all().result()`` should return.
    """
    nc = NeptuneClient("neptune.example.com")
    submitted: list[str] = []

    def submit(query, bindings=None):
        submitted.append(query)
        result = side_effect(query) if callable(side_effect) else side_effect
        return SimpleNamespace(all=lambda: SimpleNamespace(result=lambda: result))

    nc._gremlin_client = MagicMock()
    nc._gremlin_client.submit.side_effect = submit
    nc._submitted = submitted  # type: ignore[attr-defined]
    return nc


@pytest.mark.unit
class TestPaperIdValidation:
    def test_empty_paper_id_raises(self) -> None:
        nc = _client_with_submit([])
        with pytest.raises(ValueError, match="must not be empty"):
            nc.delete_document("")

    @pytest.mark.parametrize("bad", ["a b", "drop')", "a;b", "x'y", "../etc", "a*"])
    def test_injection_shaped_paper_id_raises(self, bad: str) -> None:
        nc = _client_with_submit([])
        with pytest.raises(ValueError, match="Invalid 'paper_id'"):
            nc.delete_document(bad)

    @pytest.mark.parametrize("ok", ["2606.03458", "2606.03458v1", "math:9901", "a_b-c"])
    def test_valid_paper_ids_accepted(self, ok: str) -> None:
        # fold() queries return [[]] (empty id list); source drop returns [].
        nc = _client_with_submit(lambda q: [[]] if ".fold()" in q else [])
        result = nc.delete_document(ok)
        assert result["status"] == "success"
        assert result["paper_id"] == ok


# Stage queries are distinguished by structural markers rather than brittle
# full-string matches: per-source stages end in ".id().fold()"; the shared
# facts/entities stages use ".project('id', 'owners')". A helper routes a fake
# response per stage so tests can exercise the real collect/owner-subset/drop
# logic.
def _is_collect(q: str) -> bool:
    return q.strip().endswith(".fold()")


def _is_owner_project(q: str) -> bool:
    return "project('id', 'owners')" in q


def _is_entity_project(q: str) -> bool:
    # The entities owner-project fans out to SUBJECT/OBJECT; the facts one does not.
    return _is_owner_project(q) and "__SUBJECT__" in q


def _is_fact_project(q: str) -> bool:
    return _is_owner_project(q) and "__SUBJECT__" not in q


@pytest.mark.unit
class TestBestEffortDelete:
    def test_one_failing_stage_does_not_abort_others(self) -> None:
        # The 'facts' owner-project query errors; the other stages and the source
        # drop must still run, and the result is marked error naming the stage.
        def side_effect(q):
            if _is_fact_project(q):
                raise Exception("MemoryLimitExceededException")
            if _is_owner_project(q):
                return [[]]
            return [["v1"]] if _is_collect(q) else []

        nc = _client_with_submit(side_effect)
        result = nc.delete_document("2606.03458")

        assert result["status"] == "error"
        assert "facts" in result["failed_stages"]
        assert result["deleted_nodes"]["chunks"] == 1
        assert result["deleted_nodes"]["facts"] == "error"
        assert result["deleted_nodes"]["source"] == 1

    def test_collects_all_before_any_drop(self) -> None:
        # Regression: per-source collects + the owner-project collects must all
        # precede the first id-drop, or dropping chunks severs the traversal.
        order: list[str] = []

        def submit(query, bindings=None):
            q = query.strip()
            res: object = []
            if _is_owner_project(q):
                order.append("collect")
                res = [[]]
            elif _is_collect(q):
                order.append("collect")
                res = [["v1"]]
            elif q.startswith("g.V('v1'") and q.endswith(".drop()"):
                order.append("drop")
            return SimpleNamespace(all=lambda: SimpleNamespace(result=lambda: res))

        nc = NeptuneClient("x")
        nc._gremlin_client = MagicMock()
        nc._gremlin_client.submit.side_effect = submit
        nc.delete_document("2606.03458")

        # 5 collects (chunks/statements/topics + facts/entities projects) precede
        # the first drop.
        first_drop = order.index("drop")
        assert order[:first_drop].count("collect") == 5
        assert "drop" not in order[:first_drop]

    def test_all_stages_succeed_is_success(self) -> None:
        def side_effect(q):
            if _is_owner_project(q):
                return [[]]
            return [["v1"]] if _is_collect(q) else []

        nc = _client_with_submit(side_effect)
        result = nc.delete_document("2606.03458")
        assert result["status"] == "success"
        assert "failed_stages" not in result

    def test_only_paper_owned_facts_entities_deleted(self) -> None:
        # The subset test: a fact whose owners ⊄ this paper's statements is KEPT;
        # one wholly owned is deleted. Statements collected = {s1, s2}.
        def side_effect(q):
            # Order matters: the fact/entity project queries are built on top of
            # the statements traversal (so they also contain hasLabel(Statement)
            # and end in .fold()); match the project queries FIRST.
            if _is_fact_project(q):
                return [
                    [
                        {"id": "f_owned", "owners": ["s1", "s2"]},  # fully owned
                        {"id": "f_shared", "owners": ["s1", "sX"]},  # sX = other
                    ]
                ]
            if _is_entity_project(q):
                return [[{"id": "e_owned", "owners": ["f_owned"]}]]
            if "hasLabel('__Statement__')" in q and _is_collect(q):
                return [["s1", "s2"]]  # this paper's statements
            return [["c1"]] if _is_collect(q) else []

        nc = _client_with_submit(side_effect)
        result = nc.delete_document("2606.03458")

        assert result["deleted_nodes"]["facts"] == 1  # only f_owned
        assert result["deleted_nodes"]["entities"] == 1  # e_owned (owner f_owned)
        # f_shared must NOT appear in any drop query.
        assert not any("f_shared" in q for q in nc._submitted)
        assert any("f_owned" in q for q in nc._submitted)

    def test_drop_batches_respect_size_limit(self) -> None:
        # 120 per-source ids with _DROP_BATCH_SIZE=50 -> batched drops.
        many = [f"id{i}" for i in range(120)]

        def side_effect(q):
            if _is_owner_project(q):
                return [[]]
            return [many] if _is_collect(q) else []

        nc = _client_with_submit(side_effect)
        nc.delete_document("2606.03458")

        # No single drop query inlines more than 50 ids.
        for q in nc._submitted:
            if q.startswith("g.V('id"):
                assert q.count("'id") <= NeptuneClient._DROP_BATCH_SIZE

    def test_per_hop_dedup_present_in_collect_queries(self) -> None:
        # Guards the MemoryLimitExceeded fix: every .in() hop must be deduped.
        captured = []

        def side_effect(q):
            captured.append(q)
            return [[]] if q.strip().endswith(".fold()") else []

        nc = _client_with_submit(side_effect)
        nc.delete_document("2606.03458")

        fold_qs = [q for q in captured if q.strip().endswith(".fold()")]
        for q in fold_qs:
            # No ".in('X')" should be immediately followed by another ".in("
            # without a ".dedup()" between them.
            assert ".in(" in q
            assert ").dedup()" in q


@pytest.mark.unit
class TestDateRange:
    def test_invalid_date_raises(self) -> None:
        nc = _client_with_submit([])
        with pytest.raises(ValueError, match="YYYY-MM-DD"):
            nc.delete_documents_by_date_range("2026-1-1", "2026-12-31")

    def test_python_side_date_filter(self) -> None:
        # valueMap returns one row per __Source__; only in-range papers selected.
        rows = [
            {"paper_id": ["a"], "base_date": ["2026-06-01T00:00:00"]},
            {"paper_id": ["b"], "base_date": ["2026-06-10T00:00:00"]},
            {"paper_id": ["c"], "base_date": ["2026-07-01T00:00:00"]},
        ]

        def side_effect(q):
            if "valueMap" in q:
                return rows
            if q.strip().endswith(".fold()"):
                return [[]]
            return []

        nc = _client_with_submit(side_effect)
        found = nc._find_paper_ids_in_range("2026-06-01", "2026-06-30")
        assert found == ["a", "b"]


@pytest.mark.unit
class TestMemoryLimitRetry:
    def test_retries_then_succeeds(self) -> None:
        calls = {"n": 0}

        def submit(query, bindings=None):
            calls["n"] += 1
            if calls["n"] < 3:
                raise Exception("MemoryLimitExceededException: out of memory")
            return SimpleNamespace(all=lambda: SimpleNamespace(result=lambda: [42]))

        nc = NeptuneClient("x")
        nc._gremlin_client = MagicMock()
        nc._gremlin_client.submit.side_effect = submit
        slept: list[int] = []

        out = nc._submit_query("g.V().count()", sleep=slept.append)
        assert out == [42]
        assert calls["n"] == 3
        assert slept == [3, 6]  # exponential backoff before the 2 retries

    def test_non_memory_error_propagates_immediately(self) -> None:
        nc = NeptuneClient("x")
        nc._gremlin_client = MagicMock()
        nc._gremlin_client.submit.side_effect = ValueError("bad query")
        with pytest.raises(ValueError, match="bad query"):
            nc._submit_query("g.bad()", sleep=lambda s: None)

    def test_gives_up_after_max_retries(self) -> None:
        nc = NeptuneClient("x")
        nc._gremlin_client = MagicMock()
        nc._gremlin_client.submit.side_effect = Exception(
            "MemoryLimitExceededException"
        )
        with pytest.raises(Exception, match="MemoryLimitExceeded"):
            nc._submit_query("g.V().count()", sleep=lambda s: None)


@pytest.mark.unit
class TestSummarize:
    def test_counts_success_and_error(self) -> None:
        results = [
            {"status": "success", "paper_id": "a"},
            {"status": "error", "paper_id": "b"},
            {"status": "success", "paper_id": "c"},
        ]
        out = summarize_deletion_results(results, date_range="x to y")
        assert out["success_count"] == 2
        assert out["error_count"] == 1
        assert out["total_documents"] == 3
        assert out["date_range"] == "x to y"
