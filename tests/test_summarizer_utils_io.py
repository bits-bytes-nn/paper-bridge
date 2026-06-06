"""Tests for the pure/IO paths in ``paper_bridge.summarizer.src.utils``.

Covers two previously-untested areas:

* ``measure_execution_time`` — the timing decorator: result/arg passthrough.
* ``send_files_to_slack`` FILE-UPLOAD path — the 3-step Slack external-upload
  flow (getUploadURLExternal -> upload_url POST -> completeUploadExternal),
  plus the skip-on-missing-file and skip-on-API-error edge cases.

All HTTP is mocked with ``responses`` so nothing leaves the process. Real temp
files (``tmp_path``) are used so ``Path.exists()`` / ``Path.stat()`` behave for
real inside the function under test.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest
import responses

from paper_bridge.summarizer.src.utils import (
    measure_execution_time,
    send_files_to_slack,
)

GET_UPLOAD_URL = "https://slack.com/api/files.getUploadURLExternal"
COMPLETE_UPLOAD = "https://slack.com/api/files.completeUploadExternal"
UPLOAD_URL = "https://files.slack.com/upload/v1/ABC123"


@pytest.mark.unit
class TestMeasureExecutionTime:
    def test_returns_wrapped_result_unchanged(self) -> None:
        @measure_execution_time
        def add(a: int, b: int) -> int:
            return a + b

        assert add(2, 3) == 5

    def test_passes_args_and_kwargs_through(self) -> None:
        captured: dict = {}

        @measure_execution_time
        def record(*args, **kwargs):
            captured["args"] = args
            captured["kwargs"] = kwargs
            return "value"

        result = record(1, 2, foo="bar", baz=9)

        assert result == "value"
        assert captured["args"] == (1, 2)
        assert captured["kwargs"] == {"foo": "bar", "baz": 9}

    def test_preserves_function_metadata(self) -> None:
        @measure_execution_time
        def documented() -> None:
            """A docstring."""

        # functools.wraps copies name/doc onto the wrapper.
        assert documented.__name__ == "documented"
        assert documented.__doc__ == "A docstring."

    def test_logs_timing(self, caplog: pytest.LogCaptureFixture) -> None:
        @measure_execution_time
        def noop() -> int:
            return 7

        # The module logs via its own logger; capture at root to be safe.
        with caplog.at_level(logging.INFO):
            assert noop() == 7

        assert any("execution time" in rec.message.lower() for rec in caplog.records)

    def test_propagates_exceptions(self) -> None:
        @measure_execution_time
        def boom() -> None:
            raise ValueError("nope")

        with pytest.raises(ValueError, match="nope"):
            boom()


def _make_file(tmp_path: Path, name: str = "summary.md", body: str = "hello") -> Path:
    p = tmp_path / name
    p.write_text(body)
    return p


def _register_success_flow() -> None:
    """Register the happy-path 3-step external upload flow."""
    responses.add(
        responses.POST,
        GET_UPLOAD_URL,
        json={"ok": True, "upload_url": UPLOAD_URL, "file_id": "F1"},
        status=200,
    )
    responses.add(responses.POST, UPLOAD_URL, body="OK", status=200)
    responses.add(responses.POST, COMPLETE_UPLOAD, json={"ok": True}, status=200)


@pytest.mark.unit
class TestSendFilesToSlackUpload:
    @responses.activate
    def test_full_three_step_upload_flow(self, tmp_path: Path) -> None:
        _register_success_flow()
        file_path = _make_file(tmp_path)

        # message="" so we exercise ONLY the file-upload path (no postMessage).
        send_files_to_slack([file_path], "tok", "C123", message="")

        called = [c.request.url for c in responses.calls]
        assert GET_UPLOAD_URL in called
        assert UPLOAD_URL in called
        assert COMPLETE_UPLOAD in called
        assert len(responses.calls) == 3

    @responses.activate
    def test_get_upload_url_sends_filename_and_length(self, tmp_path: Path) -> None:
        _register_success_flow()
        file_path = _make_file(tmp_path, name="paper.md", body="abcdef")

        send_files_to_slack([file_path], "tok", "C123", message="")

        get_url_call = next(
            c for c in responses.calls if c.request.url == GET_UPLOAD_URL
        )
        body = get_url_call.request.body  # urlencoded form
        assert "filename=paper.md" in body
        # length is the on-disk size of the file content "abcdef" -> 6 bytes.
        assert f"length={file_path.stat().st_size}" in body

    @responses.activate
    def test_complete_upload_references_returned_file_id(self, tmp_path: Path) -> None:
        _register_success_flow()
        file_path = _make_file(tmp_path)

        send_files_to_slack([file_path], "tok", "C999", message="")

        complete_call = next(
            c for c in responses.calls if c.request.url == COMPLETE_UPLOAD
        )
        import json

        payload = json.loads(complete_call.request.body)
        assert payload["channel_id"] == "C999"
        assert payload["files"] == [{"id": "F1", "title": file_path.name}]

    @responses.activate
    def test_uses_bearer_auth_on_get_upload_url(self, tmp_path: Path) -> None:
        _register_success_flow()
        file_path = _make_file(tmp_path)

        send_files_to_slack([file_path], "xoxb-secret", "C123", message="")

        get_url_call = next(
            c for c in responses.calls if c.request.url == GET_UPLOAD_URL
        )
        assert get_url_call.request.headers["Authorization"] == "Bearer xoxb-secret"

    @responses.activate
    def test_nonexistent_file_is_skipped_with_warning(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        missing = tmp_path / "does_not_exist.md"

        with caplog.at_level(logging.WARNING):
            # Should neither raise nor make any HTTP call.
            send_files_to_slack([missing], "tok", "C123", message="")

        assert len(responses.calls) == 0
        assert any("File not found" in rec.message for rec in caplog.records)

    @responses.activate
    def test_get_upload_url_api_error_skips_file_without_raising(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        responses.add(
            responses.POST,
            GET_UPLOAD_URL,
            json={"ok": False, "error": "invalid_auth"},
            status=200,
        )
        file_path = _make_file(tmp_path)

        with caplog.at_level(logging.ERROR):
            # Must not raise; subsequent steps must not be attempted.
            send_files_to_slack([file_path], "tok", "C123", message="")

        urls = [c.request.url for c in responses.calls]
        assert GET_UPLOAD_URL in urls
        assert UPLOAD_URL not in urls
        assert COMPLETE_UPLOAD not in urls
        assert any("invalid_auth" in rec.message for rec in caplog.records)

    @responses.activate
    def test_get_upload_url_http_error_skips_file(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        responses.add(responses.POST, GET_UPLOAD_URL, body="boom", status=500)
        file_path = _make_file(tmp_path)

        with caplog.at_level(logging.ERROR):
            send_files_to_slack([file_path], "tok", "C123", message="")

        urls = [c.request.url for c in responses.calls]
        assert UPLOAD_URL not in urls
        assert COMPLETE_UPLOAD not in urls
        assert any("HTTP error" in rec.message for rec in caplog.records)

    @responses.activate
    def test_missing_upload_url_field_skips_file(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        # ok=True but no upload_url/file_id -> function bails before uploading.
        responses.add(responses.POST, GET_UPLOAD_URL, json={"ok": True}, status=200)
        file_path = _make_file(tmp_path)

        with caplog.at_level(logging.ERROR):
            send_files_to_slack([file_path], "tok", "C123", message="")

        urls = [c.request.url for c in responses.calls]
        assert UPLOAD_URL not in urls
        assert COMPLETE_UPLOAD not in urls
        assert any(
            "Missing upload_url or file_id" in rec.message for rec in caplog.records
        )

    @responses.activate
    def test_multiple_files_one_missing_one_uploaded(self, tmp_path: Path) -> None:
        _register_success_flow()
        good = _make_file(tmp_path, name="good.md")
        missing = tmp_path / "missing.md"

        # Missing file is skipped; good file completes the full 3-step flow.
        send_files_to_slack([missing, good], "tok", "C123", message="")

        assert len(responses.calls) == 3
