"""Unit tests for the summarizer retriever logic (Option A).

These became possible once the heavy GraphRAG / bedrock-converse imports were made
lazy: the module now imports in the dev/CI env, so the real logic — query-engine
composition (G2 fix), batch exception isolation (H2 fix), and compact query
construction (G3 fix) — can be exercised with mocks instead of skipped.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from paper_bridge.summarizer.src.retriever import PaperRetriever, Retriever


def _retrieval_config(**overrides) -> SimpleNamespace:
    base = dict(
        traversal_based_or_semantic_guided="traversal_based",
        set_subretriever=False,
        use_reranking_beam_search=False,
        use_post_processors=False,
        use_gpu_reranker=False,
        gpu_id=0,
        use_diversity=False,
        use_enhancement=False,
        retrieval_max_tokens=8192,
        enable_prompt_caching=True,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _make_retriever(retrieval_cfg: SimpleNamespace) -> Retriever:
    """Build a Retriever without running its __init__ (which needs SSM + graphrag),
    wiring just enough state to drive the pure engine-composition logic."""
    r = Retriever.__new__(Retriever)
    r.config = SimpleNamespace(retrieval=retrieval_cfg)
    r.graph_store = MagicMock(name="graph_store")
    r.vector_store = MagicMock(name="vector_store")
    return r


def _paper(**overrides) -> SimpleNamespace:
    base = dict(
        arxiv_id="2503.1",
        title="Graph Neural Nets",
        summary="An abstract about GNNs and retrieval.",
        content="X" * 500_000,  # deliberately huge to test G3 truncation
    )
    base.update(overrides)
    return SimpleNamespace(**base)


@pytest.mark.unit
class TestQueryEngineComposition:
    """G2: the engine is built ONCE; post-processors attach to the chosen base
    mode instead of silently overwriting it with semantic-guided."""

    def test_traversal_default_uses_traversal_factory(self, monkeypatch) -> None:
        calls = {}

        class FakeEngine:
            @staticmethod
            def for_traversal_based_search(*a, **kw):
                calls["mode"] = "traversal"
                calls["post_processors"] = kw.get("post_processors")
                return "engine"

            @staticmethod
            def for_semantic_guided_search(*a, **kw):
                calls["mode"] = "semantic"
                return "engine"

        import paper_bridge.summarizer.src.retriever as mod

        monkeypatch.setattr(mod, "LexicalGraphQueryEngine", FakeEngine, raising=False)
        # patch the lazy import target (graphrag v3 path)
        import sys

        fake_gt = SimpleNamespace(LexicalGraphQueryEngine=FakeEngine)
        monkeypatch.setitem(sys.modules, "graphrag_toolkit.lexical_graph", fake_gt)

        r = _make_retriever(_retrieval_config())
        engine = r._build_query_engine()
        assert engine == "engine"
        assert calls["mode"] == "traversal"
        # no post-processors enabled by default
        assert calls["post_processors"] is None

    def test_post_processors_stay_on_traversal_mode(self, monkeypatch) -> None:
        """The G2 bug: enabling post-processors used to switch to semantic-guided.
        Now it must remain traversal, with post-processors attached."""
        calls = {}

        class FakeEngine:
            @staticmethod
            def for_traversal_based_search(*a, **kw):
                calls["mode"] = "traversal"
                calls["post_processors"] = kw.get("post_processors")
                return "engine"

            @staticmethod
            def for_semantic_guided_search(*a, **kw):
                calls["mode"] = "semantic"
                return "engine"

        import sys

        class _PP:
            def __init__(self, *a, **kw): ...

        fake_pp_mod = SimpleNamespace(
            SentenceReranker=_PP,
            BGEReranker=_PP,
            StatementDiversityPostProcessor=_PP,
            StatementEnhancementPostProcessor=_PP,
        )
        monkeypatch.setitem(
            sys.modules,
            "graphrag_toolkit.lexical_graph",
            SimpleNamespace(LexicalGraphQueryEngine=FakeEngine),
        )
        monkeypatch.setitem(
            sys.modules,
            "graphrag_toolkit.lexical_graph.retrieval",
            SimpleNamespace(),
        )
        monkeypatch.setitem(
            sys.modules,
            "graphrag_toolkit.lexical_graph.retrieval.post_processors",
            fake_pp_mod,
        )

        cfg = _retrieval_config(use_post_processors=True, use_diversity=True)
        r = _make_retriever(cfg)
        r._build_query_engine()
        # Must stay on traversal (the whole point of the G2 fix)...
        assert calls["mode"] == "traversal"
        # ...and post-processors must be attached, not dropped.
        assert calls["post_processors"] is not None
        assert len(calls["post_processors"]) == 2  # reranker + diversity


@pytest.mark.unit
class TestCompactQueryRepresentation:
    """G3: retrieval query uses title+abstract, not the full 200k-char paper."""

    def test_uses_title_and_abstract(self) -> None:
        pr = PaperRetriever.__new__(PaperRetriever)
        rep = pr._build_query_representation(_paper())
        assert "Graph Neural Nets" in rep
        assert "abstract about GNNs" in rep

    def test_truncates_to_cap(self) -> None:
        pr = PaperRetriever.__new__(PaperRetriever)
        rep = pr._build_query_representation(_paper())
        assert len(rep) <= PaperRetriever.MAX_QUERY_PAPER_CHARS

    def test_does_not_embed_full_paper_content(self) -> None:
        pr = PaperRetriever.__new__(PaperRetriever)
        rep = pr._build_query_representation(_paper())
        # the 500k-char content must NOT be dumped into the query
        assert len(rep) < 5000

    def test_falls_back_to_content_when_no_abstract(self) -> None:
        pr = PaperRetriever.__new__(PaperRetriever)
        paper = _paper(title="", summary="", content="fallback body text")
        rep = pr._build_query_representation(paper)
        assert "fallback body text" in rep


@pytest.mark.unit
class TestRetrieveBatchIsolation:
    """H2: one paper's failure must not wipe the whole batch's retrievals."""

    async def test_one_failing_query_does_not_abort_batch(self, monkeypatch) -> None:
        pr = PaperRetriever.__new__(PaperRetriever)
        pr.DEFAULT_QUERIES = ["q"]

        good = _paper(arxiv_id="good")
        bad = _paper(arxiv_id="bad")

        async def fake_process_query(paper, query):
            if paper.arxiv_id == "bad":
                raise RuntimeError("boom")
            return {"arxiv_id": paper.arxiv_id, "query": query, "answer": "a"}

        async def fake_process_response(arxiv_id, contexts):
            return arxiv_id, f"summary-{arxiv_id}"

        pr.process_query = fake_process_query
        pr.process_response = fake_process_response

        result = await pr.retrieve_batch([good, bad])
        # The good paper survives; the bad one is dropped, not fatal.
        assert "good" in result
        assert "bad" not in result
        assert result["good"] == "summary-good"

    async def test_failing_response_isolated(self) -> None:
        pr = PaperRetriever.__new__(PaperRetriever)
        pr.DEFAULT_QUERIES = ["q"]

        async def fake_process_query(paper, query):
            return {"arxiv_id": paper.arxiv_id, "query": query, "answer": "a"}

        async def fake_process_response(arxiv_id, contexts):
            if arxiv_id == "bad":
                raise ValueError("empty response")
            return arxiv_id, f"summary-{arxiv_id}"

        pr.process_query = fake_process_query
        pr.process_response = fake_process_response

        result = await pr.retrieve_batch(
            [_paper(arxiv_id="good"), _paper(arxiv_id="bad")]
        )
        assert set(result) == {"good"}
