from enum import Enum
from pathlib import Path
from typing import Any, Dict, Literal, Optional, Union
import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field, FilePath, model_validator


class LanguageModelId(str, Enum):
    CLAUDE_V3_HAIKU = "anthropic.claude-3-haiku-20240307-v1:0"
    CLAUDE_V3_5_HAIKU = "anthropic.claude-3-5-haiku-20241022-v1:0"
    CLAUDE_V3_5_SONNET = "anthropic.claude-3-5-sonnet-20240620-v1:0"
    CLAUDE_V3_5_SONNET_V2 = "anthropic.claude-3-5-sonnet-20241022-v2:0"
    CLAUDE_V3_7_SONNET = "anthropic.claude-3-7-sonnet-20250219-v1:0"


class LocalPaths(str, Enum):
    FIGURES_DIR = "figures"
    LOGS_DIR = "logs"
    PAPERS_DIR = "papers"

    CONFIG_FILE = "config.yaml"
    LOGS_FILE = "logs.txt"
    PARSED_FILE = "parsed.json"
    TEMPLATE_FILE = "template.html"


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
    stage: Literal["dev", "prod"] = Field(default="dev")
    default_region_name: str = Field(default="us-west-2")
    bedrock_region_name: str = Field(default="us-west-2")
    s3_bucket_name: str = Field(min_length=1)
    s3_prefix: Optional[str] = Field(default=None)


class Summarization(BaseModelWithDefaults):
    papers_per_day: int = Field(default=5, ge=1)
    days_to_fetch: int = Field(default=7, ge=1)
    min_upvotes: Optional[int] = Field(default=None, ge=0)
    parse_pdf: bool = Field(default=False)
    figure_analysis_model_id: Optional[LanguageModelId] = Field(default=None)
    paper_summarization_model_id: Optional[LanguageModelId] = Field(default=None)


class Retrieval(BaseModelWithDefaults):
    traversal_based_or_semantic_guided: Literal[
        "traversal_based", "semantic_guided"
    ] = Field(default="traversal_based")
    set_subretriever: bool = Field(default=False)
    use_reranking_beam_search: bool = Field(default=False)
    use_post_processors: bool = Field(default=False)
    use_gpu_reranker: bool = Field(default=False)
    gpu_id: int = Field(default=0, ge=0)
    use_diversity: bool = Field(default=False)
    use_enhancement: bool = Field(default=False)
    retrieval_summarization_model_id: Optional[LanguageModelId] = Field(default=None)


class Config(BaseModelWithDefaults):
    resources: Resources = Field(
        default_factory=lambda: Resources(project_name="paper-bridge")
    )
    summarization: Summarization = Field(default_factory=lambda: Summarization())
    retrieval: Retrieval = Field(default_factory=lambda: Retrieval())

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
