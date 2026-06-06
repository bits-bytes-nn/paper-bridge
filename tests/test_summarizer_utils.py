"""Tests for ``paper_bridge.summarizer.src.utils`` pure functions.

Covers argument coercion (``arg_as_bool``), HTML→text extraction and the
``HTMLTagOutputParser``. These functions back the CLI/event interface and the LLM
output parsing, so edge cases (None, empty, malformed, boundary) matter.
"""

import argparse

import pytest

from paper_bridge.summarizer.src.utils import (
    HTMLTagOutputParser,
    arg_as_bool,
    extract_text_from_html,
)


@pytest.mark.unit
class TestArgAsBool:
    @pytest.mark.parametrize(
        "value", ["yes", "true", "t", "y", "1", "YES", "True", " T "]
    )
    def test_truthy_strings(self, value: str) -> None:
        assert arg_as_bool(value) is True

    @pytest.mark.parametrize(
        "value", ["no", "false", "f", "n", "0", "NO", "False", " F "]
    )
    def test_falsy_strings(self, value: str) -> None:
        assert arg_as_bool(value) is False

    def test_bool_passthrough(self) -> None:
        assert arg_as_bool(True) is True
        assert arg_as_bool(False) is False

    @pytest.mark.parametrize("value", ["maybe", "", "2", "tru", "yesno"])
    def test_invalid_string_raises(self, value: str) -> None:
        with pytest.raises(argparse.ArgumentTypeError):
            arg_as_bool(value)

    def test_non_string_non_bool_raises(self) -> None:
        # ints (other than via str) and None are not handled → error.
        with pytest.raises(argparse.ArgumentTypeError):
            arg_as_bool(None)
        with pytest.raises(argparse.ArgumentTypeError):
            arg_as_bool(5)


@pytest.mark.unit
class TestExtractTextFromHtml:
    def test_empty_input(self) -> None:
        assert extract_text_from_html("") == ""

    def test_strips_non_content_tags(self) -> None:
        html = (
            "<html><head><title>T</title><meta name='x'><style>p{}</style>"
            "<script>var x=1;</script></head><body>Hello World</body></html>"
        )
        assert extract_text_from_html(html) == "Hello World"

    def test_image_alt_and_src(self) -> None:
        html = '<img alt="Figure 1" src="fig.png">'
        assert extract_text_from_html(html) == "[Image: alt=Figure 1, src=fig.png]"

    def test_image_missing_attrs(self) -> None:
        assert extract_text_from_html("<img>") == "[Image: alt=, src=]"

    def test_anchor_with_href(self) -> None:
        html = '<a href="http://example.com">click</a>'
        assert extract_text_from_html(html) == "click (http://example.com)"

    def test_anchor_without_href(self) -> None:
        assert extract_text_from_html("<a>bare link</a>") == "bare link"

    def test_code_wrapped_in_backticks(self) -> None:
        assert extract_text_from_html("<code>x = 1</code>") == "`x = 1`"

    def test_pre_wrapped_in_backticks(self) -> None:
        assert extract_text_from_html("<pre>block</pre>") == "`block`"

    def test_math_wrapped(self) -> None:
        assert extract_text_from_html("<math>a+b</math>") == "$$ a+b $$"

    def test_whitespace_collapse(self) -> None:
        assert extract_text_from_html("<p>a    b\n\n   c</p>") == "a b c"

    def test_table_flattened(self) -> None:
        # Table cells are joined with no separator (children concatenated directly),
        # so "A"/"B" become "AB" before whitespace collapse.
        html = "<table><tr><td>A</td><td>B</td></tr></table>"
        assert extract_text_from_html(html) == "AB"

    @pytest.mark.parametrize(
        "raw,expected",
        [
            (r"foo \AND bar", "foo bar"),
            (r"a \times b", "a x b"),
            ("see footnotemark: here", "see here"),
        ],
    )
    def test_replacements(self, raw: str, expected: str) -> None:
        # Replacements run on the extracted text, then whitespace collapses.
        assert extract_text_from_html(f"<p>{raw}</p>") == expected

    def test_literal_backslash_n_replaced(self) -> None:
        # The replacements dict maps the two-character sequence "\n" to a space.
        assert extract_text_from_html(r"<p>line\nbreak</p>") == "line break"


@pytest.mark.unit
class TestHTMLTagOutputParser:
    def test_single_tag_returns_string(self) -> None:
        parser = HTMLTagOutputParser(tag_names="summary")
        assert parser.parse("<summary>Hello</summary>") == "Hello"

    def test_single_tag_missing_returns_empty_string(self) -> None:
        parser = HTMLTagOutputParser(tag_names="summary")
        assert parser.parse("<other>x</other>") == ""

    def test_tuple_returns_dict(self) -> None:
        parser = HTMLTagOutputParser(tag_names=("summary", "tags"))
        result = parser.parse("<summary>S</summary><tags>a,b</tags>")
        assert result == {"summary": "S", "tags": "a,b"}

    def test_tuple_partial_match(self) -> None:
        parser = HTMLTagOutputParser(tag_names=("summary", "tags"))
        result = parser.parse("<summary>only</summary>")
        assert result == {"summary": "only"}

    def test_tuple_no_match_returns_empty_dict(self) -> None:
        parser = HTMLTagOutputParser(tag_names=("summary", "tags"))
        assert parser.parse("<nothing/>") == {}

    def test_empty_text_single(self) -> None:
        assert HTMLTagOutputParser(tag_names="summary").parse("") == ""

    def test_empty_text_tuple(self) -> None:
        assert HTMLTagOutputParser(tag_names=("a", "b")).parse("") == {}

    def test_nested_content_preserved(self) -> None:
        parser = HTMLTagOutputParser(tag_names="summary")
        result = parser.parse("<summary>before <b>bold</b> after</summary>")
        assert result == "before <b>bold</b> after"

    def test_content_is_stripped(self) -> None:
        parser = HTMLTagOutputParser(tag_names="summary")
        assert parser.parse("<summary>   padded   </summary>") == "padded"

    def test_output_type_property(self) -> None:
        assert HTMLTagOutputParser(tag_names="x").output_type is str
        assert HTMLTagOutputParser(tag_names=("x", "y")).output_type is not str
