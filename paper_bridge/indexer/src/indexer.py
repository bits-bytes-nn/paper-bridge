import nest_asyncio
from datetime import datetime, timezone
from pipe import Pipe
from typing import List, Optional, Tuple, Union
import boto3
from graphrag_toolkit import GraphRAGConfig, set_logging_config
from graphrag_toolkit.storage import (
    GraphStore,
    GraphStoreFactory,
    VectorStore,
    VectorStoreFactory,
)
from graphrag_toolkit.indexing import sink
from graphrag_toolkit.indexing.constants import PROPOSITIONS_KEY
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
from graphrag_toolkit.indexing.build import (
    BuildPipeline,
    Checkpoint,
    GraphConstruction,
    VectorIndexing,
)
from graphrag_toolkit.storage.constants import EMBEDDING_INDEXES
from llama_index.core.node_parser import SentenceSplitter
from llama_index.core.schema import BaseNode, Document
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

ENABLE_BATCH_INFERENCE: bool = False
nest_asyncio.apply()


class DocumentProcessor:
    def __init__(self, checkpoint: Optional[Checkpoint] = None):
        self._checkpoint = checkpoint

    def validate_config(self) -> None:
        raise NotImplementedError

    @property
    def checkpoint(self) -> Optional[Checkpoint]:
        return self._checkpoint


