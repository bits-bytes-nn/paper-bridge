import nest_asyncio
import os
from typing import Any, Dict, List, Optional, Union
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

BasePostProcessor = Union[
    StatementDiversityPostProcessor, StatementEnhancementPostProcessor
]


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

        self.query_engine = LexicalGraphQueryEngine.for_traversal_based_search(
            self.graph_store, self.vector_store
        )

        logger.info("Retriever initialized successfully")

    def use_traversal_based_retriever(
        self,
        retrievers: Optional[List[WeightedTraversalBasedRetrieverType]] = None,
    ) -> None:
        default_retrievers = [ChunkBasedSearch]
        self.query_engine = LexicalGraphQueryEngine.for_traversal_based_search(
            self.graph_store, self.vector_store, retrievers=retrievers
        )
        logger.info(
            f"Using traversal-based retriever with {retrievers or default_retrievers}"
        )

    def use_semantic_guided_retriever(
        self, retrievers: Optional[List[SemanticGuidedRetrieverType]] = None
    ) -> None:
        default_retrievers = [
            StatementCosineSimilaritySearch,
            KeywordRankingSearch,
            SemanticBeamGraphSearch,
        ]
        self.query_engine = LexicalGraphQueryEngine.for_semantic_guided_search(
            self.graph_store,
            self.vector_store,
            retrievers=retrievers or default_retrievers,
        )
        logger.info(
            f"Using semantic-guided retriever with {retrievers or default_retrievers}"
        )

    def use_reranking_beam_search(
        self,
        use_gpu: bool = False,
        gpu_id: int = 0,
        beam_width: int = 100,
        max_depth: int = 8,
        top_k: int = 50,
        max_keywords: int = 10,
        batch_size: int = 128,
    ) -> None:
        cosine_retriever = StatementCosineSimilaritySearch(
            vector_store=self.vector_store, graph_store=self.graph_store, top_k=top_k
        )

        keyword_retriever = KeywordRankingSearch(
            vector_store=self.vector_store,
            graph_store=self.graph_store,
            max_keywords=max_keywords,
        )

        reranker = (
            BGEReranker(gpu_id=gpu_id, batch_size=batch_size)
            if use_gpu
            else SentenceReranker(batch_size=batch_size)
        )

        logger.info(
            f"Using {'BGEReranker with GPU (ID: ' + str(gpu_id) + ')' if use_gpu else 'SentenceReranker (CPU)'}"
        )

        beam_retriever = RerankingBeamGraphSearch(
            vector_store=self.vector_store,
            graph_store=self.graph_store,
            reranker=reranker,
            initial_retrievers=[cosine_retriever, keyword_retriever],
            max_depth=max_depth,
            beam_width=beam_width,
        )

        self.query_engine = LexicalGraphQueryEngine.for_semantic_guided_search(
            self.graph_store,
            self.vector_store,
            retrievers=[cosine_retriever, keyword_retriever, beam_retriever],
        )

        logger.info(
            f"Using reranking beam search with beam width {beam_width} and max depth {max_depth}"
        )

    def use_post_processors(
        self,
        use_diversity: bool = True,
        use_enhancement: bool = True,
        use_gpu_reranker: bool = False,
        gpu_id: int = 0,
        batch_size: int = 128,
        similarity_threshold: float = 0.975,
    ) -> None:
        post_processors: List[PostProcessorsType] = []

        if use_gpu_reranker:
            post_processors.append(BGEReranker(gpu_id=gpu_id, batch_size=batch_size))
            logger.info(f"Using BGEReranker with GPU (ID: {gpu_id})")
        else:
            post_processors.append(SentenceReranker(batch_size=batch_size))
            logger.info("Using SentenceReranker (CPU)")

        if use_diversity:
            post_processors.append(
                StatementDiversityPostProcessor(
                    similarity_threshold=similarity_threshold
                )
            )
            logger.info(
                f"Using StatementDiversityPostProcessor with threshold {similarity_threshold}"
            )

        if use_enhancement:
            post_processors.append(StatementEnhancementPostProcessor())
            logger.info("Using StatementEnhancementPostProcessor")

        self.query_engine = LexicalGraphQueryEngine.for_semantic_guided_search(
            self.graph_store, self.vector_store, post_processors=post_processors
        )

    def query(self, query_text: str) -> Dict[str, Any]:
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
