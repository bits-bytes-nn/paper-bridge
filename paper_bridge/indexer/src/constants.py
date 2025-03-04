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
    LLAMA_CLOUD_API_KEY = "LLAMA_CLOUD_API_KEY"
    TOPIC_ARN = "TOPIC_ARN"

    @property
    def value(self) -> Optional[str]:
        return os.getenv(self.name)


class LocalPaths(str, Enum):
    LOGS_DIR = "logs"

    CONFIG_FILE = "config.yaml"
    LOGS_FILE = "logs.txt"


class SSMParams(str, Enum):
    BATCH_JOB_DEFINITION = "batch/job-definition"
    BATCH_JOB_QUEUE = "batch/job-queue"
    LLAMA_CLOUD_API_KEY = "batch/llama-cloud-api-key"


class URLs(str, Enum):
    HF_DAILY_PAPERS = "https://huggingface.co/api/daily_papers"

    @property
    def url(self) -> str:
        return self.value
