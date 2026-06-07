from __future__ import annotations

import asyncio
import os
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from pprint import pformat
from typing import TYPE_CHECKING, Any

import boto3
import nest_asyncio

from paper_bridge.shared import apply_cache_point
from paper_bridge.summarizer.configs import Config

from .aws_helpers import get_cross_inference_model_id, get_ssm_param_value
from .constants import Format, Language, SSMParams
from .fetcher import Paper
from .logger import logger
from .prompts import RetrievalSummarizationPrompt
from .utils import HTMLTagOutputParser, measure_execution_time

if TYPE_CHECKING:  # imported lazily at runtime (see note below)
    from llama_index.llms.bedrock_converse import BedrockConverse

# NOTE: graphrag-lexical-graph (v3) and llama-index-llms-bedrock-converse are
# imported lazily inside the methods that use them. The v3 toolkit + converse now
# co-install cleanly (v3 relaxed the botocore pin that previously conflicted with
# bedrock-converse's aioboto3 chain), so eager import would work — but keeping the
# imports lazy keeps this module importable for unit tests without the heavy
# graph/embedding stack and keeps test collection fast. ``from __future__ import
# annotations`` keeps the graphrag type names in signatures from being evaluated
# at import time.

nest_asyncio.apply()


class Retriever:
    # Must match the indexer's embeddings model (indexer config.py:
    # COHERE_EMBED_TEXT_V3, dimensions=1024). The indexer writes OpenSearch vectors
    # with this model; queries MUST embed with the same model/dimensions or the
    # cosine search returns nothing. Kept as constants (not summarizer config) so
    # they cannot silently drift from the indexer at the call site.
    EMBED_MODEL_ID: str = "cohere.embed-english-v3"
    EMBED_DIMENSIONS: int = 1024

    def __init__(
        self,
        config: Config,
        boto3_session: boto3.Session,
    ):
        from graphrag_toolkit.lexical_graph import (
            GraphRAGConfig,
            set_logging_config,
        )
        from graphrag_toolkit.lexical_graph.storage import (
            GraphStoreFactory,
            VectorStoreFactory,
        )

        set_logging_config(
            "DEBUG" if os.getenv("LOG_LEVEL", "INFO").upper() == "DEBUG" else "INFO"
        )

        self.boto3_session = boto3_session
        self.config = config
        self.max_chars = 200000

        # Point graphrag's query engine at OUR models. Left at graphrag's defaults
        # it uses a hard-coded response_llm (Claude 3.7 Sonnet with a bogus
        # profile_name) and a default embed_model that does NOT match the Cohere
        # English v3 / 1024-dim vectors the indexer wrote — so retrieval queries
        # would embed with the wrong model and return nothing. response_llm must be
        # a cross-region inference profile (Claude 4.x is not available on-demand);
        # the embed model + dimensions MUST match indexer config.py (the indexer is
        # the source of truth for what is stored in OpenSearch).
        if config.retrieval.retrieval_summarization_model_id is not None:
            model_id = get_cross_inference_model_id(
                boto3_session,
                config.retrieval.retrieval_summarization_model_id.value,
                config.resources.bedrock_region_name,
            )
            GraphRAGConfig.response_llm = model_id
            # The traversal retriever's keyword provider (keyword_vss_provider)
            # invokes GraphRAGConfig.extraction_llm — NOT response_llm — to extract
            # query keywords. Left at graphrag's default it points at Claude 3.7
            # Sonnet, which is now EOL on Bedrock and raises
            # ResourceNotFoundException ("model version has reached the end of its
            # life"), aborting the whole retrieval and leaving the Slack message
            # with no GraphRAG insight. Pin it to our resolved profile too.
            GraphRAGConfig.extraction_llm = model_id
        GraphRAGConfig.embed_model = self.EMBED_MODEL_ID
        GraphRAGConfig.embed_dimensions = self.EMBED_DIMENSIONS

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
        self.query_engine = self._build_query_engine()

    def _build_query_engine(self) -> Any:
        """Build the query engine ONCE, composing the chosen base retriever with
        any reranking / post-processors.

        Previously each ``use_*`` method reassigned ``self.query_engine``, so
        enabling reranking or post-processors silently replaced a traversal-based
        engine with a (deprecated) semantic-guided one — switching retrieval mode
        behind the operator's back. This builds a single engine whose
        post-processors attach to the selected base mode.
        """
        from graphrag_toolkit.lexical_graph import LexicalGraphQueryEngine

        rc = self.config.retrieval
        post_processors = self._build_post_processors()

        if rc.traversal_based_or_semantic_guided == "traversal_based":
            retrievers = (
                self._traversal_subretrievers() if rc.set_subretriever else None
            )
            logger.info(
                "Using traversal-based retriever with %s",
                retrievers or "default options",
            )
            return LexicalGraphQueryEngine.for_traversal_based_search(
                self.graph_store,
                self.vector_store,
                retrievers=retrievers,
                post_processors=post_processors,
            )

        retrievers = self._semantic_subretrievers() if rc.set_subretriever else None
        logger.info(
            "Using semantic-guided retriever with %s",
            retrievers or "default configuration",
        )
        return LexicalGraphQueryEngine.for_semantic_guided_search(
            self.graph_store,
            self.vector_store,
            retrievers=retrievers,
            post_processors=post_processors,
        )

    def _traversal_subretrievers(self) -> list[Any]:
        from graphrag_toolkit.lexical_graph.retrieval.retrievers import ChunkBasedSearch

        return [ChunkBasedSearch]

    def _semantic_subretrievers(self) -> list[Any]:
        # Semantic-guided retrievers are deprecated in v3 (slated for removal) but
        # still importable from the retrievers package; prefer traversal-based
        # search (see eval/graphrag-migration.md §4).
        from graphrag_toolkit.lexical_graph.retrieval.retrievers import (
            KeywordRankingSearch,
            SemanticBeamGraphSearch,
            StatementCosineSimilaritySearch,
        )

        return [
            StatementCosineSimilaritySearch,
            KeywordRankingSearch,
            SemanticBeamGraphSearch,
        ]

    def _build_post_processors(self) -> list[Any] | None:
        """Assemble the post-processor chain from config (reranker first, then
        optional diversity / enhancement). Returns None when none are enabled."""
        rc = self.config.retrieval
        post_processors: list[Any] = []

        if rc.use_reranking_beam_search or rc.use_post_processors:
            if rc.use_gpu_reranker:
                # BGEReranker is no longer re-exported from the post_processors
                # package __init__ in v3; import it from its module. It pulls in
                # FlagEmbedding/torch, so this path is GPU-host only.
                from graphrag_toolkit.lexical_graph.retrieval.post_processors.bge_reranker import (  # noqa: E501
                    BGEReranker,
                )

                post_processors.append(BGEReranker(gpu_id=rc.gpu_id))
                logger.info("Using BGEReranker with GPU (ID: %s)", rc.gpu_id)
            else:
                from graphrag_toolkit.lexical_graph.retrieval.post_processors import (
                    SentenceReranker,
                )

                post_processors.append(SentenceReranker())
                logger.info("Using SentenceReranker (CPU)")

        if rc.use_post_processors and rc.use_diversity:
            from graphrag_toolkit.lexical_graph.retrieval.post_processors import (
                StatementDiversityPostProcessor,
            )

            post_processors.append(StatementDiversityPostProcessor())
            logger.info("Using StatementDiversityPostProcessor")

        if rc.use_post_processors and rc.use_enhancement:
            from graphrag_toolkit.lexical_graph.retrieval.post_processors import (
                StatementEnhancementPostProcessor,
            )

            post_processors.append(StatementEnhancementPostProcessor())
            logger.info("Using StatementEnhancementPostProcessor")

        return post_processors or None

    @measure_execution_time
    def query(self, query_text: str) -> dict[str, Any]:
        if not self.query_engine:
            raise ValueError("Query engine not initialized")

        if not query_text.strip():
            raise ValueError("Query text cannot be empty")

        logger.debug("Executing query: %s", query_text)
        response = self.query_engine.query(query_text[: self.max_chars])

        result = {
            "response": response.response,
            "source_nodes": [
                {"text": node.text, "metadata": node.metadata}
                for node in response.source_nodes
            ],
        }

        return result


