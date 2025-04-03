from typing import Dict, List, Optional, Union, cast
import boto3
from llama_index.llms.bedrock_converse import BedrockConverse
from .aws_helpers import get_cross_inference_model_id
from .constants import Language
from .fetcher import Paper
from .logger import logger
from .prompts import PaperSummarizationPrompt
from .utils import HTMLTagOutputParser
from paper_bridge.summarizer.configs import Config


class PaperSummarizer:
    def __init__(
        self,
        config: Config,
        boto3_session: Optional[boto3.Session] = None,
        profile_name: Optional[str] = None,
        language: Optional[Language] = None,
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
            language or Language.KO
        ).get_prompt()
        self.summarization_llm = self._initialize_llm(
            self.config.summarization.paper_summarization_model_id.value
        )
        self.output_parser = HTMLTagOutputParser(
            tag_names=PaperSummarizationPrompt.OUTPUT_VARIABLES
        )

    def _initialize_llm(self, model_id: str) -> BedrockConverse:
        return BedrockConverse(
            get_cross_inference_model_id(
                self.boto3_session, model_id, self.region_name
            ),
            temperature=0.0,
            max_tokens=16384,
            profile_name=self.profile_name,
            region_name=self.region_name,
            timeout=900,
        )

    async def summarize(self, paper: Paper) -> Union[str, Dict[str, str]]:
        if not paper.content:
            raise ValueError("Paper content cannot be empty")

        messages = self.summarization_prompt.format_messages(content=paper.content)
        response = await self.summarization_llm.achat(messages)
        response_content = cast(str, response.message.content)
        result = self.output_parser.parse(response_content)
        logger.debug("Summarization result: %s", result)
        return result

    async def summarize_batch(
        self, papers: List[Paper]
    ) -> Dict[str, Union[str, Dict[str, str]]]:
        results = {}
        for paper in papers:
            summary = await self.summarize(paper)
            results[paper.arxiv_id] = summary
        return results
