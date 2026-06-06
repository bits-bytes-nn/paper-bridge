"""Constants for the cleaner module.

Re-exports common constants from shared module and defines module-specific ones.
"""

from enum import Enum

from paper_bridge.shared import (
    NULL_STRING,
    EnvVars,
    SSMParams,
)

__all__ = [
    "NULL_STRING",
    "EnvVars",
    "LocalPaths",
    "SSMParams",
]


class LocalPaths(str, Enum):
    """Module-specific local paths for cleaner."""

    FIGURES_DIR = "figures"
    LOGS_DIR = "logs"
    PAPERS_DIR = "papers"
    OUTPUTS_DIR = "outputs"

    CONFIG_FILE = "config.yaml"
    LOGS_FILE = "logs.txt"
