from pathlib import Path
from typing import Literal

import yaml
from dotenv import load_dotenv
from pydantic import Field

from paper_bridge.cleaner.src import LocalPaths
from paper_bridge.shared import BaseModelWithDefaults


class Resources(BaseModelWithDefaults):
    project_name: str = Field(min_length=1)
    stage: Literal["dev", "prod"] = Field(default="dev")
    default_region_name: str = Field(default="us-west-2")


class Cleaner(BaseModelWithDefaults):
    days_back: int = Field(default=365, ge=1)
    days_range: int = Field(default=7, ge=1)
    opensearch_indexes: list[str] = Field(
        default_factory=lambda: ["chunk", "statement"]
    )


class Config(BaseModelWithDefaults):
    resources: Resources = Field(
        default_factory=lambda: Resources(project_name="paper-bridge")
    )
    cleaner: Cleaner = Field(default_factory=Cleaner)

    @classmethod
    def from_yaml(cls, file_path: str | Path) -> "Config":
        try:
            with open(file_path, encoding="utf-8") as file:
                config_data = yaml.safe_load(file) or {}
            return cls(**config_data)
        except (OSError, yaml.YAMLError) as e:
            raise ValueError(f"Failed to load config from {file_path}: {e}") from e

    @classmethod
    def load(cls) -> "Config":
        load_dotenv()
        config_path = Path(__file__).parent.parent / LocalPaths.CONFIG_FILE.value
        if not config_path.exists():
            return cls()
        return cls.from_yaml(config_path)
