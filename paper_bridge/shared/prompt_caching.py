"""Prompt-caching helpers for LlamaIndex chat messages.

Amazon Bedrock (and the Anthropic API) support **prompt caching**: a stable
prompt prefix can be cached so repeated calls that share it skip re-processing
those tokens — cutting cost and latency. In LlamaIndex this is expressed by
appending a ``CachePoint`` block to a message's content; everything *before* the
cache point is cached.

Where it pays off here: the summarizer sends the full paper text on every call,
and the retriever sends a large GraphRAG context — both are big, stable prefixes
worth caching when the same paper/context is summarized more than once (retries,
multi-language, multi-output-format runs).

Version tolerance: ``CachePoint`` / ``CacheControl`` live in ``llama-index-core``
>= ~0.13. The project is pinned to **0.14.20**, where they ARE present, so caching
is **active** (``prompt_caching_supported()`` returns True). The lazy-import +
no-op fallback is retained purely as a safety net: on an older core that lacks the
blocks, this module degrades to a no-op instead of breaking. The minimum cacheable
prefix is ~1024 tokens (2048 for Haiku models); shorter prompts are simply not
cached by the provider, so adding a cache point is always safe.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from llama_index.core.llms import ChatMessage


def _cache_primitives() -> tuple[Any, Any, Any] | None:
    """Return ``(CachePoint, CacheControl, TextBlock)`` if the core supports them.

    Returns ``None`` when the installed ``llama-index-core`` predates these
    blocks, so callers can no-op cleanly.
    """
    try:
        from llama_index.core.llms import (
            CacheControl,
            CachePoint,
            TextBlock,
        )
    except ImportError:
        return None
    return CachePoint, CacheControl, TextBlock


def prompt_caching_supported() -> bool:
    """True if the installed llama-index-core exposes cache-point blocks."""
    return _cache_primitives() is not None


def apply_cache_point(
    messages: list[ChatMessage], *, enabled: bool = True
) -> list[ChatMessage]:
    """Append a cache point to the last message so its prefix is cached.

    Mutates and returns ``messages``. No-ops (returns unchanged) when caching is
    disabled, unsupported by the installed core, or there are no messages. The
    last message's ``content`` is normalized to a block list and a
    ``CachePoint(ephemeral)`` block is appended; everything before it is cached.
    """
    if not enabled or not messages:
        return messages

    primitives = _cache_primitives()
    if primitives is None:
        return messages
    CachePoint, CacheControl, _TextBlock = primitives

    last = messages[-1]
    cache_point = CachePoint(cache_control=CacheControl(type="ephemeral"))

    # llama-index-core >= 0.14 stores message content as a validated list of
    # blocks on ``.blocks`` (``.content`` is a read-only string view that rejects
    # list assignment). Append the cache point there.
    blocks = getattr(last, "blocks", None)
    if isinstance(blocks, list):
        last.blocks = [*blocks, cache_point]
        return messages

    # Fallback for older cores / mock messages that expose a settable ``.content``
    # (str or list of blocks). ``.content`` is typed as ``str`` on the model but
    # older cores accept a block list here, so the list assignment is intentional.
    content = getattr(last, "content", None)
    if isinstance(content, str):
        last.content = [_TextBlock(text=content), cache_point]  # type: ignore[assignment]
    elif isinstance(content, list):
        last.content = [*content, cache_point]  # type: ignore[assignment]
    # Unknown shape → leave untouched.
    return messages
