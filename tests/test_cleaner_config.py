"""Tests for ``paper_bridge.cleaner.configs.config`` Config loading & validation."""

import textwrap
from pathlib import Path

import pytest

from paper_bridge.cleaner.configs.config import Cleaner, Config, Resources


@pytest.mark.unit
class TestDefaults:
    def test_default_config_loads(self) -> None:
        cfg = Config.load()
        assert cfg.resources.project_name
        assert cfg.cleaner.days_back >= 1
        assert cfg.cleaner.days_range >= 1

    def test_cleaner_defaults(self) -> None:
        c = Cleaner()
        assert c.days_back == 365
        assert c.days_range == 7
        assert c.opensearch_indexes == ["chunk", "statement"]

    def test_resources_defaults(self) -> None:
        r = Resources(project_name="x")
        assert r.stage == "dev"
        assert r.default_region_name == "us-west-2"

    def test_default_factory_independent_lists(self) -> None:
        # default_factory must not share a single mutable list across instances.
        a = Cleaner()
        b = Cleaner()
        a.opensearch_indexes.append("extra")
        assert b.opensearch_indexes == ["chunk", "statement"]


@pytest.mark.unit
class TestValidation:
    @pytest.mark.parametrize("bad", [0, -1, -100])
    def test_days_back_ge_one(self, bad: int) -> None:
        with pytest.raises(Exception):
            Cleaner(days_back=bad)

    @pytest.mark.parametrize("bad", [0, -5])
    def test_days_range_ge_one(self, bad: int) -> None:
        with pytest.raises(Exception):
            Cleaner(days_range=bad)

    def test_project_name_min_length(self) -> None:
        with pytest.raises(Exception):
            Resources(project_name="")

    def test_invalid_stage_literal(self) -> None:
        with pytest.raises(Exception):
            Resources(project_name="x", stage="staging")

    def test_valid_stages(self) -> None:
        assert Resources(project_name="x", stage="prod").stage == "prod"


@pytest.mark.unit
class TestFromYaml:
    def test_roundtrip(self, tmp_path: Path) -> None:
        yaml_text = textwrap.dedent(
            """
            resources:
              project_name: my-bridge
              stage: prod
            cleaner:
              days_back: 30
              days_range: 2
              opensearch_indexes:
                - chunk
            """
        )
        path = tmp_path / "config.yaml"
        path.write_text(yaml_text, encoding="utf-8")
        cfg = Config.from_yaml(path)
        assert cfg.resources.project_name == "my-bridge"
        assert cfg.resources.stage == "prod"
        assert cfg.cleaner.days_back == 30
        assert cfg.cleaner.days_range == 2
        assert cfg.cleaner.opensearch_indexes == ["chunk"]

    def test_missing_file_raises_value_error(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError):
            Config.from_yaml(tmp_path / "nope.yaml")

    def test_malformed_yaml_raises_value_error(self, tmp_path: Path) -> None:
        path = tmp_path / "bad.yaml"
        path.write_text("resources: [unterminated", encoding="utf-8")
        with pytest.raises(ValueError):
            Config.from_yaml(path)

    def test_empty_yaml_uses_defaults(self, tmp_path: Path) -> None:
        # yaml.safe_load returns None for empty files; ``or {}`` recovers defaults.
        path = tmp_path / "empty.yaml"
        path.write_text("", encoding="utf-8")
        cfg = Config.from_yaml(path)
        assert cfg.resources.project_name == "paper-bridge"
        assert cfg.cleaner.days_back == 365

    def test_none_field_falls_back_to_default(self, tmp_path: Path) -> None:
        path = tmp_path / "config.yaml"
        path.write_text(
            "cleaner:\n  days_back: null\n  days_range: 4\n", encoding="utf-8"
        )
        cfg = Config.from_yaml(path)
        assert cfg.cleaner.days_back == 365  # None → default
        assert cfg.cleaner.days_range == 4
