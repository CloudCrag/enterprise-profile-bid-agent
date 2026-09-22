from __future__ import annotations
from src.shared.errors import ValidationError


def require_equal(label: str, expected: str, actual: str) -> None:
    if expected != actual:
        raise ValidationError(
            "agent_schema_validation_failed", f"{label} version mismatch",
            {"expected": expected, "actual": actual},
        )
