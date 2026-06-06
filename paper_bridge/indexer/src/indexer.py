import os
from datetime import UTC, datetime
from pprint import pformat

import boto3
import nest_asyncio
from graphrag_toolkit.lexical_graph import (
    ExtractionConfig,
    GraphRAGConfig,
    IndexingConfig,
    set_logging_config,
)
from graphrag_toolkit.lexical_graph.indexing import sink
from graphrag_toolkit.lexical_graph.indexing.build import (
    BuildPipeline,
    Checkpoint,
    GraphConstruction,
    VectorIndexing,
)
from graphrag_toolkit.lexical_graph.indexing.constants import PROPOSITIONS_KEY
from graphrag_toolkit.lexical_graph.indexing.extract import (
    BatchConfig,
    BatchLLMPropositionExtractorSync,
    BatchTopicExtractorSync,
    ExtractionPipeline,
    LLMPropositionExtractor,
    PreferredValuesProvider,
    TopicExtractor,
    default_preferred_values,
)
from graphrag_toolkit.lexical_graph.indexing.model import SourceDocument
from graphrag_toolkit.lexical_graph.storage import (
    GraphStoreFactory,
    VectorStoreFactory,
)
from graphrag_toolkit.lexical_graph.storage.graph import GraphStore
from graphrag_toolkit.lexical_graph.storage.vector import VectorStore
from graphrag_toolkit.lexical_graph.tenant_id import TenantId
from llama_index.core.node_parser import SentenceSplitter
from llama_index.core.schema import Document
from pipe import Pipe

from paper_bridge.indexer.configs.config import Config, ModelHandler

from .aws_helpers import (
    NeptuneClient,
    OpenSearchClient,
    get_account_id,
    get_cross_inference_model_id,
    get_ssm_param_value,
    summarize_deletion_results,
)
from .constants import ENTITY_CLASSIFICATIONS, SSMParams
from .fetcher import Paper
from .logger import logger

DEFAULT_CHECKPOINT_DATE_FORMAT: str = "%Y-%m-%d"

# graphrag v3's Extraction/Build pipelines pass tenant_id straight into
# Checkpoint.add_filter, and CheckpointFilter.tenant_id is now a non-optional
# TenantId (pydantic rejects the None default). We are single-tenant, so use the
# default-tenant instance (TenantId() == DEFAULT_TENANT_ID) everywhere.
DEFAULT_TENANT_ID: TenantId = TenantId()

nest_asyncio.apply()


class ProcessingError(Exception):
    pass


class DocumentProcessor:
    def __init__(self, checkpoint: Checkpoint | None = None):
        self._checkpoint = checkpoint

    @property
    def checkpoint(self) -> Checkpoint | None:
        return self._checkpoint

    def validate_config(self) -> None:
        raise NotImplementedError("Subclasses must implement validate_config method")


