"""Tests for the unified SNS alarm formatter shared across all modules."""

from datetime import UTC, datetime

import pytest

from paper_bridge.shared import format_alarm


@pytest.mark.unit
class TestFormatAlarm:
    def test_subject_shape(self) -> None:
        subject, _ = format_alarm(
            event="Cleaner", status="FAILED", fields={"Date": "2026-06-10"}
        )
        assert subject == "[paper-bridge] Cleaner — FAILED"

    def test_inline_fields_are_aligned(self) -> None:
        _, message = format_alarm(
            event="Indexer",
            status="FAILED",
            fields={"Date": "2026-06-10", "Error": "boom"},
        )
        # Longest key is "Error" (5) -> colon padded to width+1 so values align.
        assert "Date:  2026-06-10" in message
        assert "Error: boom" in message

    def test_first_line_is_event_status(self) -> None:
        _, message = format_alarm(
            event="Summarizer", status="FAILED", fields={"Date": "2026-06-10"}
        )
        assert message.splitlines()[0] == "Summarizer FAILED"

    def test_multiline_value_renders_as_block(self) -> None:
        _, message = format_alarm(
            event="Indexer",
            status="ALERT",
            fields={"Trace": "line1\nline2"},
        )
        assert "Trace:\nline1\nline2" in message

    def test_timestamp_is_rendered_in_utc(self) -> None:
        ts = datetime(2026, 6, 10, 4, 12, 0, tzinfo=UTC)
        _, message = format_alarm(
            event="Cleaner",
            status="FAILED",
            fields={"Date": "2026-06-10"},
            timestamp=ts,
        )
        assert message.rstrip().endswith("— 2026-06-10 04:12:00 UTC")

    def test_custom_project_in_subject(self) -> None:
        subject, _ = format_alarm(
            event="Indexer",
            status="FAILED",
            fields={"Date": "2026-06-10"},
            project="tech-digest",
        )
        assert subject == "[tech-digest] Indexer — FAILED"
