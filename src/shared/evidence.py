from __future__ import annotations
from collections.abc import Iterable
from src.shared.errors import ValidationError


def validate_evidence_ids(referenced: Iterable[str], available: Iterable[str]) -> None:
    referenced_set = set(referenced)
    available_set = set(available)
    missing = sorted(referenced_set - available_set)
    if missing:
        raise ValidationError("agent_evidence_validation_failed", "Unknown evidence ids", {"missing": missing})