class Extractor(DocumentProcessor):
    def __init__(
        self,
        config: Config,
        boto3_session: boto3.Session,
        graph_store: GraphStore,
        checkpoint: Checkpoint | None = None,
        enable_batch_inference: bool = False,
    ):
        super().__init__(checkpoint)
        self._validate_input_params(
            config.indexing.chunk_size, config.indexing.chunk_overlap
        )

        self.batch_config = self._setup_batch_config(
            config, boto3_session, enable_batch_inference
        )
        self._init_components(config, graph_store)
        self.validate_config()

    @staticmethod
    def _validate_input_params(chunk_size: int, chunk_overlap: int) -> None:
        if chunk_size <= 0:
            raise ValueError("Chunk size must be positive")
        if not 0 <= chunk_overlap < chunk_size:
            raise ValueError(
                "Chunk overlap must be non-negative and less than chunk size"
            )

    @staticmethod
    def _setup_batch_config(
        config: Config, boto3_session: boto3.Session, enable_batch_inference: bool
    ) -> BatchConfig | None:
        if not enable_batch_inference:
            return None

        base_path = f"/{config.resources.project_name}-{config.resources.stage}"
        iam_role_name = get_ssm_param_value(
            boto3_session, f"{base_path}/iam-bedrock-inference"
        )

        if not config.resources.s3_bucket_name or not iam_role_name:
            logger.warning("Batch inference enabled but missing S3 bucket or IAM role")
            return None

        logger.info("Using batch configuration with role: '%s'", iam_role_name)
        return BatchConfig(
            region=config.resources.default_region_name,
            bucket_name=str(config.resources.s3_bucket_name),
            key_prefix=config.resources.s3_key_prefix,
            role_arn=f"arn:aws:iam::{get_account_id(boto3_session)}:role/{iam_role_name}",
        )

    def _init_components(
        self,
        config: Config,
        graph_store: GraphStore,
    ) -> None:
        self._splitter = SentenceSplitter(
            chunk_size=config.indexing.chunk_size,
            chunk_overlap=config.indexing.chunk_overlap,
        )

        extraction_config = ExtractionConfig(
            enable_proposition_extraction=True,
            preferred_entity_classifications=ENTITY_CLASSIFICATIONS,
        )

        self._indexing_config = IndexingConfig(
            chunking=[self._splitter],
            extraction=extraction_config,
            batch_config=self.batch_config,
        )

        self._proposition_extractor = self._create_proposition_extractor()
        self._topic_extractor = self._create_topic_extractor(graph_store)
        self._pipeline = self._create_pipeline()

    def _create_proposition_extractor(
        self,
    ) -> BatchLLMPropositionExtractorSync | LLMPropositionExtractor:
        return (
            BatchLLMPropositionExtractorSync(batch_config=self.batch_config)
            if self.batch_config
            else LLMPropositionExtractor()
        )

    def _create_topic_extractor(
        self, graph_store: GraphStore
    ) -> BatchTopicExtractorSync | TopicExtractor:
        entity_provider = self._create_entity_provider(graph_store)
        common_params = {
            "source_metadata_field": PROPOSITIONS_KEY,
            "entity_classification_provider": entity_provider,
        }

        return (
            BatchTopicExtractorSync(batch_config=self.batch_config, **common_params)
            if self.batch_config
            else TopicExtractor(**common_params)
        )

    @staticmethod
    def _create_entity_provider(graph_store: GraphStore) -> PreferredValuesProvider:
        # graphrag v3 replaced the graph-scoped value store
        # (GraphScopedValueStore + ScopedValueProvider + DEFAULT_SCOPE) with a
        # static preferred-values provider. ENTITY_CLASSIFICATIONS is a fixed
        # constant, so this is functionally equivalent (no graph-backed
        # persistence of the classification scope, which we did not rely on).
        # ``graph_store`` is retained for call-site / signature compatibility.
        return default_preferred_values(ENTITY_CLASSIFICATIONS)

    def _create_pipeline(self) -> Pipe:
        components = [
            self._splitter,
            self._proposition_extractor,
            self._topic_extractor,
        ]
        return ExtractionPipeline.create(
            components=components,
            num_workers=GraphRAGConfig.extraction_num_workers,
            batch_size=GraphRAGConfig.extraction_batch_size,
            checkpoint=self.checkpoint,
            tenant_id=DEFAULT_TENANT_ID,
            show_progress=True,
        )

    def validate_config(self) -> None:
        if not all(
            [
                GraphRAGConfig.extraction_num_workers > 0,
                GraphRAGConfig.extraction_batch_size > 0,
            ]
        ):
            raise ValueError("Worker count and batch size must be positive")

    def extract(self, papers: list[Paper]) -> list[SourceDocument]:
        if not papers:
            logger.warning("No papers provided for extraction")
            return []

        valid_papers = [p for p in papers if p.content]
        self._log_skipped_papers(len(papers) - len(valid_papers))

        if not valid_papers:
            logger.warning("No valid papers with content to process")
            return []

        try:
            docs = [self._create_document(paper) for paper in valid_papers]
            return list(docs | self._pipeline)
        except Exception as e:
            raise ProcessingError(f"Document extraction failed: {str(e)}") from e

    @staticmethod
    def _log_skipped_papers(skipped_count: int) -> None:
        if skipped_count:
            logger.warning("Skipped %d papers with no content", skipped_count)

    @staticmethod
    def _create_document(paper: Paper) -> Document:
        max_authors = 100
        metadata = {
            "paper_id": paper.arxiv_id,
            "title": paper.title,
            "authors": paper.authors[:max_authors],
            "published_at": paper.published_at.isoformat(),
            "created_at": datetime.now(UTC).isoformat(),
            "upvotes": paper.upvotes,
            "pdf_url": str(paper.pdf_url or ""),
            "base_date": paper.base_date,
        }
        return Document(text=paper.content, metadata=metadata)


