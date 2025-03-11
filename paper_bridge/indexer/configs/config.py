from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Union
import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field, FilePath, field_validator, model_validator


class EmbeddingsModelId(str, Enum):
    COHERE_EMBED_TEXT_V3 = "cohere.embed-english-v3"
    TITAN_EMBED_TEXT_V1 = "amazon.titan-embed-text-v1"
    TITAN_EMBED_TEXT_V2 = "amazon.titan-embed-text-v2"


class LanguageModelId(str, Enum):
    CLAUDE_V3_HAIKU = "anthropic.claude-3-haiku-20240307-v1:0"
    CLAUDE_V3_5_HAIKU = "anthropic.claude-3-5-haiku-20241022-v1:0"
    CLAUDE_V3_5_SONNET = "anthropic.claude-3-5-sonnet-20240620-v1:0"
    CLAUDE_V3_5_SONNET_V2 = "anthropic.claude-3-5-sonnet-20241022-v2:0"
    CLAUDE_V3_7_SONNET = "anthropic.claude-3-7-sonnet-20250219-v1:0"


ModelIdType = Union[EmbeddingsModelId, LanguageModelId]


class ModelInfo(BaseModel):
    dimensions: Optional[Union[int, List[int]]] = Field(default=None)
    max_sequence_length: int = Field(gt=0)


_MODEL_INFO: Dict[ModelIdType, ModelInfo] = {
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
}


class ModelHandler:
    @staticmethod
    def get_model_info(model_id: ModelIdType) -> Optional[ModelInfo]:
        return _MODEL_INFO.get(model_id)

    @classmethod
    def get_dimensions(
        cls,
        model_id: ModelIdType,
        mode: Optional[str] = None,
        index: Optional[int] = None,
    ) -> Optional[Union[int, List[int]]]:
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
    def get_max_sequence_length(
        cls,
        model_id: ModelIdType,
        as_chars: bool = False,
        chars_per_token: Optional[float] = None,
    ) -> Optional[int]:
        model_info = cls.get_model_info(model_id)
        if not model_info:
            return None

        max_length = model_info.max_sequence_length
        if as_chars and chars_per_token:
            return int(max_length * chars_per_token)
        return max_length

    @staticmethod
    def get_provider_name(model_id: ModelIdType) -> Optional[str]:
        return model_id.split(".")[0] if "." in model_id else None


class LocalPaths(str, Enum):
    LOGS_DIR = "logs"

    CONFIG_FILE = "config.yaml"
    LOGS_FILE = "logs.txt"


class BaseModelWithDefaults(BaseModel):
    @model_validator(mode="before")
    def set_defaults_for_none_fields(cls, values: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(values, dict):
            return values
        for field_name, field in cls.model_fields.items():
            if values.get(field_name) is None and field.default is not None:
                values[field_name] = field.default
        return values


class Resources(BaseModelWithDefaults):
    project_name: str = Field(min_length=1)
    stage: str = Field(default="dev", pattern=r"^(dev|prod)$")
    default_region_name: str = Field(default="us-west-2")
    bedrock_region_name: str = Field(default="us-west-2")
    s3_bucket_name: Optional[str] = Field(default=None)
    s3_key_prefix: Optional[str] = Field(default=None)

    @field_validator("project_name")
    @classmethod
    def validate_project_name(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Project name cannot be empty or whitespace")
        return v


class Indexing(BaseModelWithDefaults):
    papers_per_day: int = Field(default=5, ge=1)
    days_to_fetch: int = Field(default=7, ge=1)
    min_upvotes: Optional[int] = Field(default=None, ge=0)
    use_llama_parse: bool = Field(default=False)
    main_content_extraction_model_id: Optional[LanguageModelId] = None
    extraction_model_id: LanguageModelId = Field(
        default=LanguageModelId.CLAUDE_V3_5_SONNET_V2
    )
    response_model_id: LanguageModelId = Field(
        default=LanguageModelId.CLAUDE_V3_5_SONNET_V2
    )
    embeddings_model_id: EmbeddingsModelId = Field(
        default=EmbeddingsModelId.COHERE_EMBED_TEXT_V3
    )
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
    indexing: Indexing = Field(default_factory=Indexing)

    @classmethod
    def from_yaml(cls, file_path: Union[str, Path, FilePath]) -> "Config":
        try:
            with open(file_path, encoding="utf-8") as file:
                config_data = yaml.safe_load(file) or {}
            return cls(**config_data)
        except (OSError, yaml.YAMLError) as e:
            raise ValueError(f"Failed to load config from {file_path}: {str(e)}")

    @classmethod
    def load(cls) -> "Config":
        load_dotenv()
        config_path = Path(__file__).parent / LocalPaths.CONFIG_FILE.value
        if not config_path.exists():
            return cls()
        return cls.from_yaml(config_path)
