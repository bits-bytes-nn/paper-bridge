"""Tests for cleaner date logic in ``paper_bridge.cleaner.main``.

Covers ``parse_event_params`` (event → typed params), ``parse_target_date``
(string → UTC datetime, with the yesterday default) and ``calculate_date_range``
(the off-by-one window math). No AWS is touched: only the pure functions are
exercised, with a lightweight ``SimpleNamespace`` config for the range math.
"""

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from paper_bridge.cleaner.main import (
    DateFormatError,
    calculate_date_range,
    parse_event_params,
    parse_target_date,
)


def _cfg(days_back: int, days_range: int) -> SimpleNamespace:
    return SimpleNamespace(
        cleaner=SimpleNamespace(days_back=days_back, days_range=days_range)
    )


@pytest.mark.unit
class TestParseEventParams:
    def test_full_valid_event(self) -> None:
        out = parse_event_params(
            {"TARGET_DATE": "2025-03-28", "DAYS_BACK": "10", "DAYS_RANGE": "3"}
        )
        assert out == ("2025-03-28", 10, 3)

    def test_empty_event(self) -> None:
        assert parse_event_params({}) == (None, None, None)

    def test_null_string_becomes_none(self) -> None:
        out = parse_event_params(
            {"TARGET_DATE": "null", "DAYS_BACK": "null", "DAYS_RANGE": "NULL"}
        )
        assert out == (None, None, None)

    def test_empty_string_becomes_none(self) -> None:
        out = parse_event_params({"TARGET_DATE": "", "DAYS_BACK": "", "DAYS_RANGE": ""})
        assert out == (None, None, None)

    def test_invalid_int_logs_and_returns_none(self) -> None:
        out = parse_event_params({"DAYS_BACK": "abc", "DAYS_RANGE": "1.5"})
        assert out == (None, None, None)

    def test_integer_values_coerced_via_str(self) -> None:
        # Numeric (non-string) values are stringified then re-parsed.
        out = parse_event_params({"DAYS_BACK": 5, "DAYS_RANGE": 2})
        assert out == (None, 5, 2)

    def test_target_date_non_string_coerced(self) -> None:
        out = parse_event_params({"TARGET_DATE": 20250328})
        assert out == ("20250328", None, None)

    def test_zero_days_back_is_kept(self) -> None:
        # 0 is a valid int; only falsy *strings*/None drop out.
        out = parse_event_params({"DAYS_BACK": "0"})
        assert out == (None, 0, None)


@pytest.mark.unit
class TestParseTargetDate:
    def test_none_defaults_to_yesterday_utc_midnight(self) -> None:
        result = parse_target_date(None)
        expected = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        delta = expected - result
        # Should be exactly one day earlier (allowing the rare midnight boundary).
        assert delta.days in (0, 1)
        assert result.tzinfo == UTC
        assert (result.hour, result.minute, result.second, result.microsecond) == (
            0,
            0,
            0,
            0,
        )

    def test_empty_string_defaults_to_yesterday(self) -> None:
        # Falsy string takes the same default branch as None.
        assert parse_target_date("").tzinfo == UTC

    def test_valid_date(self) -> None:
        result = parse_target_date("2025-03-28")
        assert result == datetime(2025, 3, 28, tzinfo=UTC)

    @pytest.mark.parametrize(
        "bad", ["2025/03/28", "28-03-2025", "March 28", "2025-13-01", "notadate"]
    )
    def test_invalid_format_raises(self, bad: str) -> None:
        with pytest.raises(DateFormatError):
            parse_target_date(bad)


@pytest.mark.unit
class TestCalculateDateRange:
    def test_uses_config_defaults_when_none(self) -> None:
        cfg = _cfg(days_back=365, days_range=7)
        target = datetime(2026, 1, 1, tzinfo=UTC)
        start, end = calculate_date_range(cfg, target, None, None)
        # end = target - 365 days = 2025-01-01; start = end - 6 days = 2024-12-26.
        assert end == "2025-01-01"
        assert start == "2024-12-26"

    def test_explicit_overrides_config(self) -> None:
        cfg = _cfg(days_back=999, days_range=999)
        target = datetime(2025, 3, 28, tzinfo=UTC)
        start, end = calculate_date_range(cfg, target, days_back=10, days_range=3)
        # end = 2025-03-18; start = end - 2 = 2025-03-16.
        assert end == "2025-03-18"
        assert start == "2025-03-16"

    def test_off_by_one_single_day_window(self) -> None:
        # days_range == 1 → start == end (window is a single day).
        cfg = _cfg(days_back=0, days_range=1)
        target = datetime(2025, 6, 15, tzinfo=UTC)
        start, end = calculate_date_range(cfg, target, None, None)
        assert start == end == "2025-06-15"

    def test_zero_days_back_explicit(self) -> None:
        cfg = _cfg(days_back=365, days_range=7)
        target = datetime(2025, 6, 15, tzinfo=UTC)
        start, end = calculate_date_range(cfg, target, days_back=0, days_range=1)
        assert start == end == "2025-06-15"

    def test_month_boundary_subtraction(self) -> None:
        cfg = _cfg(days_back=1, days_range=5)
        target = datetime(2025, 3, 3, tzinfo=UTC)
        start, end = calculate_date_range(cfg, target, None, None)
        # end = 2025-03-02; start = end - 4 = 2025-02-26.
        assert end == "2025-03-02"
        assert start == "2025-02-26"

    def test_real_cleaner_config_object(self) -> None:
        # Integrates with a real Config (loads yaml/defaults) to confirm attribute
        # wiring matches what calculate_date_range expects.
        from paper_bridge.cleaner.configs import Config

        cfg = Config.load()
        target = datetime(2026, 1, 1, tzinfo=UTC)
        start, end = calculate_date_range(cfg, target, None, None)
        # Both are ISO date strings, start <= end.
        assert start <= end
        assert len(start) == 10 and len(end) == 10
