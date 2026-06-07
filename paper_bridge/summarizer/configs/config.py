from enum import Enum
from pathlib import Path
from typing import Literal

import yaml
from dotenv import load_dotenv
from pydantic import Field, FilePath

from paper_bridge.shared import BaseModelWithDefaults, EnvVars, Format, LanguageModelId


class LocalPaths(str, Enum):
    FIGURES_DIR = "figures"
    LOGS_DIR = "logs"
    PAPERS_DIR = "papers"

    CONFIG_FILE = "config.yaml"
    LOGS_FILE = "logs.txt"
    PARSED_FILE = "parsed.json"
    TEMPLATE_FILE = "template.html"


# Trigger configuration
class AutoMode(BaseModelWithDefaults):
    enabled: bool = Field(default=True)
    source: Literal["huggingface_daily_papers"] = Field(
        default="huggingface_daily_papers"
    )


class TriggerConfig(BaseModelWithDefaults):
    auto_mode: AutoMode = Field(default_factory=AutoMode)


# Input configuration
class InputConfig(BaseModelWithDefaults):
    pdf_download_timeout: int = Field(default=120, ge=10)
    temp_dir_base: str = Field(default="/tmp/paper-bridge")
    use_md5_hash_dirs: bool = Field(default=True)
    arxiv_optimizations: bool = Field(default=True)


# Output configuration
class SlackOutput(BaseModelWithDefaults):
    enabled: bool = Field(default=True)
    html_template: str = Field(default="template.html")
    apply_retrieval: bool = Field(default=True)


class GithubOutput(BaseModelWithDefaults):
    enabled: bool = Field(default=False)
    repo_name: str | None = Field(default=None)
    base_branch: str = Field(default="main")
    branch_prefix: str = Field(default="paper-summaries")
    author_name: str = Field(default="Paper Bridge Bot")
    author_email: str | None = Field(default=None)
    posts_dir: str = Field(default="_posts")
    assets_dir: str = Field(default="assets")


class OutputConfig(BaseModelWithDefaults):
    mode: Literal["slack", "github"] = Field(default="slack")
    slack: SlackOutput = Field(default_factory=SlackOutput)
    github: GithubOutput = Field(default_factory=GithubOutput)


class Resources(BaseModelWithDefaults):
    project_name: str = Field(min_length=1)
    stage: Literal["dev", "prod"] = Field(default="dev")
    default_region_name: str = Field(default="us-west-2")
    bedrock_region_name: str = Field(default="us-west-2")
    # Account/region-specific; injected via the S3_BUCKET_NAME env var
    # (Terraform in AWS, .env locally) by Config.load() rather than committed
    # to config.yaml. Defaults to "" so the model validates before load() fills
    # it in.
    s3_bucket_name: str = Field(default="")
    s3_prefix: str | None = Field(default=None)
    s3_outputs_path: str = Field(default="outputs")


class Summarization(BaseModelWithDefaults):
    papers_per_day: int = Field(default=5, ge=1)
    days_to_fetch: int = Field(default=7, ge=1)
    min_upvotes: int | None = Field(default=None, ge=0)
    # Paper-selection scoring weights (see shared.paper_selection.PaperScorer).
    selection_popularity_weight: float = Field(default=0.6, ge=0)
    selection_recency_weight: float = Field(default=0.4, ge=0)
    selection_recency_half_life_days: float = Field(default=7.0, gt=0)
    parse_pdf: bool = Field(default=False)
    figure_analysis_model_id: LanguageModelId | None = Field(default=None)
    figure_analysis_max_tokens: int = Field(default=4096, ge=1)
    paper_summarization_model_id: LanguageModelId | None = Field(default=None)
    summarization_max_tokens: int = Field(default=8192, ge=1)
    enable_prompt_caching: bool = Field(default=True)


class Retrieval(BaseModelWithDefaults):
    output_format: Format | None = Field(default=None)
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
    retrieval_summarization_model_id: LanguageModelId | None = Field(default=None)
    retrieval_max_tokens: int = Field(default=8192, ge=1)
    enable_prompt_caching: bool = Field(default=True)


class Config(BaseModelWithDefaults):
    resources: Resources = Field(
        default_factory=lambda: Resources(project_name="paper-bridge")
    )
    summarization: Summarization = Field(default_factory=lambda: Summarization())
    retrieval: Retrieval = Field(default_factory=lambda: Retrieval())
    trigger: TriggerConfig = Field(default_factory=TriggerConfig)
    input: InputConfig = Field(default_factory=InputConfig)
    output: OutputConfig = Field(default_factory=OutputConfig)

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

        # The GitHub target repo (owner/name) is deployment-specific, so — like the
        # S3 bucket — it is injected via the GITHUB_REPO_NAME env var (Terraform in
        # AWS, .env locally) rather than committed to config.yaml. The env value,
        # when set, wins over any config.yaml value.
        github_repo = EnvVars.GITHUB_REPO_NAME.env_value
        if github_repo:
            config.output.github.repo_name = github_repo
        return config
