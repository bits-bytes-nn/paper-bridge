"""Constants for the summarizer module.

Re-exports common constants from shared module and defines module-specific ones.
"""

from enum import Enum, auto

from paper_bridge.shared import (
    NULL_STRING,
    AutoNamedEnum,
    EnvVars,
    Format,
    Language,
    LanguageModelId,
    SSMParams,
    URLs,
)

__all__ = [
    "NULL_STRING",
    "AutoNamedEnum",
    "EnvVars",
    "Format",
    "Language",
    "LanguageModelId",
    "LocalPaths",
    "S3Paths",
    "SSMParams",
    "URLs",
]


class LocalPaths(str, Enum):
    """Module-specific local paths for summarizer."""

    FIGURES_DIR = "figures"
    LOGS_DIR = "logs"
    PAPERS_DIR = "summarizer/papers"
    OUTPUTS_DIR = "outputs"
    TEMPLATES_DIR = "summarizer/templates"

    CONFIG_FILE = "config.yaml"
    LOGS_FILE = "logs.txt"
    PARSED_FILE = "parsed.json"
    TEMPLATE_FILE = "template.html"


class S3Paths(AutoNamedEnum):
    """S3 path prefixes."""

    INPUTS = auto()
    OUTPUTS = auto()