class Extractor(DocumentProcessor):
    def __init__(
        self,
        config: Config,
        graph_store: GraphStore,
        boto3_session: boto3.Session,
        checkpoint: Optional[Checkpoint] = None,
    ):
        super().__init__(checkpoint)
        self._validate_input_params(
            config.indexing.chunk_size, config.indexing.chunk_overlap
        )

        self.batch_config = self._setup_batch_config(config, boto3_session)
        self._init_components(config, graph_store)
        self.validate_config()

    @staticmethod
    def _setup_batch_config(
        config: Config, boto3_session: boto3.Session
    ) -> Optional[BatchConfig]:
        iam_role_name = get_ssm_param_value(
            boto3_session,
            f"/{config.resources.project_name}-{config.resources.stage}/iam/batch-inference-role",
        )

        if ENABLE_BATCH_INFERENCE and config.resources.s3_bucket_name and iam_role_name:
            logger.info(f"Using batch configuration with role: {iam_role_name}")
            return BatchConfig(
                region=config.resources.default_region_name,
                bucket_name=config.resources.s3_bucket_name,
                key_prefix=config.resources.s3_key_prefix,
                role_arn=f"arn:aws:iam::{get_account_id(boto3_session)}:role/{iam_role_name}",
            )
        return None

    @staticmethod
    def _validate_input_params(chunk_size: int, chunk_overlap: int) -> None:
        if chunk_size <= 0:
            raise ValueError("Chunk size must be positive")
        if chunk_overlap < 0:
            raise ValueError("Chunk overlap must be non-negative")
        if chunk_overlap >= chunk_size:
            raise ValueError("Chunk overlap must be less than chunk size")

    def _init_components(
        self,
        config: Config,
        graph_store: GraphStore,
    ) -> None:
        self._splitter = SentenceSplitter(
            chunk_size=config.indexing.chunk_size,
            chunk_overlap=config.indexing.chunk_overlap,
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

    @staticmethod
    def _create_entity_provider(graph_store: GraphStore) -> ScopedValueProvider:
        value_store = GraphScopedValueStore(graph_store=graph_store)
        return ScopedValueProvider(
            label="EntityClassification",
            scoped_value_store=value_store,
            initial_scoped_values={DEFAULT_SCOPE: ENTITY_CLASSIFICATIONS},
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
        if not GraphRAGConfig.extraction_num_workers > 0:
            raise ValueError("'extraction_num_workers' must be positive")
        if not GraphRAGConfig.extraction_batch_size > 0:
            raise ValueError("'extraction_batch_size' must be positive")

    def extract(self, papers: List[Paper]) -> List[BaseNode]:
        if not papers:
            logger.warning("No papers provided for extraction")
            return []

        valid_papers = [p for p in papers if p.content]
        self._log_skipped_papers(len(papers) - len(valid_papers))

        docs = [self._create_document(paper) for paper in valid_papers]
        return docs | self._pipeline

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
        }
        return Document(text=paper.content, metadata=metadata)


class Builder(DocumentProcessor):
    def __init__(
        self,
        graph_store: GraphStore,
        vector_store: VectorStore,
        neptune_client: NeptuneClient,
        open_search_clients: List[OpenSearchClient],
        collection: str,
        checkpoint: Optional[Checkpoint] = None,
    ):
        super().__init__(checkpoint)
        self._init_components(graph_store, vector_store, collection)
        self._neptune_client = neptune_client
        self._open_search_clients = open_search_clients
        self.validate_config()

    def _init_components(
        self,
        graph_store: GraphStore,
        vector_store: VectorStore,
        collection: str,
    ) -> None:
        self._graph_store = graph_store
        self._vector_store = vector_store
        self._graph_construction = GraphConstruction.for_graph_store(graph_store)
        self._vector_indexing = VectorIndexing.for_vector_store(vector_store)
        self._pipeline = self._create_pipeline()
        self._collection = collection

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

    def validate_config(self) -> None:
        if not GraphRAGConfig.build_num_workers > 0:
            raise ValueError("'build_num_workers' must be positive")
        if not GraphRAGConfig.build_batch_size > 0:
            raise ValueError("'build_batch_size' must be positive")

    def build(self, extracted_docs: List[BaseNode]) -> None:
        if not extracted_docs:
            logger.warning("No documents provided for building indices")
            return None

        self._clean_existing_documents(extracted_docs)
        return extracted_docs | self._pipeline | sink

    def _clean_existing_documents(self, docs: List[BaseNode]) -> None:
        for doc in docs:
            paper_id = doc.metadata.get("paper_id")
            if not paper_id:
                logger.warning("Document missing paper_id metadata")
                continue

            logger.info(f"Deleting existing document for paper_id: {paper_id}")
            self._delete_document(paper_id)

    def _delete_document(self, paper_id: str) -> None:
        try:
            self._neptune_client.delete_nodes_by_metadata("paper_id", paper_id)
            # for client in self._open_search_clients:
            #     client.delete_nodes_by_metadata(self._collection, "paper_id", paper_id)
        except Exception as e:
            logger.error(f"Failed to delete existing document {paper_id}: {str(e)}")


def run_extract_and_build(
    papers: List[Paper],
    config: Config,
    profile_name: Optional[str] = None,
) -> None:
    try:
        _configure_graph_rag(config)
        _configure_logging(config)

        boto3_session = _create_boto3_session(config, profile_name)
        checkpoint = _create_checkpoint(config)
        stores = _setup_stores(boto3_session, config, profile_name)

        extractor = Extractor(
            config=config,
            graph_store=stores[0],
            boto3_session=boto3_session,
            checkpoint=checkpoint,
        )

        builder = Builder(*stores, checkpoint=checkpoint)

        extracted_docs = extractor.extract(papers)
        builder.build(extracted_docs)
        logger.info("Indexing completed successfully")

    except Exception as e:
        logger.error(f"Failed to complete indexing: {str(e)}")
        raise


def _configure_logging(config: Config) -> None:
    set_logging_config(
        config.logging.level,
        config.logging.include_prefixes,
        config.logging.exclude_prefixes,
    )


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


def _create_boto3_session(
    config: Config, profile_name: Optional[str] = None
) -> boto3.Session:
    return boto3.Session(
        region_name=config.resources.default_region_name, profile_name=profile_name
    )


def _create_checkpoint(config: Config) -> Checkpoint:
    checkpoint_name = (
        f"{config.resources.project_name}-{datetime.now().strftime('%Y-%m-%d')}"
    )
    return Checkpoint(checkpoint_name, enabled=True)


def _setup_stores(
    boto3_session: boto3.Session,
    config: Config,
    profile_name: Optional[str] = None,
) -> Tuple[GraphStore, VectorStore, NeptuneClient, List[OpenSearchClient], str]:
    project_name = config.resources.project_name
    stage = config.resources.stage

    graph_endpoint = get_ssm_param_value(
        boto3_session, f"/{project_name}-{stage}/neptune/endpoint"
    )
    vector_endpoint = get_ssm_param_value(
        boto3_session, f"/{project_name}-{stage}/opensearch/endpoint"
    )

    graph_store = GraphStoreFactory.for_graph_store(f"neptune-db://{graph_endpoint}")
    vector_store = VectorStoreFactory.for_vector_store(f"aoss://{vector_endpoint}")

    neptune_client = NeptuneClient(graph_endpoint)
    open_search_clients = [
        OpenSearchClient(
            host=vector_endpoint.replace("http://", "").replace("https://", ""),
            port=443,
            index=index,
            region_name=config.resources.default_region_name,
            profile_name=profile_name,
        )
        for index in EMBEDDING_INDEXES
    ]
    collection = f"{project_name}-{stage}-collection"

    return graph_store, vector_store, neptune_client, open_search_clients, collection
