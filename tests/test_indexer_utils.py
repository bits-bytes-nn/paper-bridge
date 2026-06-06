"""Tests for ``paper_bridge.indexer.src.utils``."""

import argparse

import pytest

from paper_bridge.indexer.src.utils import (
    HTMLTagOutputParser,
    arg_as_bool,
)


@pytest.mark.unit
class TestArgAsBool:
    @pytest.mark.parametrize("value", ["yes", "true", "t", "y", "1", "TRUE"])
    def test_truthy(self, value: str) -> None:
        assert arg_as_bool(value) is True

    @pytest.mark.parametrize("value", ["no", "false", "f", "n", "0", "FALSE"])
    def test_falsy(self, value: str) -> None:
        assert arg_as_bool(value) is False

    def test_bool_passthrough(self) -> None:
        assert arg_as_bool(True) is True
        assert arg_as_bool(False) is False

    @pytest.mark.parametrize("value", ["nope", "", None, 7])
    def test_invalid_raises(self, value) -> None:
        with pytest.raises(argparse.ArgumentTypeError):
            arg_as_bool(value)


@pytest.mark.unit
class TestHTMLTagOutputParser:
    def test_single_tag(self) -> None:
        assert HTMLTagOutputParser("body").parse("<body>X</body>") == "X"

    def test_single_missing(self) -> None:
        assert HTMLTagOutputParser("body").parse("<head>x</head>") == ""

    def test_tuple_dict(self) -> None:
        result = HTMLTagOutputParser(("a", "b")).parse("<a>1</a><b>2</b>")
        assert result == {"a": "1", "b": "2"}

    def test_tuple_no_match(self) -> None:
        assert HTMLTagOutputParser(("a", "b")).parse("<z/>") == {}

    def test_empty_text(self) -> None:
        assert HTMLTagOutputParser("a").parse("") == ""
        assert HTMLTagOutputParser(("a",)).parse("") == {}

    def test_nested_decode(self) -> None:
        result = HTMLTagOutputParser("a").parse("<a>x <i>y</i> z</a>")
        assert result == "x <i>y</i> z"