class PaperRetriever:
    DEFAULT_QUERIES: list[str] = [
        "What are the recent major developments in the specific technical field of this paper? What are the key differences between this paper and other papers that attempted to solve similar problems? How does this paper's approach, methodology, or results differ from previous work? Please analyze the research trends in the field related to this paper and insights that can be derived from them.\nPaper content:"
    ]
    MAX_WORKERS: int = 4

    def __init__(
        self,
        config: Config,
        boto3_session: boto3.Session | None = None,
        profile_name: str | None = None,
        language: Language | None = None,
        output_format: Format | None = None,
    ):
        self.config = config
        self.profile_name = profile_name
        self.region_name = config.resources.bedrock_region_name
        self.boto3_session = boto3_session or boto3.Session(
            profile_name=profile_name,
            region_name=config.resources.bedrock_region_name,
        )
        self._executor: ThreadPoolExecutor | None = ThreadPoolExecutor(
            max_workers=self.MAX_WORKERS
        )

        if self.config.retrieval.retrieval_summarization_model_id is None:
            raise ValueError("'retrieval_summarization_model_id' is not set")

        self.retriever = Retriever(config, self.boto3_session)
        if output_format == Format.SLACK:
            language_to_use = Language.KO
        else:
            language_to_use = language or Language.KO

        self.retrieval_prompt = RetrievalSummarizationPrompt.for_language_and_format(
            Language(language_to_use), Format(output_format or Format.HTML)
        ).get_prompt()
        self.retrieval_llm = self._initialize_llm(
            self.config.retrieval.retrieval_summarization_model_id.value
        )
        self.output_parser = HTMLTagOutputParser(
            tag_names=RetrievalSummarizationPrompt.OUTPUT_VARIABLES
        )

    def close(self) -> None:
        """Shut down the query thread pool. Idempotent."""
        executor = getattr(self, "_executor", None)
        if executor is not None:
            executor.shutdown(wait=False)
            self._executor = None

    def __enter__(self) -> PaperRetriever:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def __del__(self) -> None:
        # Safety net if the caller forgets the context manager; GC'ing a
        # PaperRetriever should not leak the 4 worker threads.
        self.close()

    def _initialize_llm(self, model_id: str) -> BedrockConverse:
        from llama_index.llms.bedrock_converse import BedrockConverse

        return BedrockConverse(
            get_cross_inference_model_id(
                self.boto3_session, model_id, self.region_name
            ),
            temperature=0.0,
            max_tokens=self.config.retrieval.retrieval_max_tokens,
            profile_name=self.profile_name,
            region_name=self.region_name,
            timeout=900,
        )

    async def retrieve_batch(
        self, papers: list[Paper]
    ) -> dict[str, str | dict[str, str]]:
        retrieval_tasks = [
            self.process_query(paper, query)
            for paper in papers
            for query in self.DEFAULT_QUERIES
        ]

        # return_exceptions=True so one paper's failure doesn't wipe retrievals
        # for the whole batch (mirrors summarize_batch's per-item isolation).
        retrieval_results = await asyncio.gather(
            *retrieval_tasks, return_exceptions=True
        )

        responses = defaultdict(list)
        for result in retrieval_results:
            if isinstance(result, BaseException):
                logger.error("Retrieval query failed: %s", result)
                continue
            arxiv_id = result.pop("arxiv_id")
            responses[arxiv_id].append(result)

        logger.debug("Retrieval results: %s", pformat(dict(responses)))

        processing_tasks = [
            self.process_response(arxiv_id, contexts)
            for arxiv_id, contexts in responses.items()
        ]

        processing_results = await asyncio.gather(
            *processing_tasks, return_exceptions=True
        )
        processed_results: dict[str, str | dict[str, str]] = {}
        for result in processing_results:
            if isinstance(result, BaseException):
                logger.error("Retrieval summarization failed: %s", result)
                continue
            arxiv_id, summary = result
            processed_results[arxiv_id] = summary

        logger.debug("Processed retrieval results: %s", pformat(processed_results))
        return processed_results

    # Cap the paper representation injected into the retrieval query. The query
    # drives graph/vector matching against the embedding model (Cohere v3, 512-token
    # window); stuffing the full paper (up to 200k chars) overflows it ~100x and
    # biases retrieval toward the paper's own statements instead of *related* work.
    # A compact title + abstract keeps the query targeted at the comparison intent.
    MAX_QUERY_PAPER_CHARS: int = 2000

    async def process_query(
        self,
        paper: Paper,
        query: str,
    ) -> dict[str, Any]:
        arxiv_id = paper.arxiv_id
        query_text = f"{query} {self._build_query_representation(paper)}"

        # Run synchronous query in thread pool to avoid blocking
        loop = asyncio.get_event_loop()
        query_func = partial(self.retriever.query, query_text=query_text)
        answer = await loop.run_in_executor(self._executor, query_func)

        return {"arxiv_id": arxiv_id, "query": query, "answer": answer}

    def _build_query_representation(self, paper: Paper) -> str:
        """Compact paper representation for the retrieval query: title + abstract,
        falling back to a truncated content slice. Kept small so it fits the
        embedding window and targets *related* work rather than self-similarity."""
        title = (paper.title or "").strip()
        abstract = (paper.summary or "").strip()
        rep = f"{title}\n{abstract}".strip()
        if not rep:
            rep = (paper.content or "").strip()
        return rep[: self.MAX_QUERY_PAPER_CHARS]

    async def process_response(
        self,
        arxiv_id: str,
        contexts: list[dict[str, Any]],
    ) -> tuple[str, str | dict[str, str]]:
        messages = self.retrieval_prompt.format_messages(context=str(contexts))
        # Cache the large GraphRAG context prefix to cut cost/latency on repeats.
        messages = apply_cache_point(
            messages, enabled=self.config.retrieval.enable_prompt_caching
        )
        response = await self.retrieval_llm.achat(messages)
        if not response.message.content:
            raise ValueError("Empty response from LLM")
        response_content = str(response.message.content)
        if not response_content.strip():
            raise ValueError("Empty response content from LLM")
        result = self.output_parser.parse(response_content)

        return arxiv_id, result
