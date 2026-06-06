"""Unit tests for PaperSummarizer logic with a mocked LLM (Option A).

Importable now that BedrockConverse is a lazy import. Exercises the real
summarize() / summarize_batch() control flow — including prompt-cache application
and the asyncio batch isolation — without any AWS/LLM backend.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from paper_bridge.summarizer.src.summarizer import PaperSummarizer


def _summarizer_with_mock_llm(llm) -> PaperSummarizer:
    s = PaperSummarizer.__new__(PaperSummarizer)
    s.config = SimpleNamespace(
        summarization=SimpleNamespace(enable_prompt_caching=True)
    )
    s.summarization_llm = llm
    # prompt builds a trivial message list from content
    prompt = MagicMock()
    prompt.format_messages.side_effect = lambda content: [
        SimpleNamespace(role="user", content=content)
    ]
    s.summarization_prompt = prompt
    parser = MagicMock()
    parser.parse.side_effect = lambda text: {"summary": text}
    s.output_parser = parser
    return s


def _paper(content: str = "paper body", arxiv_id: str = "2503.1") -> SimpleNamespace:
    return SimpleNamespace(arxiv_id=arxiv_id, content=content)


@pytest.mark.unit
class TestSummarize:
    async def test_happy_path_parses_response(self) -> None:
        llm = MagicMock()
        llm.achat = AsyncMock(
            return_value=SimpleNamespace(message=SimpleNamespace(content="result text"))
        )
        s = _summarizer_with_mock_llm(llm)
        out = await s.summarize(_paper())
        assert out == {"summary": "result text"}
        llm.achat.assert_awaited_once()

    async def test_empty_content_raises(self) -> None:
        s = _summarizer_with_mock_llm(MagicMock())
        with pytest.raises(ValueError, match="content cannot be empty"):
            await s.summarize(_paper(content=""))

    async def test_empty_llm_response_raises(self) -> None:
        llm = MagicMock()
        llm.achat = AsyncMock(
            return_value=SimpleNamespace(message=SimpleNamespace(content=""))
        )
        s = _summarizer_with_mock_llm(llm)
        with pytest.raises(ValueError, match="Empty response"):
            await s.summarize(_paper())

    async def test_whitespace_only_response_raises(self) -> None:
        llm = MagicMock()
        llm.achat = AsyncMock(
            return_value=SimpleNamespace(message=SimpleNamespace(content="   "))
        )
        s = _summarizer_with_mock_llm(llm)
        with pytest.raises(ValueError, match="Empty response content"):
            await s.summarize(_paper())


@pytest.mark.unit
class TestSummarizeBatch:
    async def test_batch_isolates_failures(self) -> None:
        # First paper succeeds, second raises inside summarize -> isolated.
        llm = MagicMock()

        async def achat(messages):
            # With prompt caching active on llama-index-core >= 0.14, the last
            # message's content is normalized to a block list (TextBlock +
            # CachePoint), so coerce to str before substring matching.
            text = str(messages[0].content)
            if "boom" in text:
                raise RuntimeError("llm error")
            return SimpleNamespace(message=SimpleNamespace(content=f"ok:{text}"))

        llm.achat = achat
        s = _summarizer_with_mock_llm(llm)

        out = await s.summarize_batch(
            [_paper("good", "g"), _paper("boom", "b")], max_concurrent=2
        )
        assert "g" in out
        assert "b" not in out  # failed paper dropped, batch not aborted

    async def test_batch_all_success(self) -> None:
        llm = MagicMock()
        llm.achat = AsyncMock(
            return_value=SimpleNamespace(message=SimpleNamespace(content="ok"))
        )
        s = _summarizer_with_mock_llm(llm)
        out = await s.summarize_batch([_paper("a", "1"), _paper("b", "2")])
        assert set(out) == {"1", "2"}