class Builder(DocumentProcessor):
    def __init__(
        self,
        graph_store: GraphStore,
        vector_store: VectorStore,
        neptune_client: NeptuneClient,
        open_search_clients: list[OpenSearchClient],
        checkpoint: Checkpoint | None = None,
    ):
        super().__init__(checkpoint)
        self._init_components(graph_store, vector_store)
        self._neptune_client = neptune_client
        self._open_search_clients = open_search_clients
        self.validate_config()

    def _init_components(
        self,
        graph_store: GraphStore,
        vector_store: VectorStore,
    ) -> None:
        self._graph_store = graph_store
        self._vector_store = vector_store
        self._graph_construction = GraphConstruction.for_graph_store(graph_store)
        self._vector_indexing = VectorIndexing.for_vector_store(vector_store)
        self._pipeline = self._create_pipeline()

    def validate_config(self) -> None:
        if not all(
            [GraphRAGConfig.build_num_workers > 0, GraphRAGConfig.build_batch_size > 0]
        ):
            raise ValueError("Worker count and batch size must be positive")

    def _create_pipeline(self) -> Pipe:
        components = [self._graph_construction, self._vector_indexing]
        return BuildPipeline.create(
            components=components,
            num_workers=GraphRAGConfig.build_num_workers,
            batch_size=GraphRAGConfig.build_batch_size,
            batch_writes_enabled=GraphRAGConfig.batch_writes_enabled,
            checkpoint=self.checkpoint,
            tenant_id=DEFAULT_TENANT_ID,
            show_progress=True,
        )

    def build(self, extracted_docs: list[SourceDocument]) -> None:
        if not extracted_docs:
            logger.warning("No documents provided for building indices")
            return

        try:
            extracted_docs | self._pipeline | sink
            logger.info("Build pipeline completed successfully")
        except Exception as e:
            raise ProcessingError(f"Build pipeline failed: {str(e)}") from e

    def clean_existing_documents(self, paper_ids: list[str]) -> None:
        """Delete any prior version of these papers from the graph + vectors.

        MUST run BEFORE extraction. The graphrag extraction pipeline already
        writes chunks/topics/entities to the graph (the topic extractor and
        entity provider are graph-store-backed), so if cleanup runs after
        extraction it sees this run's half-written new chunks (no statements
        linked yet) and deletes those instead of the prior complete version —
        leaving the old statements/facts/entities as unreachable orphans.
        """
        if not paper_ids:
            logger.warning("No 'paper_id's found for deletion")
            return
        try:
            self._perform_deletion(paper_ids)
        except Exception as e:
            logger.error("Failed to delete documents: %s", str(e))
            raise

    def _perform_deletion(self, paper_id_list: list[str]) -> None:
        results = self._neptune_client.batch_delete_documents(paper_id_list)
        logger.info(
            "Neptune deletion result: %s",
            pformat(summarize_deletion_results(results)),
        )

        for client in self._open_search_clients:
            results = client.batch_delete_documents(paper_id_list)
            logger.info(
                "OpenSearch deletion result for index '%s': %s",
                client.index,
                pformat(summarize_deletion_results(results)),
            )


