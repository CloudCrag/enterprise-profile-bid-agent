"""Error helpers for enterprise profile question-response intake."""
from __future__ import annotations
from typing import Any


def issue(code: str, field_path: str, message: str, **extra: Any) -> dict[str, Any]:
    value: dict[str, Any] = {"code": code, "field_path": field_path, "message": message}
    value.update(extra)
    return value
