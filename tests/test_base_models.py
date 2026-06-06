"""Tests for ``paper_bridge.shared.base_models.BaseModelWithDefaults``.

The model's ``before`` validator swaps explicit ``None`` for the field default so
YAML-loaded configs (where missing keys deserialize to None) still validate.
"""

import pytest
from pydantic import Field

from paper_bridge.shared.base_models import BaseModelWithDefaults


class _Sample(BaseModelWithDefaults):
    name: str = Field(default="anon")
    count: int = Field(default=3)
    note: str | None = Field(default=None)


@pytest.mark.unit
class TestBaseModelWithDefaults:
    def test_none_replaced_with_default(self) -> None:
        m = _Sample(name=None, count=None)
        assert m.name == "anon"
        assert m.count == 3

    def test_real_value_kept(self) -> None:
        m = _Sample(name="real", count=10)
        assert m.name == "real"
        assert m.count == 10

    def test_zero_and_empty_string_kept(self) -> None:
        # Only None is special; falsy-but-not-None values pass through.
        m = _Sample(name="", count=0)
        assert m.name == ""
        assert m.count == 0

    def test_none_with_none_default_stays_none(self) -> None:
        # When the field default IS None, the validator leaves the None in place.
        m = _Sample(note=None)
        assert m.note is None

    def test_missing_fields_use_defaults(self) -> None:
        m = _Sample()
        assert (m.name, m.count, m.note) == ("anon", 3, None)

    def test_non_dict_input_passthrough(self) -> None:
        # The validator returns non-dict input untouched (it then fails normal
        # pydantic validation since a string isn't a valid model payload).
        with pytest.raises(Exception):
            _Sample.model_validate("not a dict")

    def test_partial_override(self) -> None:
        m = _Sample(name=None, count=99)
        assert m.name == "anon"
        assert m.count == 99
