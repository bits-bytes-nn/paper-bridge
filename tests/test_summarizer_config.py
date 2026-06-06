"""Tests for summarizer configuration — model wiring and new token fields."""

import textwrap
from pathlib import Path

import pytest

from paper_bridge.shared.constants import LanguageModelId
from paper_bridge.summarizer.configs.config import Config, Retrieval, Summarization


@pytest.mark.unit
class TestModelWiring:
    def test_default_config_loads(self) -> None:
        cfg = Config.load()
        assert cfg is not None

    def test_summarization_uses_sonnet_46(self) -> None:
        cfg = Config.load()
        assert (
            cfg.summarization.paper_summarization_model_id
            is LanguageModelId.CLAUDE_V4_6_SONNET
        )

    def test_retrieval_uses_sonnet_46(self) -> None:
        cfg = Config.load()
        assert (
            cfg.retrieval.retrieval_summarization_model_id
            is LanguageModelId.CLAUDE_V4_6_SONNET
        )

    def test_figure_analysis_uses_haiku_45(self) -> None:
        cfg = Config.load()
        assert (
            cfg.summarization.figure_analysis_model_id
            is LanguageModelId.CLAUDE_V4_5_HAIKU
        )


@pytest.mark.unit
class TestMaxTokensFields:
    def test_summarization_max_tokens_default(self) -> None:
        assert Summarization().summarization_max_tokens == 8192

    def test_retrieval_max_tokens_default(self) -> None:
        assert Retrieval().retrieval_max_tokens == 8192

    def test_max_tokens_must_be_positive(self) -> None:
        with pytest.raises(Exception):
            Summarization(summarization_max_tokens=0)

    def test_max_tokens_override(self) -> None:
        assert (
            Summarization(summarization_max_tokens=16384).summarization_max_tokens
            == 16384
        )

    def test_figure_analysis_max_tokens_default(self) -> None:
        assert Summarization().figure_analysis_max_tokens == 4096

    def test_figure_analysis_max_tokens_must_be_positive(self) -> None:
        with pytest.raises(Exception):
            Summarization(figure_analysis_max_tokens=0)


@pytest.mark.unit
class TestFromYaml:
    def test_from_yaml_roundtrip(self, tmp_path: Path) -> None:
        yaml_text = textwrap.dedent(
            """
            resources:
              project_name: test-bridge
              s3_bucket_name: test-bucket
            summarization:
              paper_summarization_model_id: anthropic.claude-sonnet-4-6
              summarization_max_tokens: 4096
            retrieval:
              retrieval_summarization_model_id: anthropic.claude-sonnet-4-6
              retrieval_max_tokens: 2048
            """
        )
        path = tmp_path / "config.yaml"
        path.write_text(yaml_text, encoding="utf-8")

        cfg = Config.from_yaml(path)
        assert cfg.resources.project_name == "test-bridge"
        assert cfg.summarization.summarization_max_tokens == 4096
        assert cfg.retrieval.retrieval_max_tokens == 2048

    def test_from_yaml_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError):
            Config.from_yaml(tmp_path / "does_not_exist.yaml")

    def test_none_fields_fall_back_to_defaults(self, tmp_path: Path) -> None:
        # BaseModelWithDefaults should replace explicit None with the field default.
        yaml_text = textwrap.dedent(
            """
            resources:
              project_name: test-bridge
              s3_bucket_name: test-bucket
            summarization:
              summarization_max_tokens: null
            """
        )
        path = tmp_path / "config.yaml"
        path.write_text(yaml_text, encoding="utf-8")
        cfg = Config.from_yaml(path)
        assert cfg.summarization.summarization_max_tokens == 8192
