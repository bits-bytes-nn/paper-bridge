import nest_asyncio
import os
from datetime import datetime, timezone
from pipe import Pipe
from pprint import pformat
from typing import List, Optional, Tuple, Union
import boto3
from graphrag_toolkit import (
    ExtractionConfig,
    IndexingConfig,
    GraphRAGConfig,
    set_logging_config,
)
from graphrag_toolkit.storage import (
    GraphStore,
    GraphStoreFactory,
    VectorStore,
    VectorStoreFactory,
)
from graphrag_toolkit.indexing import sink
from graphrag_toolkit.indexing.constants import PROPOSITIONS_KEY
from graphrag_toolkit.indexing.build import (
    BuildPipeline,
    Checkpoint,
    GraphConstruction,
    VectorIndexing,
)
from graphrag_toolkit.indexing.extract import (
    DEFAULT_SCOPE,
    BatchConfig,
    BatchLLMPropositionExtractor,
    BatchTopicExtractor,
    ExtractionPipeline,
    GraphScopedValueStore,
    LLMPropositionExtractor,
    ScopedValueProvider,
    TopicExtractor,
)
from graphrag_toolkit.indexing.model import SourceDocument
from llama_index.core.node_parser import SentenceSplitter
from llama_index.core.schema import Document
from .aws_helpers import (
    NeptuneClient,
    OpenSearchClient,
    get_account_id,
    get_ssm_param_value,
)
from .constants import ENTITY_CLASSIFICATIONS
from .fetcher import Paper
from .logger import logger
from paper_bridge.indexer.configs.config import Config, ModelHandler

DEFAULT_CHECKPOINT_DATE_FORMAT = "%Y-%m-%d"

nest_asyncio.apply()


class ProcessingError(Exception):
    pass


class DocumentProcessor:
    def __init__(self, checkpoint: Optional[Checkpoint] = None):
        self._checkpoint = checkpoint

    @property
    def checkpoint(self) -> Optional[Checkpoint]:
        return self._checkpoint

    def validate_config(self) -> None:
        raise NotImplementedError("Subclasses must implement validate_config method")


class Extractor(DocumentProcessor):
    def __init__(
        self,
        config: Config,
        graph_store: GraphStore,
        boto3_session: boto3.Session,
        checkpoint: Optional[Checkpoint] = None,
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
    ) -> Optional[BatchConfig]:
        if not enable_batch_inference:
            return None

        iam_role_name = get_ssm_param_value(
            boto3_session,
            f"/{config.resources.project_name}-{config.resources.stage}/iam/bedrock-inference",
        )

        if not config.resources.s3_bucket_name or not iam_role_name:
            logger.warning("Batch inference enabled but missing S3 bucket or IAM role")
            return None

        logger.info(f"Using batch configuration with role: '{iam_role_name}'")
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
    ) -> Union[BatchLLMPropositionExtractor, LLMPropositionExtractor]:
        return (
            BatchLLMPropositionExtractor(batch_config=self.batch_config)
            if self.batch_config
            else LLMPropositionExtractor()
        )

    def _create_topic_extractor(
        self, graph_store: GraphStore
    ) -> Union[BatchTopicExtractor, TopicExtractor]:
        entity_provider = self._create_entity_provider(graph_store)
        common_params = {
            "source_metadata_field": PROPOSITIONS_KEY,
            "entity_classification_provider": entity_provider,
        }

        return (
            BatchTopicExtractor(batch_config=self.batch_config, **common_params)
            if self.batch_config
            else TopicExtractor(**common_params)
        )

    @staticmethod
    def _create_entity_provider(graph_store: GraphStore) -> ScopedValueProvider:
        value_store = GraphScopedValueStore(graph_store=graph_store)
        return ScopedValueProvider(
            label="EntityClassification",
            scoped_value_store=value_store,
            initial_scoped_values={DEFAULT_SCOPE: ENTITY_CLASSIFICATIONS},
        )

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

    def extract(self, papers: List[Paper]) -> List[SourceDocument]:
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
            raise ProcessingError(f"Document extraction failed: {str(e)}")

    @staticmethod
    def _log_skipped_papers(skipped_count: int) -> None:
        if skipped_count:
            logger.warning(f"Skipped {skipped_count} papers with no content")

    @staticmethod
    def _create_document(paper: Paper) -> Document:
        metadata = {
            "paper_id": paper.arxiv_id,
            "title": paper.title,
            "authors": paper.authors,
            "published_at": paper.published_at.isoformat(),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "summary": paper.summary,
            "upvotes": paper.upvotes,
            "thumbnail": paper.thumbnail,
            "status": paper.status.name,
            "base_date": paper.published_at.date().isoformat(),
        }
        return Document(text=paper.content, metadata=metadata)


