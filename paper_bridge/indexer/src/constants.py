import os
from enum import Enum
from typing import List, Optional


ENTITY_CLASSIFICATIONS: List[str] = [
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


class EnvVars(str, Enum):
    AWS_PROFILE_NAME = "AWS_PROFILE_NAME"

    @property
    def value(self) -> Optional[str]:
        return os.getenv(self.name)


class LocalPaths(str, Enum):
    LOGS_DIR = "logs"

    CONFIG_FILE = "config.yaml"
    LOGS_FILE = "logs.txt"


class URLs(str, Enum):
    HF_DAILY_PAPERS = "https://huggingface.co/api/daily_papers"

    @property
    def url(self) -> str:
        return self.value
