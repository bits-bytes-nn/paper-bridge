import asyncio
import nest_asyncio
import os
from collections import defaultdict
from pprint import pformat
from typing import Any, Dict, List, Optional, Tuple, Union, cast
import boto3
from graphrag_toolkit import LexicalGraphQueryEngine, set_logging_config
from graphrag_toolkit.lexical_graph_query_engine import PostProcessorsType
from graphrag_toolkit.retrieval.post_processors import (
    BGEReranker,
    SentenceReranker,
    StatementDiversityPostProcessor,
    StatementEnhancementPostProcessor,
)
from graphrag_toolkit.retrieval.retrievers import (
    ChunkBasedSearch,
    KeywordRankingSearch,
    RerankingBeamGraphSearch,
    SemanticBeamGraphSearch,
    SemanticGuidedRetrieverType,
    StatementCosineSimilaritySearch,
    WeightedTraversalBasedRetrieverType,
)
from graphrag_toolkit.storage import GraphStoreFactory, VectorStoreFactory
from llama_index.llms.bedrock_converse import BedrockConverse
from .aws_helpers import get_cross_inference_model_id, get_ssm_param_value
from .constants import Language, SSMParams
from .fetcher import Paper
from .logger import logger
from .prompts import RetrievalSummarizationPrompt
from .utils import HTMLTagOutputParser, measure_execution_time
from paper_bridge.summarizer.configs import Config

nest_asyncio.apply()


class Retriever:
    def __init__(
        self,
        config: Config,
        boto3_session: boto3.Session,
    ):
        set_logging_config(
            "DEBUG" if os.getenv("LOG_LEVEL", "INFO").upper() == "DEBUG" else "INFO"
        )

        self.boto3_session = boto3_session
        self.config = config

        base_path = f"/{config.resources.project_name}-{config.resources.stage}"
        graph_endpoint = get_ssm_param_value(
            boto3_session, f"{base_path}/{SSMParams.NEPTUNE_ENDPOINT.value}"
        )
        vector_endpoint = get_ssm_param_value(
            boto3_session, f"{base_path}/{SSMParams.OPENSEARCH_ENDPOINT.value}"
        )
        if graph_endpoint is None or vector_endpoint is None:
            raise ValueError("Graph or vector endpoint not found")

        self.graph_store = GraphStoreFactory.for_graph_store(
            f"neptune-db://{graph_endpoint}"
        )
        self.vector_store = VectorStoreFactory.for_vector_store(
            f"aoss://{vector_endpoint}"
        )
        self.query_engine = None

        self._initialize_retriever()

    def _initialize_retriever(self) -> None:
        if (
            self.config.retrieval.traversal_based_or_semantic_guided
            == "traversal_based"
        ):
            self.use_traversal_based_retriever(self.config.retrieval.set_subretriever)
        else:
            self.use_semantic_guided_retriever(self.config.retrieval.set_subretriever)

        if self.config.retrieval.use_reranking_beam_search:
            self.use_reranking_beam_search()

        if self.config.retrieval.use_post_processors:
            self.use_post_processors()

    def use_traversal_based_retriever(self, set_subretrievers: bool = False) -> None:
        subretrievers: Optional[List[WeightedTraversalBasedRetrieverType]] = None
        if set_subretrievers:
            subretrievers = [ChunkBasedSearch]

        self.query_engine = LexicalGraphQueryEngine.for_traversal_based_search(
            self.graph_store, self.vector_store, retrievers=subretrievers
        )
        logger.info(
            f"Using traversal-based retriever with {subretrievers or 'default options'}"
        )

    def use_semantic_guided_retriever(self, set_subretrievers: bool = False) -> None:
        subretrievers: Optional[List[SemanticGuidedRetrieverType]] = None
        if set_subretrievers:
            subretrievers = [
                StatementCosineSimilaritySearch,
                KeywordRankingSearch,
                SemanticBeamGraphSearch,
            ]

        self.query_engine = LexicalGraphQueryEngine.for_semantic_guided_search(
            self.graph_store, self.vector_store, retrievers=subretrievers
        )
        logger.info(
            f"Using semantic-guided retriever with {subretrievers or 'default configuration'}"
        )

    def use_reranking_beam_search(self) -> None:
        cosine_retriever = StatementCosineSimilaritySearch(
            vector_store=self.vector_store, graph_store=self.graph_store
        )

        keyword_retriever = KeywordRankingSearch(
            vector_store=self.vector_store, graph_store=self.graph_store
        )

        reranker = (
            BGEReranker(gpu_id=self.config.retrieval.gpu_id)
            if self.config.retrieval.use_gpu_reranker
            else SentenceReranker()
        )

        logger.info(
            f"Using {'BGEReranker with GPU (ID: ' + str(self.config.retrieval.gpu_id) + ')' if self.config.retrieval.use_gpu_reranker else 'SentenceReranker (CPU)'}"
        )

        beam_retriever = RerankingBeamGraphSearch(
            vector_store=self.vector_store,
            graph_store=self.graph_store,
            reranker=reranker,
            initial_retrievers=[cosine_retriever, keyword_retriever],
        )

        self.query_engine = LexicalGraphQueryEngine.for_semantic_guided_search(
            self.graph_store,
            self.vector_store,
            retrievers=[cosine_retriever, keyword_retriever, beam_retriever],
        )

        logger.info("Using reranking beam search with default configuration")

    def use_post_processors(self) -> None:
        post_processors: Optional[List[PostProcessorsType]] = []

        if self.config.retrieval.use_gpu_reranker:
            post_processors.append(BGEReranker(gpu_id=self.config.retrieval.gpu_id))
            logger.info(
                f"Using BGEReranker with GPU (ID: {self.config.retrieval.gpu_id})"
            )
        else:
            post_processors.append(SentenceReranker())
            logger.info("Using SentenceReranker (CPU)")

        if self.config.retrieval.use_diversity:
            post_processors.append(StatementDiversityPostProcessor())
            logger.info(
                "Using StatementDiversityPostProcessor with default configuration"
            )

        if self.config.retrieval.use_enhancement:
            post_processors.append(StatementEnhancementPostProcessor())
            logger.info("Using StatementEnhancementPostProcessor")

        post_processors = post_processors if post_processors else None

        self.query_engine = LexicalGraphQueryEngine.for_semantic_guided_search(
            self.graph_store,
            self.vector_store,
            post_processors=post_processors,
        )

    @measure_execution_time
    def query(self, query_text: str) -> Dict[str, Any]:
        if not self.query_engine:
            raise ValueError("Query engine not initialized")

        if not query_text.strip():
            raise ValueError("Query text cannot be empty")

        logger.debug(f"Executing query: {query_text}")
        response = self.query_engine.query(query_text)

        result = {
            "response": response.response,
            "source_nodes": [
                {"text": node.text, "metadata": node.metadata}
                for node in response.source_nodes
            ],
        }

        return result