class Builder(DocumentProcessor):
    def __init__(
        self,
        graph_store: GraphStore,
        vector_store: VectorStore,
        neptune_client: NeptuneClient,
        open_search_clients: List[OpenSearchClient],
        checkpoint: Optional[Checkpoint] = None,
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
            show_progress=True,
        )

    def build(self, extracted_docs: List[SourceDocument]) -> None:
        if not extracted_docs:
            logger.warning("No documents provided for building indices")
            return

        self._clean_existing_documents(extracted_docs)

        try:
            extracted_docs | self._pipeline | sink
            logger.info("Build pipeline completed successfully")
        except Exception as e:
            raise ProcessingError(f"Build pipeline failed: {str(e)}")

    def _clean_existing_documents(self, docs: List[SourceDocument]) -> None:
        paper_ids = self._extract_paper_ids(docs)

        if not paper_ids:
            logger.warning("No 'paper_id's found for deletion")
            return

        paper_id_list = list(paper_ids)

        try:
            self._perform_deletion(paper_id_list)
        except Exception as e:
            logger.error(f"Failed to delete documents: {str(e)}")
            raise

    @staticmethod
    def _extract_paper_ids(docs: List[SourceDocument]) -> set:
        paper_ids = set()
        for doc in docs:
            for node in doc.nodes:
                if paper_id := node.metadata.get("paper_id"):
                    paper_ids.add(paper_id)
        return paper_ids

    def _perform_deletion(self, paper_id_list: List[str]) -> None:
        results = self._neptune_client.batch_delete_documents(paper_id_list)
        logger.info(
            f"Neptune deletion result: {pformat(self._neptune_client.summarize_deletion_results(results))}"
        )

        for client in self._open_search_clients:
            results = client.batch_delete_documents(paper_id_list)
            logger.info(
                f"OpenSearch deletion result for index '{client.index}': {pformat(client.summarize_deletion_results(results))}"
            )


def run_extract_and_build(
    papers: List[Paper],
    config: Config,
    profile_name: Optional[str] = None,
    output_dir: Optional[str] = None,
    enable_batch_inference: bool = False,
) -> None:
    try:
        _configure_graph_rag(config)
        _configure_logging()

        boto3_session = _create_boto3_session(config, profile_name)
        checkpoint = _create_checkpoint(config, output_dir or "output")
        stores = _setup_stores(boto3_session, config)

        extractor = Extractor(
            config=config,
            graph_store=stores[0],
            boto3_session=boto3_session,
            checkpoint=checkpoint,
            enable_batch_inference=enable_batch_inference,
        )

        builder = Builder(*stores, checkpoint=checkpoint)

        extracted_docs = extractor.extract(papers)
        builder.build(extracted_docs)
        logger.info("Indexing completed successfully")

    except Exception as e:
        logger.error(f"Failed to complete indexing: {str(e)}")
        raise


def _configure_graph_rag(config: Config) -> None:
    graph_rag_config = {
        "extraction_llm": config.indexing.extraction_model_id.value,
        "response_llm": config.indexing.response_model_id.value,
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


def _create_boto3_session(
    config: Config, profile_name: Optional[str] = None
) -> boto3.Session:
    return boto3.Session(
        region_name=config.resources.default_region_name, profile_name=profile_name
    )


def _create_checkpoint(config: Config, output_dir: str) -> Checkpoint:
    checkpoint_name = f"{config.resources.project_name}-{datetime.now().strftime(DEFAULT_CHECKPOINT_DATE_FORMAT)}"
    return Checkpoint(checkpoint_name, output_dir=output_dir, enabled=True)


def _setup_stores(
    boto3_session: boto3.Session,
    config: Config,
) -> Tuple[GraphStore, VectorStore, NeptuneClient, List[OpenSearchClient]]:
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

    graph_store = GraphStoreFactory.for_graph_store(f"neptune-db://{graph_endpoint}")
    vector_store = VectorStoreFactory.for_vector_store(f"aoss://{vector_endpoint}")

    neptune_client = NeptuneClient(graph_endpoint)
    open_search_clients = [
        OpenSearchClient(
            host=vector_endpoint.replace("http://", "").replace("https://", ""),
            port=443,
            index=index,
            region_name=config.resources.default_region_name,
            boto3_session=boto3_session,
        )
        for index in ["chunk", "statement"]
    ]

    return graph_store, vector_store, neptune_client, open_search_clients
