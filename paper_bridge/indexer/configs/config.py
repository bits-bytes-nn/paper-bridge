from enum import Enum
from pathlib import Path
from typing import Literal

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field, FilePath

from paper_bridge.shared import BaseModelWithDefaults, EnvVars, LanguageModelId


class EmbeddingsModelId(str, Enum):
    COHERE_EMBED_TEXT_V3 = "cohere.embed-english-v3"
    TITAN_EMBED_TEXT_V1 = "amazon.titan-embed-text-v1"
    TITAN_EMBED_TEXT_V2 = "amazon.titan-embed-text-v2"


ModelIdType = EmbeddingsModelId | LanguageModelId


class ModelInfo(BaseModel):
    dimensions: int | list[int] | None = Field(default=None)
    max_sequence_length: int = Field(gt=0)


_MODEL_INFO: dict[ModelIdType, ModelInfo] = {
    EmbeddingsModelId.COHERE_EMBED_TEXT_V3: ModelInfo(
        dimensions=1024, max_sequence_length=512
    ),
    EmbeddingsModelId.TITAN_EMBED_TEXT_V1: ModelInfo(
        dimensions=1536, max_sequence_length=8192
    ),
    EmbeddingsModelId.TITAN_EMBED_TEXT_V2: ModelInfo(
        dimensions=[256, 384, 1024], max_sequence_length=8192
    ),
    LanguageModelId.CLAUDE_V3_HAIKU: ModelInfo(max_sequence_length=200000),
    LanguageModelId.CLAUDE_V3_5_HAIKU: ModelInfo(max_sequence_length=200000),
    LanguageModelId.CLAUDE_V3_5_SONNET: ModelInfo(max_sequence_length=200000),
    LanguageModelId.CLAUDE_V3_5_SONNET_V2: ModelInfo(max_sequence_length=200000),
    LanguageModelId.CLAUDE_V3_7_SONNET: ModelInfo(max_sequence_length=200000),
    LanguageModelId.CLAUDE_V4_5_HAIKU: ModelInfo(max_sequence_length=200000),
    LanguageModelId.CLAUDE_V4_SONNET: ModelInfo(max_sequence_length=200000),
    LanguageModelId.CLAUDE_V4_5_SONNET: ModelInfo(max_sequence_length=200000),
    LanguageModelId.CLAUDE_V4_6_SONNET: ModelInfo(max_sequence_length=200000),
    LanguageModelId.CLAUDE_V4_OPUS: ModelInfo(max_sequence_length=200000),
    LanguageModelId.CLAUDE_V4_1_OPUS: ModelInfo(max_sequence_length=200000),
    LanguageModelId.CLAUDE_V4_5_OPUS: ModelInfo(max_sequence_length=200000),
    LanguageModelId.CLAUDE_V4_6_OPUS: ModelInfo(max_sequence_length=200000),
    LanguageModelId.CLAUDE_V4_7_OPUS: ModelInfo(max_sequence_length=200000),
    LanguageModelId.CLAUDE_V4_8_OPUS: ModelInfo(max_sequence_length=200000),
}


class ModelHandler:
    @staticmethod
    def get_model_info(model_id: ModelIdType) -> ModelInfo | None:
        return _MODEL_INFO.get(model_id)

    @classmethod
    def get_dimensions(
        cls,
        model_id: ModelIdType,
        mode: str | None = None,
        index: int | None = None,
    ) -> int | list[int] | None:
        model_info = cls.get_model_info(model_id)
        if not model_info or model_info.dimensions is None:
            return None

        dimensions = model_info.dimensions
        if isinstance(dimensions, list):
            if mode == "max":
                return max(dimensions)
            elif mode == "min":
                return min(dimensions)
            elif index is not None and 0 <= index < len(dimensions):
                return dimensions[index]
            else:
                return dimensions
        return dimensions

    @classmethod
    def get_max_sequence_length(cls, model_id: ModelIdType) -> int | None:
        model_info = cls.get_model_info(model_id)
        return model_info.max_sequence_length if model_info else None


class LocalPaths(str, Enum):
    FIGURES_DIR = "figures"
    LOGS_DIR = "logs"
    PAPERS_DIR = "papers"

    TEMPLATES_FILE = "templates.html"
    CONFIG_FILE = "config.yaml"
    LOGS_FILE = "logs.txt"
    PARSED_FILE = "parsed.json"


class Resources(BaseModelWithDefaults):
    project_name: str = Field(min_length=1)
    stage: Literal["dev", "prod"] = Field(default="dev")
    default_region_name: str = Field(default="us-west-2")
    bedrock_region_name: str = Field(default="us-west-2")
    s3_bucket_name: str | None = Field(default=None)
    s3_key_prefix: str | None = Field(default=None)


class Indexing(BaseModelWithDefaults):
    papers_per_day: int = Field(default=5, ge=1)
    days_to_fetch: int = Field(default=7, ge=1)
    min_upvotes: int | None = Field(default=None, ge=0)
    # Paper-selection scoring weights (see shared.paper_selection.PaperScorer).
    selection_popularity_weight: float = Field(default=0.6, ge=0)
    selection_recency_weight: float = Field(default=0.4, ge=0)
    selection_recency_half_life_days: float = Field(default=7.0, gt=0)
    use_llama_parse: bool = Field(default=False)
    main_content_extraction_model_id: LanguageModelId | None = Field(default=None)
    extraction_model_id: LanguageModelId
    response_model_id: LanguageModelId
    embeddings_model_id: EmbeddingsModelId
    enable_batch_inference: bool = Field(default=False)
    extraction_num_workers: int = Field(default=2)
    extraction_num_threads_per_worker: int = Field(default=4)
    extraction_batch_size: int = Field(default=4)
    build_num_workers: int = Field(default=2)
    build_batch_size: int = Field(default=4)
    build_batch_write_size: int = Field(default=25)
    batch_writes_enabled: bool = Field(default=True)
    enable_cache: bool = Field(default=False)
    chunk_size: int = Field(default=1024)
    chunk_overlap: int = Field(default=128)


class Config(BaseModelWithDefaults):
    resources: Resources = Field(
        default_factory=lambda: Resources(project_name="paper-bridge")
    )
    indexing: Indexing = Field(
        default_factory=lambda: Indexing(
            extraction_model_id=LanguageModelId.CLAUDE_V4_5_HAIKU,
            response_model_id=LanguageModelId.CLAUDE_V4_6_SONNET,
            embeddings_model_id=EmbeddingsModelId.COHERE_EMBED_TEXT_V3,
        )
    )

    @classmethod
    def from_yaml(cls, file_path: str | Path | FilePath) -> "Config":
        try:
            with open(file_path, encoding="utf-8") as file:
                config_data = yaml.safe_load(file) or {}
            return cls(**config_data)
        except (OSError, yaml.YAMLError) as e:
            raise ValueError(f"Failed to load config from {file_path}: {str(e)}") from e

    @classmethod
    def load(cls) -> "Config":
        load_dotenv()
        config_path = Path(__file__).parent / LocalPaths.CONFIG_FILE.value
        config = cls() if not config_path.exists() else cls.from_yaml(config_path)

        # The S3 bucket is account/region-specific (e.g.
        # "sagemaker-us-west-2-<acct>"), so it must NOT be committed in
        # config.yaml. Terraform injects it as S3_BUCKET_NAME into the Batch
        # job; locally it comes from .env. The env value, when set, wins.
        bucket = EnvVars.S3_BUCKET_NAME.env_value
        if bucket:
            config.resources.s3_bucket_name = bucket
        return config
