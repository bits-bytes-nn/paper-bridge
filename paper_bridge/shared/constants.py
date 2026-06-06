"""Shared constants, enums, and configuration values for Paper Bridge."""

import os
from enum import Enum, auto

NULL_STRING: str = "null"


class AutoNamedEnum(str, Enum):
    """Enum that uses lowercase member names as values."""

    @staticmethod
    def _generate_next_value_(
        name: str, start: int, count: int, last_values: list[str]
    ) -> str:
        return name.lower()


class EnvVars(str, Enum):
    """Environment variable names used across all modules."""

    AWS_PROFILE_NAME = "AWS_PROFILE_NAME"
    BEDROCK_REGION_NAME = "BEDROCK_REGION_NAME"
    BUSINESS_SLACK_BOT_TOKEN = "BUSINESS_SLACK_BOT_TOKEN"
    BUSINESS_SLACK_CHANNEL_ID = "BUSINESS_SLACK_CHANNEL_ID"
    DEFAULT_REGION_NAME = "DEFAULT_REGION_NAME"
    GITHUB_TOKEN = "GITHUB_TOKEN"
    LLAMA_CLOUD_API_KEY = "LLAMA_CLOUD_API_KEY"
    LOG_LEVEL = "LOG_LEVEL"
    PERSONAL_SLACK_BOT_TOKEN = "PERSONAL_SLACK_BOT_TOKEN"
    PERSONAL_SLACK_CHANNEL_ID = "PERSONAL_SLACK_CHANNEL_ID"
    S3_BUCKET_NAME = "S3_BUCKET_NAME"
    SLACK_BOT_TOKEN = "SLACK_BOT_TOKEN"
    SLACK_CHANNEL_ID = "SLACK_CHANNEL_ID"
    TOPIC_ARN = "TOPIC_ARN"
    UPSTAGE_API_KEY = "UPSTAGE_API_KEY"

    @property
    def env_value(self) -> str | None:
        """Get the environment variable value.

        Named env_value to avoid conflict with Enum's built-in value attribute.
        """
        return os.getenv(self.name)


class Format(AutoNamedEnum):
    """Output format types."""

    HTML = auto()
    SLACK = auto()


class Language(AutoNamedEnum):
    """Supported languages for summarization."""

    EN = auto()
    KO = auto()


class LanguageModelId(str, Enum):
    """Bedrock language model identifiers."""

    CLAUDE_V3_HAIKU = "anthropic.claude-3-haiku-20240307-v1:0"
    CLAUDE_V3_SONNET = "anthropic.claude-3-sonnet-20240229-v1:0"
    CLAUDE_V3_OPUS = "anthropic.claude-3-opus-20240229-v1:0"
    CLAUDE_V3_5_HAIKU = "anthropic.claude-3-5-haiku-20241022-v1:0"
    CLAUDE_V4_5_HAIKU = "anthropic.claude-haiku-4-5-20251001-v1:0"
    CLAUDE_V3_5_SONNET = "anthropic.claude-3-5-sonnet-20240620-v1:0"
    CLAUDE_V3_5_SONNET_V2 = "anthropic.claude-3-5-sonnet-20241022-v2:0"
    CLAUDE_V3_7_SONNET = "anthropic.claude-3-7-sonnet-20250219-v1:0"
    CLAUDE_V4_SONNET = "anthropic.claude-sonnet-4-20250514-v1:0"
    CLAUDE_V4_5_SONNET = "anthropic.claude-sonnet-4-5-20250929-v1:0"
    CLAUDE_V4_6_SONNET = "anthropic.claude-sonnet-4-6"
    CLAUDE_V4_OPUS = "anthropic.claude-opus-4-20250514-v1:0"
    CLAUDE_V4_1_OPUS = "anthropic.claude-opus-4-1-20250805-v1:0"
    CLAUDE_V4_5_OPUS = "anthropic.claude-opus-4-5-20251101-v1:0"


class SSMParams(str, Enum):
    """AWS SSM Parameter Store parameter names."""

    BATCH_JOB_DEFINITION_INDEXER = "batch-job-definition-indexer"
    BATCH_JOB_DEFINITION_SUMMARIZER = "batch-job-definition-summarizer"
    BATCH_JOB_QUEUE_INDEXER = "batch-job-queue-indexer"
    BATCH_JOB_QUEUE_SUMMARIZER = "batch-job-queue-summarizer"
    BUSINESS_SLACK_BOT_TOKEN = "business-slack-bot-token"
    BUSINESS_SLACK_CHANNEL_ID = "business-slack-channel-id"
    LLAMA_CLOUD_API_KEY = "llama-cloud-api-key"
    NEPTUNE_ENDPOINT = "neptune-endpoint"
    OPENSEARCH_ENDPOINT = "opensearch-endpoint"
    PERSONAL_SLACK_BOT_TOKEN = "personal-slack-bot-token"
    PERSONAL_SLACK_CHANNEL_ID = "personal-slack-channel-id"
    SLACK_BOT_TOKEN = "slack-bot-token"
    SLACK_CHANNEL_ID = "slack-channel-id"
    UPSTAGE_API_KEY = "upstage-api-key"


class URLs(str, Enum):
    """External service URLs."""

    ARXIV_HTML = "https://arxiv.org/html"
    ARXIV_PDF = "https://arxiv.org/pdf"
    HF_DAILY_PAPERS = "https://huggingface.co/api/daily_papers"
    UPSTAGE_DOCUMENT_PARSE = "https://api.upstage.ai/v1/document-ai/document-parse"

    @property
    def url(self) -> str:
        """Get the URL value."""
        return self.value
