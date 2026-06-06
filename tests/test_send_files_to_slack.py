"""Tests for send_files_to_slack — focusing on the new optional blocks payload.

The Slack HTTP API is mocked with ``responses`` so no real calls are made. We
assert backward compatibility (no blocks) and the new behavior (blocks attached
to chat.postMessage alongside a text fallback).
"""

import json

import pytest
import responses

from paper_bridge.summarizer.src.utils import send_files_to_slack

POST_MESSAGE = "https://slack.com/api/chat.postMessage"


def _register_post_message() -> None:
    responses.add(responses.POST, POST_MESSAGE, json={"ok": True}, status=200)


@pytest.mark.unit
class TestSendFilesToSlackMessage:
    @responses.activate
    def test_plain_message_has_text_no_blocks(self) -> None:
        _register_post_message()
        send_files_to_slack([], "tok", "C123", message="hello")

        body = json.loads(responses.calls[0].request.body)
        assert body["text"] == "hello"
        assert body["channel"] == "C123"
        assert "blocks" not in body

    @responses.activate
    def test_blocks_attached_with_text_fallback(self) -> None:
        _register_post_message()
        blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": "hi"}}]
        send_files_to_slack([], "tok", "C123", message="fallback", blocks=blocks)

        body = json.loads(responses.calls[0].request.body)
        # both required: text for notifications/accessibility, blocks for layout
        assert body["text"] == "fallback"
        assert body["blocks"] == blocks

    @responses.activate
    def test_uses_bearer_auth(self) -> None:
        _register_post_message()
        send_files_to_slack([], "xoxb-secret", "C123", message="hi")
        assert (
            responses.calls[0].request.headers["Authorization"] == "Bearer xoxb-secret"
        )

    @responses.activate
    def test_api_error_does_not_raise(self) -> None:
        responses.add(
            responses.POST, POST_MESSAGE, json={"ok": False, "error": "bad"}, status=200
        )
        # Must log and continue, never propagate.
        send_files_to_slack([], "tok", "C123", message="hi")
