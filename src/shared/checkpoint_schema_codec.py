from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any

from pydantic import BaseModel

_SCHEMA_MARKER = "__bid_decision_agents_schema_v1__"
_SCHEMA_PAYLOAD = "payload"


class CheckpointSchemaCodec:
    """Encode approved Pydantic schema objects as plain msgpack-safe envelopes.

    LangGraph checkpoint 4.1.1 can fail to encode nested Pydantic objects when
    strict msgpack mode is enabled. This codec removes that dependency on the
    framework's Pydantic extension path: project-owned schema objects are first
    converted to JSON-mode data and later reconstructed only from a fixed type
    map. No dynamic imports or pickle fallback are used.
    """

    def __init__(self, schema_types: Iterable[type[Any]]) -> None:
        self._schema_types = tuple(
            schema_type
            for schema_type in schema_types
            if isinstance(schema_type, type) and issubclass(schema_type, BaseModel)
        )
        self._types_by_key: dict[tuple[str, str], type[BaseModel]] = {
            (schema_type.__module__, schema_type.__name__): schema_type
            for schema_type in self._schema_types
        }

    def encode(self, value: Any) -> Any:
        if isinstance(value, BaseModel):
            key = (value.__class__.__module__, value.__class__.__name__)
            if key in self._types_by_key:
                return {
                    _SCHEMA_MARKER: [key[0], key[1]],
                    _SCHEMA_PAYLOAD: value.model_dump(
                        mode="json",
                        by_alias=False,
                        warnings="error",
                    ),
                }
            return value
        if isinstance(value, dict):
            return {key: self.encode(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self.encode(item) for item in value]
        if isinstance(value, tuple):
            return tuple(self.encode(item) for item in value)
        return value

    def decode(self, value: Any) -> Any:
        if isinstance(value, dict):
            marker = value.get(_SCHEMA_MARKER)
            if (
                len(value) == 2
                and isinstance(marker, (list, tuple))
                and len(marker) == 2
                and _SCHEMA_PAYLOAD in value
            ):
                key = (str(marker[0]), str(marker[1]))
                schema_type = self._types_by_key.get(key)
                if schema_type is not None:
                    payload = value[_SCHEMA_PAYLOAD]
                    return schema_type.model_validate_json(
                        json.dumps(
                            payload,
                            ensure_ascii=False,
                            separators=(",", ":"),
                        )
                    )
            return {key: self.decode(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self.decode(item) for item in value]
        if isinstance(value, tuple):
            return tuple(self.decode(item) for item in value)
        return value
