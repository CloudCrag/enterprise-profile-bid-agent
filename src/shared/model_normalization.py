from __future__ import annotations

import json
from typing import Any, TypeVar

from pydantic import BaseModel

ModelT = TypeVar("ModelT", bound=BaseModel)


def ensure_model(value: Any, model_type: type[ModelT]) -> ModelT:
    """Reconstruct a strict Pydantic type at serialization/checkpoint boundaries."""
    if isinstance(value, model_type):
        return value
    if isinstance(value, (str, bytes, bytearray)):
        return model_type.model_validate_json(value)
    return model_type.model_validate_json(
        json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
    )


def ensure_model_list(values: list[Any] | tuple[Any, ...], model_type: type[ModelT]) -> list[ModelT]:
    return [ensure_model(value, model_type) for value in values]