class PaperRetriever:
    DEFAULT_QUERIES: List[str] = [
        "What are the recent major developments in the technical field of this paper? What are the key differences between this paper and recently published similar papers? Please analyze the research trends in the field related to this paper and insights that can be derived from them.\nPaper content:"
    ]

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
            region_name=config.resources.bedrock_region_name,
        )

        if self.config.retrieval.retrieval_summarization_model_id is None:
            raise ValueError("'retrieval_summarization_model_id' is not set")

        self.retriever = Retriever(config, self.boto3_session)
        self.retrieval_prompt = RetrievalSummarizationPrompt.for_language(
            language or Language.KO
        ).get_prompt()
        self.retrieval_llm = self._initialize_llm(
            self.config.retrieval.retrieval_summarization_model_id.value
        )
        self.output_parser = HTMLTagOutputParser(
            tag_names=RetrievalSummarizationPrompt.OUTPUT_VARIABLES
        )

    def _initialize_llm(self, model_id: str) -> BedrockConverse:
        return BedrockConverse(
            get_cross_inference_model_id(
                self.boto3_session, model_id, self.region_name
            ),
            temperature=0.0,
            max_tokens=8192,
            profile_name=self.profile_name,
            region_name=self.region_name,
            timeout=900,
        )

    async def retrieve_batch(
        self, papers: List[Paper]
    ) -> Dict[str, Union[str, Dict[str, str]]]:
        retrieval_tasks = [
            self.process_query(paper, query)
            for paper in papers
            for query in self.DEFAULT_QUERIES
        ]

        retrieval_results = await asyncio.gather(*retrieval_tasks)

        responses = defaultdict(list)
        for result in retrieval_results:
            arxiv_id = result.pop("arxiv_id")
            responses[arxiv_id].append(result)

        logger.debug("Retrieval results: %s", pformat(dict(responses)))

        processing_tasks = [
            self.process_response(arxiv_id, contexts)
            for arxiv_id, contexts in responses.items()
        ]

        processing_results = await asyncio.gather(*processing_tasks)
        processed_results = {
            arxiv_id: result for arxiv_id, result in processing_results
        }

        logger.debug("Processed retrieval results: %s", pformat(processed_results))
        return processed_results

    async def process_query(
        self,
        paper: Paper,
        query: str,
        include_content: bool = False,
    ) -> Dict[str, Any]:
        arxiv_id = paper.arxiv_id
        paper_content = paper.content or ""
        query_text = f"{query} {paper_content}"

        answer = self.retriever.query(query_text=query_text)
        result = {"arxiv_id": arxiv_id, "query": query, "answer": answer}

        if include_content:
            result["paper_content"] = paper_content

        return result

    async def process_response(
        self,
        arxiv_id: str,
        contexts: List[Dict[str, Any]],
    ) -> Tuple[str, Union[str, Dict[str, str]]]:
        messages = self.retrieval_prompt.format_messages(context=str(contexts))
        response = await self.retrieval_llm.achat(messages)
        response_content = cast(str, response.message.content)
        result = self.output_parser.parse(response_content)

        return arxiv_id, result
