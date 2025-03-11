import os
from enum import Enum
from typing import Optional


class EnvVars(str, Enum):
    AWS_PROFILE_NAME = "AWS_PROFILE_NAME"
    UPSTAGE_API_KEY = "UPSTAGE_API_KEY"

    @property
    def value(self) -> Optional[str]:
        return os.getenv(self.name)


class LanguageModelId(str, Enum):
    CLAUDE_V3_HAIKU = "anthropic.claude-3-haiku-20240307-v1:0"
    CLAUDE_V3_5_HAIKU = "anthropic.claude-3-5-haiku-20241022-v1:0"
    CLAUDE_V3_5_SONNET = "anthropic.claude-3-5-sonnet-20240620-v1:0"
    CLAUDE_V3_5_SONNET_V2 = "anthropic.claude-3-5-sonnet-20241022-v2:0"
    CLAUDE_V3_7_SONNET = "anthropic.claude-3-7-sonnet-20250219-v1:0"


class LocalPaths(str, Enum):
    FIGURES_DIR = "figures"
    LOGS_DIR = "logs"

    CONFIG_FILE = "config.yaml"
    LOGS_FILE = "logs.txt"
    PARSED_FILE = "parsed.json"


class SSMParams(str, Enum):
    UPSTAGE_API_KEY = "lambda/upstage_api_key"


class URLs(str, Enum):
    ARXIV_HTML = "https://ar5iv.labs.arxiv.org/html"
    HF_DAILY_PAPERS = "https://huggingface.co/api/daily_papers"
    UPSTAGE_DOCUMENT_PARSE = "https://api.upstage.ai/v1/document-ai/document-parse"

    @property
    def url(self) -> str:
        return self.value
