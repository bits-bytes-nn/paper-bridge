"""Tests for shared text utilities (markdown→Slack links, unique URL extraction).

These were previously duplicated across output handlers; the shared module is
the single source of truth, so its behavior is locked here.
"""

import pytest

from paper_bridge.shared.text_utils import (
    convert_markdown_to_slack_links,
    extract_unique_urls,
)


@pytest.mark.unit
class TestConvertMarkdownToSlackLinks:
    def test_single_link(self) -> None:
        assert convert_markdown_to_slack_links("[t](http://u)") == "<http://u|t>"

    def test_multiple_links(self) -> None:
        text = "see [A](http://a) and [B](http://b)"
        assert (
            convert_markdown_to_slack_links(text) == "see <http://a|A> and <http://b|B>"
        )

    def test_no_links_unchanged(self) -> None:
        assert convert_markdown_to_slack_links("plain text") == "plain text"

    def test_empty(self) -> None:
        assert convert_markdown_to_slack_links("") == ""


@pytest.mark.unit
class TestExtractUniqueUrls:
    def test_empty_and_whitespace(self) -> None:
        assert extract_unique_urls("") == []
        assert extract_unique_urls("   ") == []

    def test_plain_urls_dedup(self) -> None:
        assert extract_unique_urls("http://x, http://x, http://y") == [
            "http://x",
            "http://y",
        ]

    def test_markdown_links_dedup_by_url(self) -> None:
        # Same underlying URL with different anchor text → deduped to first.
        result = extract_unique_urls("[A](http://x), [B](http://x), [C](http://z)")
        assert result == ["[A](http://x)", "[C](http://z)"]

    def test_order_preserved(self) -> None:
        assert extract_unique_urls("http://c, http://a, http://b") == [
            "http://c",
            "http://a",
            "http://b",
        ]

    def test_mixed_plain_and_markdown(self) -> None:
        result = extract_unique_urls("http://x, [Y](http://y)")
        assert result == ["http://x", "[Y](http://y)"]
