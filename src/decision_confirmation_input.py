"""Validation helpers for explicit user decision confirmations."""
from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

from .enterprise_decision_profile import (
    DECISION_CONFIRMATION_INPUT_SCHEMA_VERSION,
    DECISION_FIELD_CODES,
    confirmation_input_content_hash,
)
from .errors import InputDataError

_ROOT = Path(__file__).resolve().parents[1]
_SCHEMA = json.loads((_ROOT / "schemas" / "enterprise_decision_confirmation_input.schema.json").read_text(encoding="utf-8"))
Draft202012Validator.check_schema(_SCHEMA)
_VALIDATOR = Draft202012Validator(_SCHEMA, format_checker=FormatChecker())


def _issue(code: str, path: str, message: str) -> dict[str, str]:
    return {"code": code, "field_path": path, "message": message}


def _is_timezone_aware_datetime(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() is not None


def validate_confirmation_input(value: Any) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    if not isinstance(value, dict):
        return [_issue("decision_confirmation_input_schema_invalid", "$", "Confirmation input must be an object")]
    for error in sorted(_VALIDATOR.iter_errors(value), key=lambda item: (list(item.absolute_path), item.message)):
        path = ".".join(str(part) for part in error.absolute_path) or "$"
        issues.append(_issue("decision_confirmation_input_schema_invalid", path, error.message))

    actor = value.get("confirmation_actor") or {}
    actor_id = actor.get("actor_id")
    if actor.get("actor_type") != "user":
        issues.append(_issue("decision_actor_required", "confirmation_actor.actor_type", "actor_type must be user"))
    if not isinstance(actor_id, str) or not actor_id.strip():
        issues.append(_issue("decision_actor_id_invalid", "confirmation_actor.actor_id", "actor_id must be a non-empty string"))
    if not _is_timezone_aware_datetime(value.get("confirmed_at")):
        issues.append(_issue("decision_confirmation_time_invalid", "confirmed_at", "confirmed_at must be an externally provided timezone-aware ISO datetime"))

    updates = value.get("updates") if isinstance(value.get("updates"), list) else []
    codes = [item.get("field_code") for item in updates if isinstance(item, dict)]
    duplicates = sorted({code for code in codes if codes.count(code) > 1})
    if duplicates:
        issues.append(_issue("decision_field_duplicate", "updates", f"Duplicate field_code values are not allowed: {duplicates}"))
    unknown = sorted({code for code in codes if code not in DECISION_FIELD_CODES})
    if unknown:
        issues.append(_issue("decision_field_catalog_mismatch", "updates", f"Unknown field_code values: {unknown}"))

    minimum = None
    maximum = None
    for index, update in enumerate(updates):
        if not isinstance(update, dict) or update.get("field_code") != "budget_preference" or update.get("action") != "set":
            continue
        budget = update.get("value") or {}
        minimum = budget.get("minimum")
        maximum = budget.get("maximum")
        _validate_budget_point(minimum, f"updates.{index}.value.minimum", issues)
        _validate_budget_point(maximum, f"updates.{index}.value.maximum", issues)
        if isinstance(minimum, dict) and isinstance(maximum, dict):
            min_value = minimum.get("normalized_value")
            max_value = maximum.get("normalized_value")
            if min_value is not None and max_value is not None:
                if minimum.get("normalized_currency") != maximum.get("normalized_currency"):
                    issues.append(_issue("decision_budget_currency_mismatch", f"updates.{index}.value", "Budget minimum and maximum currencies must match"))
                if minimum.get("normalized_unit") != maximum.get("normalized_unit"):
                    issues.append(_issue("decision_budget_unit_mismatch", f"updates.{index}.value", "Budget minimum and maximum units must match"))
                if minimum.get("normalized_currency") == maximum.get("normalized_currency") and minimum.get("normalized_unit") == maximum.get("normalized_unit") and min_value > max_value:
                    issues.append(_issue("decision_budget_range_invalid", f"updates.{index}.value", "Budget minimum cannot exceed maximum"))

    expected_hash = confirmation_input_content_hash(value)
    if value.get("confirmation_input_content_hash") != expected_hash:
        issues.append(_issue("decision_confirmation_input_hash_mismatch", "confirmation_input_content_hash", "Confirmation input content hash mismatch"))
    return issues


def _validate_budget_point(point: Any, path: str, issues: list[dict[str, str]]) -> None:
    if point is None or not isinstance(point, dict):
        return
    normalized_value = point.get("normalized_value")
    unit = point.get("normalized_unit")
    currency = point.get("normalized_currency")
    if normalized_value is not None and (not isinstance(unit, str) or not unit or not isinstance(currency, str) or not currency):
        issues.append(_issue("decision_budget_normalization_incomplete", path, "A normalized value requires explicit unit and currency"))
    if normalized_value is None and (unit is not None or currency is not None):
        issues.append(_issue("decision_budget_normalization_incomplete", path, "Unit and currency must remain null when normalized_value is null"))


def assert_valid_confirmation_input(value: Any) -> None:
    issues = validate_confirmation_input(value)
    if issues:
        raise InputDataError(json.dumps(issues[0], ensure_ascii=False, sort_keys=True))
