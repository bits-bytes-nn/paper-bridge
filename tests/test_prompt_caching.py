"""Tests for the version-tolerant prompt-caching helper.

The helper applies a CachePoint block when the installed llama-index-core
supports it, and no-ops otherwise. Since the pinned core (0.12.6) lacks the
blocks, the "supported" path is tested by injecting fakes into the import the
helper performs, so both branches are covered regardless of the env.
"""

from dataclasses import dataclass

import pytest

from paper_bridge.shared import prompt_caching
from paper_bridge.shared.prompt_caching import apply_cache_point


@dataclass
class FakeMessage:
    role: str
    content: object


# --- Fakes mimicking the llama-index-core cache-point block API ---------------


@dataclass
class FakeCacheControl:
    type: str = "ephemeral"


@dataclass
class FakeCachePoint:
    cache_control: object


@dataclass
class FakeTextBlock:
    text: str
    type: str = "text"


@pytest.fixture
def supported(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force the helper to see a cache-point-capable core."""
    monkeypatch.setattr(
        prompt_caching,
        "_cache_primitives",
        lambda: (FakeCachePoint, FakeCacheControl, FakeTextBlock),
    )


@pytest.fixture
def unsupported(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force the helper to see a core without cache-point blocks."""
    monkeypatch.setattr(prompt_caching, "_cache_primitives", lambda: None)


@pytest.mark.unit
class TestNoOpConditions:
    def test_disabled_returns_unchanged(self, supported: None) -> None:
        msgs = [FakeMessage("user", "hello")]
        out = apply_cache_point(msgs, enabled=False)
        assert out is msgs
        assert out[0].content == "hello"

    def test_empty_messages(self, supported: None) -> None:
        assert apply_cache_point([], enabled=True) == []

    def test_unsupported_core_no_op(self, unsupported: None) -> None:
        msgs = [FakeMessage("user", "hello")]
        out = apply_cache_point(msgs, enabled=True)
        assert out[0].content == "hello"  # untouched


@pytest.mark.unit
class TestSupportedPath:
    def test_string_content_becomes_block_list_with_cache_point(
        self, supported: None
    ) -> None:
        msgs = [FakeMessage("user", "big stable prefix")]
        out = apply_cache_point(msgs, enabled=True)
        content = out[-1].content
        assert isinstance(content, list)
        # original text preserved as a TextBlock, cache point appended last
        assert isinstance(content[0], FakeTextBlock)
        assert content[0].text == "big stable prefix"
        assert isinstance(content[-1], FakeCachePoint)
        assert content[-1].cache_control.type == "ephemeral"

    def test_list_content_appends_cache_point(self, supported: None) -> None:
        blocks = [FakeTextBlock("a"), FakeTextBlock("b")]
        msgs = [FakeMessage("user", blocks)]
        out = apply_cache_point(msgs, enabled=True)
        content = out[-1].content
        assert len(content) == 3
        assert isinstance(content[-1], FakeCachePoint)

    def test_cache_point_only_on_last_message(self, supported: None) -> None:
        msgs = [
            FakeMessage("system", "instructions"),
            FakeMessage("user", "the document"),
        ]
        out = apply_cache_point(msgs, enabled=True)
        # first message untouched (still a str), last got the block list
        assert out[0].content == "instructions"
        assert isinstance(out[-1].content, list)
        assert isinstance(out[-1].content[-1], FakeCachePoint)

    def test_unknown_content_shape_left_untouched(self, supported: None) -> None:
        msgs = [FakeMessage("user", 12345)]  # neither str nor list
        out = apply_cache_point(msgs, enabled=True)
        assert out[-1].content == 12345


@pytest.mark.unit
class TestRealChatMessage:
    """Exercises the helper against a REAL llama-index ChatMessage (not fakes).

    This is what catches API drift like core>=0.14 making ``.content`` a
    read-only string view — the cache point must go on ``.blocks``. Skips when
    the cache-point primitives aren't in the installed core.
    """

    def test_real_message_gets_cache_point_on_blocks(self) -> None:
        pytest.importorskip("llama_index.core.llms")
        from paper_bridge.shared.prompt_caching import prompt_caching_supported

        if not prompt_caching_supported():
            pytest.skip("installed llama-index-core lacks CachePoint")

        from llama_index.core.llms import CachePoint, ChatMessage

        msgs = [ChatMessage(role="user", content="a large stable prefix " * 100)]
        out = apply_cache_point(msgs, enabled=True)
        # Must not raise, and the last block must be a real CachePoint.
        assert any(isinstance(b, CachePoint) for b in out[-1].blocks)
        # Original text still present.
        assert "large stable prefix" in out[-1].content
