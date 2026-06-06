import asyncio
from typing import TYPE_CHECKING

import boto3

from paper_bridge.shared import apply_cache_point
from paper_bridge.summarizer.configs import Config

if TYPE_CHECKING:
    from llama_index.llms.bedrock_converse import BedrockConverse

from .aws_helpers import get_cross_inference_model_id
from .constants import Language
from .fetcher import Paper
from .logger import logger
from .prompts import PaperSummarizationPrompt
from .utils import HTMLTagOutputParser


class PaperSummarizer:
    def __init__(
        self,
        config: Config,
        boto3_session: boto3.Session | None = None,
        profile_name: str | None = None,
        language: Language | None = None,
    ):
        self.config = config
        self.profile_name = profile_name
        self.region_name = config.resources.bedrock_region_name
        self.boto3_session = boto3_session or boto3.Session(
            profile_name=profile_name,
            region_name=self.region_name,
        )

        if self.config.summarization.paper_summarization_model_id is None:
            raise ValueError("'paper_summarization_model_id' is not set")

        self.summarization_prompt = PaperSummarizationPrompt.for_language(
            Language(language or Language.KO)
        ).get_prompt()
        self.summarization_llm = self._initialize_llm(
            self.config.summarization.paper_summarization_model_id.value
        )
        self.output_parser = HTMLTagOutputParser(
            tag_names=PaperSummarizationPrompt.OUTPUT_VARIABLES
        )

    def _initialize_llm(self, model_id: str) -> "BedrockConverse":
        # Lazy import: keeps this module importable for unit tests that mock the
        # LLM, without loading the heavy bedrock-converse / aioboto3 stack at
        # import time. (Under graphrag v3 this no longer resolves a hard pin
        # conflict — v3 relaxed botocore — but lazy loading stays for test speed.)
        from llama_index.llms.bedrock_converse import BedrockConverse

        return BedrockConverse(
            get_cross_inference_model_id(
                self.boto3_session, model_id, self.region_name
            ),
            temperature=0.0,
            max_tokens=self.config.summarization.summarization_max_tokens,
            profile_name=self.profile_name,
            region_name=self.region_name,
            timeout=900,
        )

    async def summarize(self, paper: Paper) -> str | dict[str, str]:
        if not paper.content:
            raise ValueError("Paper content cannot be empty")

        messages = self.summarization_prompt.format_messages(content=paper.content)
        # Cache the large, stable paper-content prefix so repeated summarizations
        # of the same paper (retries, multi-language/format runs) are cheaper.
        messages = apply_cache_point(
            messages, enabled=self.config.summarization.enable_prompt_caching
        )
        response = await self.summarization_llm.achat(messages)
        if not response.message.content:
            raise ValueError("Empty response from LLM")
        response_content = str(response.message.content)
        if not response_content.strip():
            raise ValueError("Empty response content from LLM")
        result = self.output_parser.parse(response_content)
        logger.debug("Summarization result: %s", result)
        return result

    async def summarize_batch(
        self, papers: list[Paper], max_concurrent: int = 5
    ) -> dict[str, str | dict[str, str]]:
        """Summarize multiple papers in parallel with rate limiting."""
        semaphore = asyncio.Semaphore(max_concurrent)

        async def _summarize_with_limit(
            paper: Paper,
        ) -> tuple[str, str | dict[str, str]]:
            async with semaphore:
                result = await self.summarize(paper)
                return paper.arxiv_id, result

        tasks = [_summarize_with_limit(paper) for paper in papers]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        output: dict[str, str | dict[str, str]] = {}
        for result in results:
            if isinstance(result, Exception):
                logger.error("Summarization failed: %s", result)
                continue
            arxiv_id, summary = result
            output[arxiv_id] = summary

        return output
