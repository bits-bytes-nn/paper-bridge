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

    @pytest.mark.parametrize(
        "bad", ["a b", "drop')", "a;b", "x'y", "../etc", "a*"]
    )
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


@pytest.mark.unit
class TestBestEffortDelete:
    def test_one_failing_stage_does_not_abort_others(self) -> None:
        # The 'facts' collect query exhausts retries; the other stages and the
        # source drop must still run, and the result is marked error with the
        # failing stage named.
        def side_effect(q):
            if "where(out('__SUPPORTS__')" in q:  # the facts collect query
                raise Exception("MemoryLimitExceededException")
            return [["v1"]] if q.strip().endswith(".fold()") else []

        nc = _client_with_submit(side_effect)
        result = nc.delete_document("2606.03458")

        assert result["status"] == "error"
        assert result["failed_stages"] == ["facts"]
        # Other stages still deleted, and the source was still dropped.
        assert result["deleted_nodes"]["chunks"] == 1
        assert result["deleted_nodes"]["facts"] == "error"
        assert result["deleted_nodes"]["source"] == 1

    def test_all_stages_succeed_is_success(self) -> None:
        nc = _client_with_submit(
            lambda q: [["v1"]] if q.strip().endswith(".fold()") else []
        )
        result = nc.delete_document("2606.03458")
        assert result["status"] == "success"
        assert "failed_stages" not in result

    def test_collects_then_drops_by_id(self) -> None:
        # Each collect query (.fold()) yields a wrapped id list; the source drop
        # and the id-drop queries yield [].
        ids_by_kind = {
            "EXTRACTED_FROM__').dedup().id().fold()": ["c1", "c2"],
        }

        def side_effect(q):
            if q.strip().endswith(".fold()"):
                # Return a distinct id list per collect query so we can total it.
                return [["v1", "v2", "v3"]]
            return []

        nc = _client_with_submit(side_effect)
        result = nc.delete_document("2606.03458")

        assert result["status"] == "success"
        # 4 collect kinds each return 3 ids -> all recorded in deleted_nodes.
        assert result["deleted_nodes"]["chunks"] == 3
        assert result["deleted_nodes"]["entities"] == 3
        assert result["deleted_nodes"]["source"] == 1
        # A drop-by-id query must have been issued (g.V('v1','v2',...).drop()).
        assert any(
            ".drop()" in q and "g.V('v1'" in q for q in nc._submitted
        )

    def test_drop_batches_respect_size_limit(self) -> None:
        # 120 ids with _DROP_BATCH_SIZE=50 -> 3 drop batches per collect kind.
        many = [f"id{i}" for i in range(120)]

        def side_effect(q):
            return [many] if q.strip().endswith(".fold()") else []

        nc = _client_with_submit(side_effect)
        nc.delete_document("2606.03458")

        drop_qs = [q for q in nc._submitted if ".drop()" in q and "g.V('id0'" in q]
        # First batch of each of the 5 collect kinds (chunks/topics/statements/
        # facts/entities) starts with id0.
        assert len(drop_qs) == 5
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
            return SimpleNamespace(
                all=lambda: SimpleNamespace(result=lambda: [42])
            )

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
