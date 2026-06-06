"""Guard tests: the shipped on-disk config.yaml files must parse into their
Config models.

The earlier audit found bugs where config files / code drifted from the Config
schema (e.g. a handler reading ``s3_outputs_path`` that didn't exist on the
model). These tests load the REAL config files (not test-authored YAML) so any
such drift fails CI instead of crashing at runtime.
"""

from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
PB = REPO / "paper_bridge"


@pytest.mark.unit
class TestShippedConfigsParse:
    def test_summarizer_config_yaml_parses(self) -> None:
        from paper_bridge.summarizer.configs.config import Config

        cfg = Config.from_yaml(PB / "summarizer" / "configs" / "config.yaml")
        # spot-check a few load-bearing fields exist with expected types
        assert cfg.summarization.paper_summarization_model_id is not None
        assert isinstance(cfg.resources.s3_outputs_path, str)
        assert isinstance(cfg.summarization.enable_prompt_caching, bool)
        assert cfg.summarization.selection_popularity_weight >= 0

    def test_indexer_config_yaml_parses(self) -> None:
        from paper_bridge.indexer.configs.config import Config

        cfg = Config.from_yaml(PB / "indexer" / "configs" / "config.yaml")
        assert cfg.indexing.extraction_model_id is not None
        assert cfg.indexing.response_model_id is not None
        assert cfg.indexing.embeddings_model_id is not None
        assert cfg.indexing.selection_recency_half_life_days > 0

    def test_cleaner_config_yaml_parses(self) -> None:
        from paper_bridge.cleaner.configs.config import Config

        path = PB / "cleaner" / "configs" / "config.yaml"
        if not path.exists():
            pytest.skip("cleaner config.yaml not present")
        cfg = Config.from_yaml(path)
        assert cfg is not None

    def test_cleaner_template_config_parses_if_present(self) -> None:
        from paper_bridge.cleaner.configs.config import Config

        path = PB / "cleaner" / "configs" / "config-template.yaml"
        if not path.exists():
            pytest.skip("no cleaner config-template.yaml")
        # Template may have placeholder values; it must at least be schema-valid.
        Config.from_yaml(path)
