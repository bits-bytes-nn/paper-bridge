import os
from enum import Enum
from typing import List, Optional

NULL_STRING: str = "null"
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
    BEDROCK_REGION_NAME = "BEDROCK_REGION_NAME"
    DEFAULT_REGION_NAME = "DEFAULT_REGION_NAME"
    LLAMA_CLOUD_API_KEY = "LLAMA_CLOUD_API_KEY"
    TOPIC_ARN = "TOPIC_ARN"
    UPSTAGE_API_KEY = "UPSTAGE_API_KEY"

    @property
    def value(self) -> Optional[str]:
        return os.getenv(self.name)


class LocalPaths(str, Enum):
    FIGURES_DIR = "figures"
    LOGS_DIR = "logs"
    PAPERS_DIR = "papers"
    OUTPUTS_DIR = "outputs"
    TEMPLATES_DIR = "summarizer/templates"

    CONFIG_FILE = "config.yaml"
    LOGS_FILE = "logs.txt"
    PARSED_FILE = "parsed.json"
    TEMPLATE_FILE = "template.html"


class SSMParams(str, Enum):
    BATCH_JOB_DEFINITION_INDEXER = "batch-job-definition-indexer"
    BATCH_JOB_DEFINITION_SUMMARIZER = "batch-job-definition-summarizer"
    BATCH_JOB_QUEUE_INDEXER = "batch-job-queue-indexer"
    BATCH_JOB_QUEUE_SUMMARIZER = "batch-job-queue-summarizer"
    LLAMA_CLOUD_API_KEY = "llama-cloud-api-key"
    OPENSEARCH_ENDPOINT = "opensearch-endpoint"
    NEPTUNE_ENDPOINT = "neptune-endpoint"
    SLACK_BOT_TOKEN = "slack-bot-token"
    SLACK_CHANNEL_ID = "slack-channel-id"
    UPSTAGE_API_KEY = "upstage-api-key"


class URLs(str, Enum):
    ARXIV_HTML = "https://arxiv.org/html"
    ARXIV_PDF = "https://arxiv.org/pdf"
    HF_DAILY_PAPERS = "https://huggingface.co/api/daily_papers"
    UPSTAGE_DOCUMENT_PARSE = "https://api.upstage.ai/v1/document-ai/document-parse"

    @property
    def url(self) -> str:
        return self.value
