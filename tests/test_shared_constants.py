"""Tests for shared constants — especially the centralized model IDs."""

import pytest

from paper_bridge.shared.constants import (
    NULL_STRING,
    EnvVars,
    Format,
    Language,
    LanguageModelId,
    SSMParams,
    URLs,
)


@pytest.mark.unit
class TestLanguageModelId:
    def test_sonnet_46_present_and_canonical(self) -> None:
        # The refactor standardizes summarization/response on Sonnet 4.6.
        assert LanguageModelId.CLAUDE_V4_6_SONNET.value == "anthropic.claude-sonnet-4-6"

    def test_haiku_45_present(self) -> None:
        assert (
            LanguageModelId.CLAUDE_V4_5_HAIKU.value
            == "anthropic.claude-haiku-4-5-20251001-v1:0"
        )

    def test_values_are_unique(self) -> None:
        values = [m.value for m in LanguageModelId]
        assert len(values) == len(set(values)), "duplicate model IDs in enum"

    def test_constructable_from_value(self) -> None:
        assert (
            LanguageModelId("anthropic.claude-sonnet-4-6")
            is LanguageModelId.CLAUDE_V4_6_SONNET
        )


@pytest.mark.unit
class TestEnums:
    def test_format_values(self) -> None:
        assert Format.SLACK.value == "slack"
        assert Format.HTML.value == "html"

    def test_language_values(self) -> None:
        assert Language.KO.value == "ko"
        assert Language.EN.value == "en"

    def test_null_string(self) -> None:
        assert NULL_STRING == "null"

    def test_url_property(self) -> None:
        assert URLs.HF_DAILY_PAPERS.url == "https://huggingface.co/api/daily_papers"

    def test_ssm_param_is_str_enum(self) -> None:
        assert SSMParams.NEPTUNE_ENDPOINT.value == "neptune-endpoint"

    def test_github_token_ssm_param(self) -> None:
        # The summarizer's GitHub PR path reads the token from this SSM parameter
        # (Terraform provisions /{project}-{stage}/github-token as a SecureString).
        # github_handler.py depends on this exact member existing.
        assert SSMParams.GITHUB_TOKEN.value == "github-token"

    def test_github_env_vars(self) -> None:
        # GITHUB_TOKEN is the local-dev secret fallback; GITHUB_REPO_NAME is the
        # deployment-specific target repo injected like S3_BUCKET_NAME.
        assert EnvVars.GITHUB_TOKEN.name == "GITHUB_TOKEN"
        assert EnvVars.GITHUB_REPO_NAME.name == "GITHUB_REPO_NAME"
