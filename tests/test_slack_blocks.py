"""Tests for the Slack Block Kit output overhaul.

These exercise the pure block-building / splitting / truncation logic, which is
where the readability improvement and the Slack length-limit safety live.
"""

from datetime import datetime
from types import SimpleNamespace

import pytest

from paper_bridge.summarizer.src.output_handlers.slack_handler import (
    SLACK_HEADER_MAX_CHARS,
    SLACK_MAX_BLOCKS,
    SLACK_SECTION_MAX_CHARS,
    SlackOutputHandler,
)

H = SlackOutputHandler


def _paper(**overrides) -> SimpleNamespace:
    base = dict(
        arxiv_id="2503.23461",
        title="Graph Neural Networks for Paper Retrieval",
        pdf_url="https://arxiv.org/pdf/2503.23461",
        published_at=datetime(2025, 3, 28),
        upvotes=42,
        content="content",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


@pytest.mark.unit
class TestTruncateHeader:
    def test_short_header_unchanged(self) -> None:
        assert H._truncate_header("Short title") == "Short title"

    def test_collapses_whitespace(self) -> None:
        assert H._truncate_header("a   b\n c") == "a b c"

    def test_long_header_truncated_within_limit(self) -> None:
        out = H._truncate_header("word " * 100)
        assert len(out) <= SLACK_HEADER_MAX_CHARS
        assert out.endswith("...")

    def test_truncation_does_not_break_midword(self) -> None:
        text = "supercalifragilistic " * 20
        out = H._truncate_header(text)
        assert len(out) <= SLACK_HEADER_MAX_CHARS
        # the visible part (minus ellipsis) should be whole words
        assert " supercali" not in out[:-3] or out[:-3].endswith("supercalifragilistic")


@pytest.mark.unit
class TestSplitForSection:
    def test_empty_returns_empty_list(self) -> None:
        assert H._split_for_section("") == []
        assert H._split_for_section("   ") == []

    def test_short_text_single_chunk(self) -> None:
        assert H._split_for_section("hello") == ["hello"]

    def test_each_chunk_within_limit(self) -> None:
        text = "Sentence number {}. ".format
        long = "".join(text(i) for i in range(2000))
        chunks = H._split_for_section(long)
        assert all(len(c) <= SLACK_SECTION_MAX_CHARS for c in chunks)
        assert len(chunks) > 1

    def test_hard_split_when_no_separator(self) -> None:
        long = "x" * (SLACK_SECTION_MAX_CHARS * 2 + 10)
        chunks = H._split_for_section(long)
        assert all(len(c) <= SLACK_SECTION_MAX_CHARS for c in chunks)
        assert "".join(chunks) == long

    def test_no_content_lost_with_paragraphs(self) -> None:
        para = ("para body " * 50).strip()
        text = "\n\n".join(para for _ in range(60))
        chunks = H._split_for_section(text)
        # all paragraph text present across chunks
        joined = " ".join(chunks)
        assert "para body" in joined
        assert all(len(c) <= SLACK_SECTION_MAX_CHARS for c in chunks)


@pytest.mark.unit
class TestCreateSlackBlocks:
    def test_minimal_blocks_structure(self) -> None:
        # With no retrieval, the layout is header -> context (date/link) ->
        # divider. The paper link now lives in the context row, not a section.
        blocks = H._create_slack_blocks(H.__new__(H), _paper(), None)
        types = [b["type"] for b in blocks]
        assert types[0] == "header"
        assert "context" in types
        assert "divider" in types

    def test_link_in_context_row(self) -> None:
        blocks = H._create_slack_blocks(H.__new__(H), _paper(), None)
        context = next(b for b in blocks if b["type"] == "context")
        texts = " ".join(e["text"] for e in context["elements"])
        assert "📄 논문 보기" in texts
        assert "https://arxiv.org/pdf/2503.23461" in texts

    def test_insight_sections_each_get_divider(self) -> None:
        retrieval = {
            "summary": (
                "*🚀 최근 발전 방향은?*\n\n• 발전 1\n\n---\n\n"
                "*💎 핵심 차이점은?*\n\n• 차이 1"
            ),
            "urls": "",
        }
        blocks = H._create_slack_blocks(H.__new__(H), _paper(), retrieval)
        joined = str(blocks)
        # The two question headers are preserved...
        assert "🚀" in joined and "💎" in joined
        # ...the stray hrule is dropped...
        assert "---" not in joined
        # ...and each section is preceded by a divider (insight title + 2 q's).
        assert [b["type"] for b in blocks].count("divider") >= 3

    def test_header_has_emoji_and_plain_text(self) -> None:
        blocks = H._create_slack_blocks(H.__new__(H), _paper(), None)
        header = blocks[0]
        assert header["text"]["type"] == "plain_text"
        assert header["text"]["emoji"] is True
        assert "🗞️" in header["text"]["text"]

    def test_context_includes_date_and_upvotes(self) -> None:
        blocks = H._create_slack_blocks(H.__new__(H), _paper(upvotes=7), None)
        context = next(b for b in blocks if b["type"] == "context")
        texts = " ".join(e["text"] for e in context["elements"])
        assert "2025-03-28" in texts
        assert "+7" in texts

    def test_zero_upvotes_omits_upvote_element(self) -> None:
        blocks = H._create_slack_blocks(H.__new__(H), _paper(upvotes=0), None)
        context = next(b for b in blocks if b["type"] == "context")
        texts = " ".join(e["text"] for e in context["elements"])
        assert "👍" not in texts

    def test_pdf_link_present(self) -> None:
        blocks = H._create_slack_blocks(H.__new__(H), _paper(), None)
        joined = str(blocks)
        assert "https://arxiv.org/pdf/2503.23461" in joined

    def test_retrieval_summary_renders_insight_section(self) -> None:
        retrieval = {"summary": "This paper improves retrieval.", "urls": ""}
        blocks = H._create_slack_blocks(H.__new__(H), _paper(), retrieval)
        joined = str(blocks)
        assert "Graph RAG" in joined
        assert "This paper improves retrieval." in joined

    def test_markdown_links_converted_in_references(self) -> None:
        retrieval = {
            "summary": "see [paper](https://example.com/a)",
            "urls": "[Ref A](https://example.com/a), [Ref B](https://example.com/b)",
        }
        blocks = H._create_slack_blocks(H.__new__(H), _paper(), retrieval)
        joined = str(blocks)
        # markdown [text](url) becomes slack <url|text>
        assert "<https://example.com/a|paper>" in joined
        assert "참고 문헌" in joined
        assert "<https://example.com/a|Ref A>" in joined

    def test_reference_with_parentheses_in_text_not_mangled(self) -> None:
        # Display text containing "(...)" must not be cut at the inner ")".
        retrieval = {
            "summary": "*🚀 q?*\n• a",
            "urls": "[QuaRot (Hadamard rotation)](https://arxiv.org/abs/2404.00456)",
        }
        blocks = H._create_slack_blocks(H.__new__(H), _paper(), retrieval)
        joined = str(blocks)
        assert (
            "<https://arxiv.org/abs/2404.00456|QuaRot (Hadamard rotation)>" in joined
        )
        # The mangled form (bare angle-bracketed text) must NOT appear.
        assert "<QuaRot (Hadamard rotation)>" not in joined

    def test_blocks_capped_at_limit(self) -> None:
        huge = " ".join(f"sentence-{i}." for i in range(20000))
        retrieval = {"summary": huge, "urls": ""}
        blocks = H._create_slack_blocks(H.__new__(H), _paper(), retrieval)
        assert len(blocks) <= SLACK_MAX_BLOCKS

    def test_long_title_header_safe(self) -> None:
        blocks = H._create_slack_blocks(H.__new__(H), _paper(title="word " * 100), None)
        assert len(blocks[0]["text"]["text"]) <= SLACK_HEADER_MAX_CHARS


@pytest.mark.unit
class TestHelpers:
    def test_convert_markdown_to_slack_links(self) -> None:
        out = H._convert_markdown_to_slack_links("[x](http://y)")
        assert out == "<http://y|x>"

    def test_extract_unique_urls_dedups(self) -> None:
        urls = "[A](http://x), [B](http://x), [C](http://z)"
        result = H._extract_unique_urls(urls)
        # http://x appears twice but should be deduped to one entry
        assert len(result) == 2

    def test_extract_unique_urls_empty(self) -> None:
        assert H._extract_unique_urls("") == []
