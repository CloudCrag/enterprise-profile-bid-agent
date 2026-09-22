"""Error helpers for task-scoped profile gap analysis."""

from __future__ import annotations

from typing import Any


def issue(code: str, field_path: str, message: str, **extra: Any) -> dict[str, Any]:
    result: dict[str, Any] = {"code": code, "field_path": field_path, "message": message}
    result.update(extra)
    return result
