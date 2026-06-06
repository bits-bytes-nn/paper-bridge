"""Shared utilities and constants for Paper Bridge modules."""

from .base_models import BaseModelWithDefaults
from .constants import (
    NULL_STRING,
    AutoNamedEnum,
    EnvVars,
    Format,
    Language,
    LanguageModelId,
    SSMParams,
    URLs,
)
from .logger import LoggerConfig, create_logger, is_aws_env
from .paper_selection import (
    PaperLike,
    PaperScorer,
    ScoredPaper,
    SelectionConfig,
)
from .prompt_caching import apply_cache_point, prompt_caching_supported
from .text_utils import convert_markdown_to_slack_links, extract_unique_urls

__all__ = [
    # Base models
    "BaseModelWithDefaults",
    # Constants
    "NULL_STRING",
    "AutoNamedEnum",
    "EnvVars",
    "Format",
    "Language",
    "LanguageModelId",
    "SSMParams",
    "URLs",
    # Logger
    "LoggerConfig",
    "create_logger",
    "is_aws_env",
    # Text utils
    "convert_markdown_to_slack_links",
    "extract_unique_urls",
    # Paper selection
    "PaperLike",
    "PaperScorer",
    "ScoredPaper",
    "SelectionConfig",
    # Prompt caching
    "apply_cache_point",
    "prompt_caching_supported",
]
