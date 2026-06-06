"""Tests for the shared OpenSearchClient (used by indexer and cleaner).

The opensearch-py client is mocked. These lock in that every delete path goes
through ``delete_by_query`` (no per-id loop, no 10-hit search cap — the bug that
left orphan vectors after re-indexing) and that the skip/error shapes are
normalized.
"""

from unittest.mock import MagicMock

import pytest
from opensearchpy import NotFoundError

from paper_bridge.shared.opensearch_client import OpenSearchClient


def _client(exists=True, delete_response=None, delete_raises=None):
    oc = OpenSearchClient.__new__(OpenSearchClient)  # skip __init__/AWS auth
    oc.index = "chunk"
    oc.client = MagicMock()
    oc.client.indices.exists.return_value = exists
    if delete_raises is not None:
        oc.client.delete_by_query.side_effect = delete_raises
    else:
        oc.client.delete_by_query.return_value = delete_response or {
            "deleted": 7,
            "total": 7,
            "failures": [],
        }
    return oc


@pytest.mark.unit
class TestDeleteDocument:
    def test_uses_delete_by_query_with_term(self) -> None:
        oc = _client()
        result = oc.delete_document("2606.03458")
        assert result["status"] == "success"
        assert result["deleted"] == 7
        assert result["paper_id"] == "2606.03458"
        # The crucial regression guard: a single delete_by_query, not a search.
        oc.client.delete_by_query.assert_called_once()
        oc.client.search.assert_not_called()
        body = oc.client.delete_by_query.call_args.kwargs["body"]
        assert body["query"]["term"]["metadata.source.metadata.paper_id"] == (
            "2606.03458"
        )

    def test_empty_paper_id_raises(self) -> None:
        oc = _client()
        with pytest.raises(ValueError, match="must not be empty"):
            oc.delete_document("")

    def test_missing_index_skips(self) -> None:
        oc = _client(exists=False)
        result = oc.delete_document("2606.03458")
        assert result["status"] == "skipped"
        oc.client.delete_by_query.assert_not_called()

    def test_error_is_caught_not_raised(self) -> None:
        oc = _client(delete_raises=RuntimeError("boom"))
        result = oc.delete_document("2606.03458")
        assert result["status"] == "error"
        assert "boom" in result["error"]

    def test_not_found_during_delete_skips(self) -> None:
        oc = _client(delete_raises=NotFoundError(404, "x", {}))
        result = oc.delete_document("2606.03458")
        assert result["status"] == "skipped"


@pytest.mark.unit
class TestDateDeletes:
    def test_date_range_uses_range_query(self) -> None:
        oc = _client()
        result = oc.delete_documents_by_date_range("2026-06-01", "2026-06-30")
        assert result["status"] == "success"
        body = oc.client.delete_by_query.call_args.kwargs["body"]
        rng = body["query"]["range"]["metadata.source.metadata.base_date"]
        assert rng == {"gte": "2026-06-01", "lte": "2026-06-30"}
        assert result["date_range"] == "2026-06-01 to 2026-06-30"

    def test_single_date_uses_term_query(self) -> None:
        oc = _client()
        oc.delete_documents_by_date("2026-06-05")
        body = oc.client.delete_by_query.call_args.kwargs["body"]
        assert body["query"]["term"]["metadata.source.metadata.base_date"] == (
            "2026-06-05"
        )

    @pytest.mark.parametrize("bad", ["2026-6-5", "06-05-2026", "", "not-a-date"])
    def test_invalid_date_raises(self, bad: str) -> None:
        oc = _client()
        with pytest.raises(ValueError, match="YYYY-MM-DD"):
            oc.delete_documents_by_date(bad)

    def test_batch_delete_documents(self) -> None:
        oc = _client()
        results = oc.batch_delete_documents(["a", "b"])
        assert len(results) == 2
        assert oc.client.delete_by_query.call_count == 2
