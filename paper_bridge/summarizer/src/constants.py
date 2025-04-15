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
    BUSINESS_SLACK_BOT_TOKEN = "BUSINESS_SLACK_BOT_TOKEN"
    BUSINESS_SLACK_CHANNEL_ID = "BUSINESS_SLACK_CHANNEL_ID"
    DEFAULT_REGION_NAME = "DEFAULT_REGION_NAME"
    LLAMA_CLOUD_API_KEY = "LLAMA_CLOUD_API_KEY"
    PERSONAL_SLACK_BOT_TOKEN = "PERSONAL_SLACK_BOT_TOKEN"
    PERSONAL_SLACK_CHANNEL_ID = "PERSONAL_SLACK_CHANNEL_ID"
    TOPIC_ARN = "TOPIC_ARN"
    UPSTAGE_API_KEY = "UPSTAGE_API_KEY"

    @property
    def value(self) -> Optional[str]:
        return os.getenv(self.name)


class Format(AutoNamedEnum):
    HTML = auto()
    SLACK = auto()


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
    PAPERS_DIR = "summarizer/papers"
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
    BATCH_JOB_DEFINITION_INDEXER = "batch-job-definition-indexer"
    BATCH_JOB_DEFINITION_SUMMARIZER = "batch-job-definition-summarizer"
    BATCH_JOB_QUEUE_INDEXER = "batch-job-queue-indexer"
    BATCH_JOB_QUEUE_SUMMARIZER = "batch-job-queue-summarizer"
    BUSINESS_SLACK_BOT_TOKEN = "business-slack-bot-token"
    BUSINESS_SLACK_CHANNEL_ID = "business-slack-channel-id"
    LLAMA_CLOUD_API_KEY = "llama-cloud-api-key"
    OPENSEARCH_ENDPOINT = "opensearch-endpoint"
    PERSONAL_SLACK_BOT_TOKEN = "personal-slack-bot-token"
    PERSONAL_SLACK_CHANNEL_ID = "personal-slack-channel-id"
    NEPTUNE_ENDPOINT = "neptune-endpoint"
    UPSTAGE_API_KEY = "upstage-api-key"


class URLs(str, Enum):
    ARXIV_HTML = "https://arxiv.org/html"
    ARXIV_PDF = "https://arxiv.org/pdf"
    HF_DAILY_PAPERS = "https://huggingface.co/api/daily_papers"
    UPSTAGE_DOCUMENT_PARSE = "https://api.upstage.ai/v1/document-ai/document-parse"

    @property
    def url(self) -> str:
        return self.value
