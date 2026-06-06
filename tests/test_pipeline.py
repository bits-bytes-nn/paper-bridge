"""Tests for the extracted summarizer pipeline helpers.

The pipeline module imports the heavy handler stack (fetcher/renderer/retriever),
which pulls optional ML dependencies. When those are not installed (e.g. a thin
local env), the whole module is skipped — in CI with full deps these run.
"""

from datetime import datetime
from types import SimpleNamespace

import pytest

pipeline = pytest.importorskip(
    "paper_bridge.summarizer.src.pipeline",
    reason="summarizer ML stack not installed in this environment",
)


@pytest.mark.unit
class TestGetFormattedDate:
    def test_explicit_date(self) -> None:
        assert pipeline.get_formatted_date(datetime(2025, 3, 28)) == "2025-03-28"

    def test_none_defaults_to_yesterday_utc(self) -> None:
        # Without an explicit date it returns yesterday's UTC date string.
        out = pipeline.get_formatted_date(None)
        assert len(out) == 10 and out.count("-") == 2


@pytest.mark.unit
class TestCreateResultFromSummary:
    def test_string_summary(self) -> None:
        result = pipeline.create_result_from_summary("2503.1", "plain summary")
        assert result.arxiv_id == "2503.1"
        assert result.summary == "plain summary"

    def test_dict_summary_with_tags_and_urls(self) -> None:
        result = pipeline.create_result_from_summary(
            "2503.1",
            {
                "summary": "S",
                "tags": "a,b,c",
                "urls": "[X](http://x), [X dup](http://x), [Y](http://y)",
            },
        )
        assert result.summary == "S"
        assert result.tags == ["a", "b", "c"]
        # URL dedup by underlying link (shared.extract_unique_urls)
        assert result.urls == ["[X](http://x)", "[Y](http://y)"]

    def test_dict_summary_without_optionals(self) -> None:
        result = pipeline.create_result_from_summary("2503.1", {"summary": "S"})
        assert result.tags is None
        assert result.urls is None


@pytest.mark.unit
class TestEnrichContentWithFigures:
    def test_no_figures_returns_text_unchanged(self) -> None:
        text = "some [Image: alt=Figure 1, src=x.png] body"
        assert pipeline._enrich_content_with_figures(text, []) == text

    def test_figure_caption_injected(self) -> None:
        fig = SimpleNamespace(figure_id="1", path="figs/1.png", analysis="A chart.")
        text = "intro [Image: alt=Figure 1, src=orig.png] outro"
        out = pipeline._enrich_content_with_figures(text, [fig])
        assert "figs/1.png" in out
        assert 'caption="A chart."' in out


@pytest.mark.unit
class TestProcessResults:
    def test_summaries_without_retrieval(self) -> None:
        results = pipeline.process_results(
            {"2503.1": "summary one", "2503.2": "summary two"}, {}, add_retrievals=False
        )
        assert {r.arxiv_id for r in results} == {"2503.1", "2503.2"}
        assert all(r.retrieval is None for r in results)

    def test_retrieval_merged_when_enabled(self) -> None:
        results = pipeline.process_results(
            {"2503.1": {"summary": "S"}},
            {"2503.1": {"summary": "R", "urls": "[Y](http://y)"}},
            add_retrievals=True,
        )
        assert results[0].retrieval == "R"
        assert results[0].urls == ["[Y](http://y)"]
