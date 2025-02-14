from enum import Enum
from pathlib import Path
from typing import Any, Dict, Optional, Union
import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field, FilePath, field_validator, model_validator


class LanguageModelId(str, Enum):
    CLAUDE_V3_HAIKU = "anthropic.claude-3-haiku-20240307-v1:0"
    CLAUDE_V3_SONNET = "anthropic.claude-3-sonnet-20240229-v1:0"
    CLAUDE_V3_OPUS = "anthropic.claude-3-opus-20240229-v1:0"
    CLAUDE_V3_5_HAIKU = "anthropic.claude-3-5-haiku-20241022-v1:0"
    CLAUDE_V3_5_SONNET = "anthropic.claude-3-5-sonnet-20240620-v1:0"
    CLAUDE_V3_5_SONNET_V2 = "anthropic.claude-3-5-sonnet-20241022-v2:0"

    COMMAND_R = "cohere.command-r-v1:0"
    COMMAND_R_PLUS = "cohere.command-r-plus-v1:0"

    LLAMA_V3_1_8B = "meta.llama3-1-8b-instruct-v1:0"
    LLAMA_V3_1_70B = "meta.llama3-1-70b-instruct-v1:0"
    LLAMA_V3_1_405B = "meta.llama3-1-405b-instruct-v1:0"

    MISTRAL_7B = "mistral.mistral-7b-instruct-v0:2"
    MISTRAL_8X7B = "mistral.mixtral-8x7b-instruct-v0:1"
    MISTRAL_SMALL = "mistral.mistral-small-2402-v1:0"
    MISTRAL_LARGE = "mistral.mistral-large-2407-v1:0"

    NOVA_MICRO = "amazon.nova-micro-v1:0"
    NOVA_PRO = "amazon.nova-pro-v1:0"


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
    bedrock_region_name: str = Field(default="us-west-2")

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
    main_content_extraction_model_id: Optional[LanguageModelId] = None


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
