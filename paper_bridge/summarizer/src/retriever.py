import nest_asyncio
import os
from typing import Any, Dict, List, Optional
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
from .aws_helpers import get_ssm_param_value
from .logger import logger
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

        project_name = config.resources.project_name
        stage = config.resources.stage

        graph_endpoint = get_ssm_param_value(
            boto3_session, f"/{project_name}-{stage}/neptune/endpoint"
        )
        vector_endpoint = get_ssm_param_value(
            boto3_session, f"/{project_name}-{stage}/opensearch/endpoint"
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
            f"Using semantic-guided retriever with {subretrievers or 'default options'}"
        )

    def use_reranking_beam_search(self) -> None:
        cosine_retriever = StatementCosineSimilaritySearch(
            vector_store=self.vector_store,
            graph_store=self.graph_store,
            top_k=self.config.retrieval.top_k,
        )

        keyword_retriever = KeywordRankingSearch(
            vector_store=self.vector_store,
            graph_store=self.graph_store,
            max_keywords=self.config.retrieval.max_keywords,
        )

        reranker = (
            BGEReranker(
                gpu_id=self.config.retrieval.gpu_id,
                batch_size=self.config.retrieval.batch_size,
            )
            if self.config.retrieval.use_gpu_reranker
            else SentenceReranker(batch_size=self.config.retrieval.batch_size)
        )

        logger.info(
            f"Using {'BGEReranker with GPU (ID: ' + str(self.config.retrieval.gpu_id) + ')' if self.config.retrieval.use_gpu_reranker else 'SentenceReranker (CPU)'}"
        )

        beam_retriever = RerankingBeamGraphSearch(
            vector_store=self.vector_store,
            graph_store=self.graph_store,
            reranker=reranker,
            initial_retrievers=[cosine_retriever, keyword_retriever],
            max_depth=self.config.retrieval.max_depth,
            beam_width=self.config.retrieval.beam_width,
        )

        self.query_engine = LexicalGraphQueryEngine.for_semantic_guided_search(
            self.graph_store,
            self.vector_store,
            retrievers=[cosine_retriever, keyword_retriever, beam_retriever],
        )

        logger.info(
            f"Using reranking beam search with beam width {self.config.retrieval.beam_width} and max depth {self.config.retrieval.max_depth}"
        )

    def use_post_processors(self) -> None:
        post_processors: Optional[List[PostProcessorsType]] = []

        if self.config.retrieval.use_gpu_reranker:
            post_processors.append(
                BGEReranker(
                    gpu_id=self.config.retrieval.gpu_id,
                    batch_size=self.config.retrieval.batch_size,
                )
            )
            logger.info(
                f"Using BGEReranker with GPU (ID: {self.config.retrieval.gpu_id})"
            )
        else:
            post_processors.append(
                SentenceReranker(batch_size=self.config.retrieval.batch_size)
            )
            logger.info("Using SentenceReranker (CPU)")

        if self.config.retrieval.use_diversity:
            post_processors.append(
                StatementDiversityPostProcessor(
                    similarity_threshold=self.config.retrieval.similarity_threshold
                )
            )
            logger.info(
                f"Using StatementDiversityPostProcessor with threshold {self.config.retrieval.similarity_threshold}"
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

    def query(self, query_text: str) -> Dict[str, Any]:
        if not self.query_engine:
            raise ValueError("Query engine not initialized")

        if not query_text.strip():
            raise ValueError("Query text cannot be empty")

        logger.info(f"Executing query: {query_text}")
        response = self.query_engine.query(query_text)

        result = {
            "response": response.response,
            "source_nodes": [
                {"text": node.text, "metadata": node.metadata}
                for node in response.source_nodes
            ],
        }

        return result
