"""Constants for the indexer module.

Re-exports common constants from shared module and defines module-specific ones.
"""

from enum import Enum

from paper_bridge.shared import (
    NULL_STRING,
    EnvVars,
    SSMParams,
    URLs,
)

__all__ = [
    "NULL_STRING",
    "ENTITY_CLASSIFICATIONS",
    "EnvVars",
    "LocalPaths",
    "SSMParams",
    "URLs",
]

ENTITY_CLASSIFICATIONS: list[str] = [
    "Ablation Study",
    "Algorithm",
    "Baseline",
    "Benchmark",
    "Computing Infrastructure",
    "Conference",
    "Data Augmentation",
    "Data Preprocessing",
    "Dataset",
    "Domain",
    "Framework",
    "Future Work",
    "Hardware",
    "Hyperparameter",
    "Journal",
    "Library",
    "Limitation",
    "Loss Function",
    "Metric",
    "Model Architecture",
    "Optimization Method",
    "Performance Result",
    "Prior Work",
    "Research Field",
    "Research Group",
    "Research Institution",
    "Research Problem",
    "Researcher",
    "Task",
    "Training Data",
    "Use Case",
    "Validation Data",
]


class LocalPaths(str, Enum):
    """Module-specific local paths for indexer."""

    FIGURES_DIR = "figures"
    LOGS_DIR = "logs"
    PAPERS_DIR = "papers"
    OUTPUTS_DIR = "outputs"

    CONFIG_FILE = "config.yaml"
    LOGS_FILE = "logs.txt"
    PARSED_FILE = "parsed.json"
