import os
from enum import Enum
from typing import Optional

NULL_STRING: str = "null"


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
    BATCH_JOB_DEFINITION = "batch-job-definition"
    BATCH_JOB_QUEUE = "batch-job-queue"
    LLAMA_CLOUD_API_KEY = "llama-cloud-api-key"
    OPENSEARCH_ENDPOINT = "opensearch-endpoint"
    NEPTUNE_ENDPOINT = "neptune-endpoint"
    SLACK_BOT_TOKEN = "slack-bot-token"
    SLACK_CHANNEL_ID = "slack-channel-id"
    UPSTAGE_API_KEY = "upstage_api_key"
