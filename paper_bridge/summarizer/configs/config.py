from enum import Enum
from pathlib import Path
from typing import Any, Dict, Optional, Union
import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field, FilePath, field_validator, model_validator


class LanguageModelId(str, Enum):
    CLAUDE_V3_HAIKU = "anthropic.claude-3-haiku-20240307-v1:0"
    CLAUDE_V3_5_HAIKU = "anthropic.claude-3-5-haiku-20241022-v1:0"
    CLAUDE_V3_5_SONNET = "anthropic.claude-3-5-sonnet-20240620-v1:0"
    CLAUDE_V3_5_SONNET_V2 = "anthropic.claude-3-5-sonnet-20241022-v2:0"
    CLAUDE_V3_7_SONNET = "anthropic.claude-3-7-sonnet-20250219-v1:0"


class LocalPaths(str, Enum):
    FIGURES_DIR = "figures"
    LOGS_DIR = "logs"

    CONFIG_FILE = "config.yaml"
    LOGS_FILE = "logs.txt"
    PARSED_FILE = "parsed.json"


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

    @field_validator("project_name")
    @classmethod
    def validate_project_name(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Project name cannot be empty or whitespace")
        return v


class Summarization(BaseModelWithDefaults):
    papers_per_day: int = Field(default=5, ge=1)
    days_to_fetch: int = Field(default=7, ge=1)
    min_upvotes: Optional[int] = Field(default=None, ge=0)
    figure_analysis_model_id: Optional[LanguageModelId] = None


class Config(BaseModelWithDefaults):
    resources: Resources = Field(
        default_factory=lambda: Resources(project_name="paper-bridge")
    )
    summarization: Summarization = Field(default_factory=Summarization)

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
