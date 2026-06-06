"""Base Pydantic models with enhanced default handling."""

from typing import Any

from pydantic import BaseModel, model_validator


class BaseModelWithDefaults(BaseModel):
    """Base model that replaces None values with field defaults during validation.

    This is useful when loading configuration from YAML files where missing fields
    are represented as None rather than being absent.
    """

    @model_validator(mode="before")
    @classmethod
    def set_defaults_for_none_fields(cls, values: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(values, dict):
            return values
        for field_name, field in cls.model_fields.items():
            if values.get(field_name) is None and field.default is not None:
                values[field_name] = field.default
        return values
