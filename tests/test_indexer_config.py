"""Tests for ``paper_bridge.indexer.configs.config``.

Covers Config defaults/from_yaml, validation constraints, model-id enum fields
resolving, and the ``ModelHandler`` dimension/sequence-length helpers.
"""

import textwrap
from pathlib import Path

import pytest

from paper_bridge.indexer.configs.config import (
    Config,
    EmbeddingsModelId,
    Indexing,
    ModelHandler,
    Resources,
)
from paper_bridge.shared.constants import LanguageModelId


def _indexing(**overrides) -> Indexing:
    base = dict(
        extraction_model_id=LanguageModelId.CLAUDE_V4_5_HAIKU,
        response_model_id=LanguageModelId.CLAUDE_V4_6_SONNET,
        embeddings_model_id=EmbeddingsModelId.COHERE_EMBED_TEXT_V3,
    )
    base.update(overrides)
    return Indexing(**base)


@pytest.mark.unit
class TestDefaults:
    def test_default_config_loads(self) -> None:
        cfg = Config.load()
        assert cfg.resources.project_name
        assert isinstance(cfg.indexing.extraction_model_id, LanguageModelId)

    def test_indexing_numeric_defaults(self) -> None:
        idx = _indexing()
        assert idx.papers_per_day == 5
        assert idx.days_to_fetch == 7
        assert idx.chunk_size == 1024
        assert idx.chunk_overlap == 128
        assert idx.min_upvotes is None
        assert idx.use_llama_parse is False

    def test_resources_defaults(self) -> None:
        r = Resources(project_name="x")
        assert r.stage == "dev"
        assert r.bedrock_region_name == "us-west-2"
        assert r.s3_bucket_name is None


@pytest.mark.unit
class TestEnumResolution:
    def test_model_id_string_resolves_to_enum(self) -> None:
        idx = _indexing(extraction_model_id="anthropic.claude-sonnet-4-6")
        assert idx.extraction_model_id is LanguageModelId.CLAUDE_V4_6_SONNET

    def test_embeddings_model_id_string_resolves(self) -> None:
        idx = _indexing(embeddings_model_id="amazon.titan-embed-text-v2")
        assert idx.embeddings_model_id is EmbeddingsModelId.TITAN_EMBED_TEXT_V2

    def test_invalid_model_id_raises(self) -> None:
        with pytest.raises(Exception):
            _indexing(extraction_model_id="not-a-real-model")

    def test_optional_main_content_model_default_none(self) -> None:
        assert _indexing().main_content_extraction_model_id is None


@pytest.mark.unit
class TestValidation:
    @pytest.mark.parametrize("bad", [0, -1])
    def test_papers_per_day_ge_one(self, bad: int) -> None:
        with pytest.raises(Exception):
            _indexing(papers_per_day=bad)

    @pytest.mark.parametrize("bad", [0, -2])
    def test_days_to_fetch_ge_one(self, bad: int) -> None:
        with pytest.raises(Exception):
            _indexing(days_to_fetch=bad)

    def test_min_upvotes_ge_zero(self) -> None:
        assert _indexing(min_upvotes=0).min_upvotes == 0
        with pytest.raises(Exception):
            _indexing(min_upvotes=-1)

    def test_project_name_min_length(self) -> None:
        with pytest.raises(Exception):
            Resources(project_name="")

    def test_extraction_model_id_required(self) -> None:
        # Missing the required model-id field must error.
        with pytest.raises(Exception):
            Indexing(
                response_model_id=LanguageModelId.CLAUDE_V4_6_SONNET,
                embeddings_model_id=EmbeddingsModelId.COHERE_EMBED_TEXT_V3,
            )


@pytest.mark.unit
class TestFromYaml:
    def test_roundtrip(self, tmp_path: Path) -> None:
        yaml_text = textwrap.dedent(
            """
            resources:
              project_name: idx-bridge
              s3_bucket_name: my-bucket
            indexing:
              extraction_model_id: anthropic.claude-haiku-4-5-20251001-v1:0
              response_model_id: anthropic.claude-sonnet-4-6
              embeddings_model_id: cohere.embed-english-v3
              papers_per_day: 10
              chunk_size: 512
            """
        )
        path = tmp_path / "config.yaml"
        path.write_text(yaml_text, encoding="utf-8")
        cfg = Config.from_yaml(path)
        assert cfg.resources.project_name == "idx-bridge"
        assert cfg.resources.s3_bucket_name == "my-bucket"
        assert cfg.indexing.papers_per_day == 10
        assert cfg.indexing.chunk_size == 512
        assert (
            cfg.indexing.embeddings_model_id is EmbeddingsModelId.COHERE_EMBED_TEXT_V3
        )

    def test_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError):
            Config.from_yaml(tmp_path / "missing.yaml")

    def test_empty_yaml_uses_defaults(self, tmp_path: Path) -> None:
        path = tmp_path / "empty.yaml"
        path.write_text("", encoding="utf-8")
        cfg = Config.from_yaml(path)
        assert cfg.indexing.papers_per_day == 5


@pytest.mark.unit
class TestModelHandler:
    def test_get_dimensions_scalar(self) -> None:
        assert (
            ModelHandler.get_dimensions(EmbeddingsModelId.COHERE_EMBED_TEXT_V3) == 1024
        )

    def test_get_dimensions_list_modes(self) -> None:
        mid = EmbeddingsModelId.TITAN_EMBED_TEXT_V2
        assert ModelHandler.get_dimensions(mid, mode="max") == 1024
        assert ModelHandler.get_dimensions(mid, mode="min") == 256
        assert ModelHandler.get_dimensions(mid, index=1) == 384
        assert ModelHandler.get_dimensions(mid) == [256, 384, 1024]

    def test_get_dimensions_out_of_range_index_returns_full_list(self) -> None:
        mid = EmbeddingsModelId.TITAN_EMBED_TEXT_V2
        assert ModelHandler.get_dimensions(mid, index=99) == [256, 384, 1024]

    def test_get_dimensions_none_for_language_model(self) -> None:
        # Language models have no dimensions entry.
        assert ModelHandler.get_dimensions(LanguageModelId.CLAUDE_V3_HAIKU) is None

    def test_get_dimensions_none_for_v4_language_model(self) -> None:
        # v4 language models are registered for max_sequence_length but still have
        # no embedding dimensions (only embedding models do).
        assert ModelHandler.get_dimensions(LanguageModelId.CLAUDE_V4_6_SONNET) is None

    def test_get_max_sequence_length(self) -> None:
        assert (
            ModelHandler.get_max_sequence_length(EmbeddingsModelId.COHERE_EMBED_TEXT_V3)
            == 512
        )

    def test_get_max_sequence_length_v4_models_registered(self) -> None:
        # v4 Claude models (the ones actually in use) are now registered.
        for model in (
            LanguageModelId.CLAUDE_V4_6_SONNET,
            LanguageModelId.CLAUDE_V4_5_HAIKU,
        ):
            assert ModelHandler.get_max_sequence_length(model) == 200000
