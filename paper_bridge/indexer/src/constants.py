import os
from enum import Enum
from typing import Optional


class LanguageModelId(str, Enum):
    CLAUDE_V3_HAIKU = "anthropic.claude-3-haiku-20240307-v1:0"
    CLAUDE_V3_SONNET = "anthropic.claude-3-sonnet-20240229-v1:0"
    CLAUDE_V3_OPUS = "anthropic.claude-3-opus-20240229-v1:0"
    CLAUDE_V3_5_HAIKU = "anthropic.claude-3-5-haiku-20241022-v1:0"
    CLAUDE_V3_5_SONNET = "anthropic.claude-3-5-sonnet-20240620-v1:0"
    CLAUDE_V3_5_SONNET_V2 = "anthropic.claude-3-5-sonnet-20241022-v2:0"

    COMMAND_R = "cohere.command-r-v1:0"
    COMMAND_R_PLUS = "cohere.command-r-plus-v1:0"

    LLAMA_V3_1_8B = "meta.llama3-1-8b-instruct-v1:0"
    LLAMA_V3_1_70B = "meta.llama3-1-70b-instruct-v1:0"
    LLAMA_V3_1_405B = "meta.llama3-1-405b-instruct-v1:0"

    MISTRAL_7B = "mistral.mistral-7b-instruct-v0:2"
    MISTRAL_8X7B = "mistral.mixtral-8x7b-instruct-v0:1"
    MISTRAL_SMALL = "mistral.mistral-small-2402-v1:0"
    MISTRAL_LARGE = "mistral.mistral-large-2407-v1:0"

    NOVA_MICRO = "amazon.nova-micro-v1:0"
    NOVA_PRO = "amazon.nova-pro-v1:0"


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