def run_extract_and_build(
    papers: list[Paper],
    config: Config,
    boto3_session: boto3.Session,
    output_dir: str | None = None,
    enable_batch_inference: bool = False,
) -> None:
    try:
        _configure_graph_rag(config, boto3_session)
        _configure_logging()

        checkpoint = _create_checkpoint(config, output_dir or "output")
        stores = _setup_stores(config, boto3_session)

        extractor = Extractor(
            config=config,
            graph_store=stores[0],
            boto3_session=boto3_session,
            checkpoint=checkpoint,
            enable_batch_inference=enable_batch_inference,
        )

        builder = Builder(*stores, checkpoint=checkpoint)

        # Clean any prior version of these papers BEFORE extraction: the
        # extraction pipeline itself writes to the graph, so cleaning afterwards
        # would target this run's half-written nodes and orphan the old ones.
        paper_ids = [p.arxiv_id for p in papers if p.arxiv_id]
        builder.clean_existing_documents(paper_ids)

        extracted_docs = extractor.extract(papers)
        builder.build(extracted_docs)
        logger.info("Indexing completed successfully")

    except Exception as e:
        logger.error("Failed to complete indexing: %s", str(e))
        raise


def _configure_graph_rag(config: Config, boto3_session: boto3.Session) -> None:
    # graphrag invokes these LLMs directly via BedrockConverse with the bare model
    # id. Claude 4.x models are not available on-demand and must be addressed by a
    # cross-region inference profile (e.g. "us.anthropic.claude-haiku-4-5-..."),
    # so resolve the profile id here exactly as the fetcher does for its own LLMs.
    region_name = config.resources.bedrock_region_name
    extraction_llm = get_cross_inference_model_id(
        boto3_session, config.indexing.extraction_model_id.value, region_name
    )
    response_llm = get_cross_inference_model_id(
        boto3_session, config.indexing.response_model_id.value, region_name
    )
    graph_rag_config = {
        "extraction_llm": extraction_llm,
        "response_llm": response_llm,
        "embed_model": config.indexing.embeddings_model_id.value,
        "embed_dimensions": ModelHandler.get_dimensions(
            config.indexing.embeddings_model_id
        ),
        "extraction_num_workers": config.indexing.extraction_num_workers,
        "extraction_num_threads_per_worker": config.indexing.extraction_num_threads_per_worker,
        "extraction_batch_size": config.indexing.extraction_batch_size,
        "build_num_workers": config.indexing.build_num_workers,
        "build_batch_size": config.indexing.build_batch_size,
        "batch_writes_enabled": config.indexing.batch_writes_enabled,
        "enable_cache": config.indexing.enable_cache,
    }
    for key, value in graph_rag_config.items():
        setattr(GraphRAGConfig, key, value)


def _configure_logging() -> None:
    set_logging_config(
        "DEBUG" if os.getenv("LOG_LEVEL", "INFO").upper() == "DEBUG" else "INFO"
    )


def _create_checkpoint(config: Config, output_dir: str) -> Checkpoint:
    checkpoint_name = f"{config.resources.project_name}-{datetime.now().strftime(DEFAULT_CHECKPOINT_DATE_FORMAT)}"
    return Checkpoint(checkpoint_name, output_dir=output_dir, enabled=True)


def _setup_stores(
    config: Config,
    boto3_session: boto3.Session,
) -> tuple[GraphStore, VectorStore, NeptuneClient, list[OpenSearchClient]]:
    base_path = f"/{config.resources.project_name}-{config.resources.stage}"
    graph_endpoint = get_ssm_param_value(
        boto3_session, f"{base_path}/{SSMParams.NEPTUNE_ENDPOINT.value}"
    )
    vector_endpoint = get_ssm_param_value(
        boto3_session, f"{base_path}/{SSMParams.OPENSEARCH_ENDPOINT.value}"
    )
    if graph_endpoint is None or vector_endpoint is None:
        raise ValueError("Graph or vector endpoint not found")

    graph_store = GraphStoreFactory.for_graph_store(f"neptune-db://{graph_endpoint}")
    vector_store = VectorStoreFactory.for_vector_store(f"aoss://{vector_endpoint}")

    neptune_client = NeptuneClient(graph_endpoint)
    open_search_clients = [
        OpenSearchClient(
            vector_endpoint.replace("http://", "").replace("https://", ""),
            443,
            index,
            boto3_session,
            config.resources.default_region_name,
        )
        for index in ["chunk", "statement"]
    ]

    return graph_store, vector_store, neptune_client, open_search_clients
