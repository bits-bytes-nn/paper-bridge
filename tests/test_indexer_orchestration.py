"""Orchestration tests for `run_extract_and_build` (paper_bridge.indexer.src.indexer).

The single most important invariant here is ORDER: re-index cleanup must run
BEFORE extraction. graphrag's extraction pipeline writes to the graph itself, so
cleaning afterwards would target this run's half-written nodes and orphan the
prior version. This bug had zero coverage; these tests lock the ordering in
without needing a live graphrag/AWS stack (all heavy collaborators are patched).
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("graphrag_toolkit", reason="graphrag not installed")

from paper_bridge.indexer.src import indexer  # noqa: E402


def _paper(arxiv_id: str):
    return SimpleNamespace(arxiv_id=arxiv_id)


@pytest.mark.unit
class TestRunExtractAndBuildOrdering:
    def _run(self, papers):
        """Run run_extract_and_build with all heavy collaborators patched,
        returning the ordered list of (step) calls."""
        calls: list[str] = []

        builder = MagicMock()
        builder.clean_existing_documents.side_effect = lambda ids: calls.append(
            f"clean:{ids}"
        )
        builder.build.side_effect = lambda docs: calls.append("build")

        extractor = MagicMock()
        extractor.extract.side_effect = lambda ps: (calls.append("extract") or [])

        with (
            patch.object(indexer, "_configure_graph_rag"),
            patch.object(indexer, "_configure_logging"),
            patch.object(indexer, "_create_checkpoint", return_value=None),
            patch.object(
                indexer, "_setup_stores", return_value=(MagicMock(), MagicMock())
            ),
            patch.object(indexer, "Extractor", return_value=extractor),
            patch.object(indexer, "Builder", return_value=builder),
        ):
            indexer.run_extract_and_build(
                papers, config=MagicMock(), boto3_session=MagicMock()
            )
        return calls, builder, extractor

    def test_clean_runs_before_extract(self) -> None:
        calls, _, _ = self._run([_paper("2606.001"), _paper("2606.002")])
        clean_idx = next(i for i, c in enumerate(calls) if c.startswith("clean:"))
        extract_idx = calls.index("extract")
        build_idx = calls.index("build")
        # clean BEFORE extract BEFORE build — the load-bearing invariant.
        assert clean_idx < extract_idx < build_idx

    def test_clean_called_with_paper_ids(self) -> None:
        _, builder, _ = self._run([_paper("2606.001"), _paper("2606.002")])
        builder.clean_existing_documents.assert_called_once_with(
            ["2606.001", "2606.002"]
        )

    def test_empty_arxiv_ids_filtered_out(self) -> None:
        _, builder, _ = self._run([_paper("2606.001"), _paper("")])
        # falsy arxiv_id is dropped before cleanup.
        builder.clean_existing_documents.assert_called_once_with(["2606.001"])
