import os
from enum import Enum, auto
from typing import List, Optional

NULL_STRING: str = "null"


class AutoNamedEnum(str, Enum):
    @staticmethod
    def _generate_next_value_(
        name: str, start: int, count: int, last_values: List[str]
    ) -> str:
        return name.lower()


class EnvVars(str, Enum):
    AWS_PROFILE_NAME = "AWS_PROFILE_NAME"
    BEDROCK_REGION_NAME = "BEDROCK_REGION_NAME"
    DEFAULT_REGION_NAME = "DEFAULT_REGION_NAME"
    LLAMA_CLOUD_API_KEY = "LLAMA_CLOUD_API_KEY"
    SLACK_BOT_TOKEN = "SLACK_BOT_TOKEN"
    SLACK_CHANNEL_ID = "SLACK_CHANNEL_ID"
    TOPIC_ARN = "TOPIC_ARN"
    UPSTAGE_API_KEY = "UPSTAGE_API_KEY"

    @property
    def value(self) -> Optional[str]:
        return os.getenv(self.name)


class Language(AutoNamedEnum):
    EN = auto()
    KO = auto()


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
    OUTPUTS_DIR = "outputs"
    TEMPLATES_DIR = "summarizer/templates"

    CONFIG_FILE = "config.yaml"
    LOGS_FILE = "logs.txt"
    PARSED_FILE = "parsed.json"
    TEMPLATE_FILE = "template.html"


class S3Paths(AutoNamedEnum):
    INPUTS = auto()
    OUTPUTS = auto()


class SSMParams(str, Enum):
    BATCH_JOB_DEFINITION = "batch-job-definition"
    BATCH_JOB_QUEUE = "batch-job-queue"
    LLAMA_CLOUD_API_KEY = "llama-cloud-api-key"
    OPENSEARCH_ENDPOINT = "opensearch-endpoint"
    NEPTUNE_ENDPOINT = "neptune-endpoint"
    SLACK_BOT_TOKEN = "slack-bot-token"
    SLACK_CHANNEL_ID = "slack-channel-id"
    UPSTAGE_API_KEY = "upstage_api_key"


class URLs(str, Enum):
    ARXIV_HTML = "https://ar5iv.labs.arxiv.org/html"
    ARXIV_PDF = "https://arxiv.org/pdf"
    HF_DAILY_PAPERS = "https://huggingface.co/api/daily_papers"
    UPSTAGE_DOCUMENT_PARSE = "https://api.upstage.ai/v1/document-ai/document-parse"

    @property
    def url(self) -> str:
        return self.value
