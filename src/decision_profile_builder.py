"""Create and update enterprise decision profiles from explicit user confirmation input."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from .capability_validator import assert_valid_capability_profile
from .decision_confirmation_input import assert_valid_confirmation_input
from .enterprise_capability_profile import (
    CAPABILITY_PROFILE_BASELINE_SCHEMA_VERSION,
    CAPABILITY_PROFILE_REVIEWED_SCHEMA_VERSION,
)
from .enterprise_decision_profile import (
    DECISION_CONFIRMATION_INPUT_SCHEMA_VERSION,
    DECISION_FIELDS,
    DECISION_PROFILE_SCHEMA_VERSION,
    GENERATION_MODE,
    finalize_decision_profile,
    load_decision_field_catalog,
    normalize_decision_value,
)
from .enterprise_fact_profile import FACT_PROFILE_SCHEMA_VERSION, fact_profile_content_hash
from .errors import InputDataError
from .fact_validator import assert_valid_fact_profile

_ALLOWED_CAPABILITY_VERSIONS = {CAPABILITY_PROFILE_BASELINE_SCHEMA_VERSION, CAPABILITY_PROFILE_REVIEWED_SCHEMA_VERSION}


def _context_dependencies(fact_profile: dict[str, Any], capability_profile: dict[str, Any]) -> dict[str, Any]:
    return {
        "fact_profile_schema_version": fact_profile.get("fact_profile_schema_version"),
        "fact_profile_content_hash": fact_profile_content_hash(fact_profile),
        "capability_profile_schema_version": capability_profile.get("capability_profile_schema_version"),
        "capability_profile_id": capability_profile.get("capability_profile_id"),
        "capability_profile_content_hash": capability_profile.get("capability_content_hash"),
    }


def validate_decision_context(fact_profile: dict[str, Any], capability_profile: dict[str, Any]) -> None:
    assert_valid_fact_profile(fact_profile)
    assert_valid_capability_profile(capability_profile)
    if fact_profile.get("fact_profile_schema_version") != FACT_PROFILE_SCHEMA_VERSION:
        raise InputDataError("decision_fact_dependency_mismatch: unsupported fact profile version")
    if capability_profile.get("capability_profile_schema_version") not in _ALLOWED_CAPABILITY_VERSIONS:
        raise InputDataError("decision_capability_dependency_mismatch: capability profile must be 1.1.0 or 1.2.0")
    if fact_profile.get("enterprise") != capability_profile.get("enterprise"):
        raise InputDataError("decision_context_enterprise_mismatch: fact and capability enterprise identifiers differ")
    if fact_profile.get("as_of_date") != capability_profile.get("as_of_date"):
        raise InputDataError("decision_context_as_of_date_mismatch: fact and capability as_of_date differ")
    dependencies = capability_profile.get("source_dependencies") or {}
    if dependencies.get("fact_profile_schema_version") != fact_profile.get("fact_profile_schema_version"):
        raise InputDataError("decision_fact_dependency_mismatch: capability fact schema dependency differs")
    if dependencies.get("fact_profile_content_hash") != fact_profile_content_hash(fact_profile):
        raise InputDataError("decision_fact_dependency_mismatch: capability fact hash dependency differs")


def _empty_fields() -> list[dict[str, Any]]:
    return [
        {
            "field_code": code,
            "field_name": name,
            "confirmation_status": "not_provided",
            "value": None,
            "last_user_update": None,
        }
        for code, name in DECISION_FIELDS
    ]


def apply_confirmation_updates(
    fields: list[dict[str, Any]],
    confirmation_input: dict[str, Any],
) -> tuple[list[dict[str, Any]], bool]:
    result = deepcopy(fields)
    by_code = {field["field_code"]: field for field in result}
    actor = confirmation_input["confirmation_actor"]
    confirmed_at = confirmation_input["confirmed_at"]
    changed = False
    for update in confirmation_input["updates"]:
        field = by_code[update["field_code"]]
        before_state = (field.get("confirmation_status"), deepcopy(field.get("value")))
        if update["action"] == "set":
            field["confirmation_status"] = "confirmed"
            field["value"] = normalize_decision_value(update["field_code"], update["value"])
        else:
            field["confirmation_status"] = "not_provided"
            field["value"] = None
        after_state = (field.get("confirmation_status"), deepcopy(field.get("value")))
        changed = changed or before_state != after_state
        field["last_user_update"] = {
            "actor_type": "user",
            "actor_id": actor["actor_id"],
            "confirmed_at": confirmed_at,
            "action": update["action"],
        }
    return result, changed


def _summary(fields: list[dict[str, Any]]) -> dict[str, int]:
    confirmed = sum(1 for field in fields if field["confirmation_status"] == "confirmed")
    return {
        "total_field_count": len(fields),
        "confirmed_field_count": confirmed,
        "not_provided_field_count": len(fields) - confirmed,
    }


def build_decision_profile(
    fact_profile: dict[str, Any],
    capability_profile: dict[str, Any],
    confirmation_input: dict[str, Any],
    *,
    base_decision_profile: dict[str, Any] | None = None,
    generated_at_utc: str | None = None,
) -> dict[str, Any]:
    load_decision_field_catalog()
    validate_decision_context(fact_profile, capability_profile)
    assert_valid_confirmation_input(confirmation_input)
    if confirmation_input.get("enterprise") != fact_profile.get("enterprise"):
        raise InputDataError("decision_context_enterprise_mismatch: confirmation enterprise differs from context")

    if base_decision_profile is None:
        if confirmation_input.get("base_decision_profile_id") is not None or confirmation_input.get("base_decision_profile_content_hash") is not None:
            raise InputDataError("decision_previous_profile_mismatch: initial confirmation must have null base identifiers")
        profile_version = 1
        previous_id = None
        previous_hash = None
        fields = _empty_fields()
    else:
        from .decision_profile_validator import assert_valid_decision_profile

        assert_valid_decision_profile(base_decision_profile)
        if base_decision_profile.get("enterprise") != fact_profile.get("enterprise"):
            raise InputDataError("decision_previous_profile_mismatch: base profile belongs to a different enterprise")
        if confirmation_input.get("base_decision_profile_id") != base_decision_profile.get("decision_profile_id"):
            raise InputDataError("decision_previous_profile_mismatch: base decision profile ID mismatch")
        if confirmation_input.get("base_decision_profile_content_hash") != base_decision_profile.get("decision_profile_content_hash"):
            raise InputDataError("decision_previous_profile_mismatch: base decision profile hash mismatch")
        profile_version = int(base_decision_profile["profile_version"]) + 1
        previous_id = base_decision_profile["decision_profile_id"]
        previous_hash = base_decision_profile["decision_profile_content_hash"]
        fields = deepcopy(base_decision_profile["decision_fields"])

    updated_fields, changed = apply_confirmation_updates(fields, confirmation_input)
    if base_decision_profile is not None and not changed:
        raise InputDataError("decision_profile_no_change: updates do not change any decision field state or value")

    profile = {
        "decision_profile_schema_version": DECISION_PROFILE_SCHEMA_VERSION,
        "profile_version": profile_version,
        "previous_decision_profile_id": previous_id,
        "previous_decision_profile_content_hash": previous_hash,
        "enterprise": deepcopy(fact_profile["enterprise"]),
        "as_of_date": fact_profile.get("as_of_date"),
        "confirmation_context_dependencies": _context_dependencies(fact_profile, capability_profile),
        "generation": {
            "generation_mode": GENERATION_MODE,
            "confirmation_input_schema_version": DECISION_CONFIRMATION_INPUT_SCHEMA_VERSION,
            "confirmation_input_content_hash": confirmation_input["confirmation_input_content_hash"],
        },
        "decision_fields": updated_fields,
        "decision_summary": _summary(updated_fields),
        "warnings": [],
    }
    result = finalize_decision_profile(
        profile,
        generated_at_utc=generated_at_utc or datetime.now(timezone.utc).isoformat(),
    )
    from .decision_profile_validator import assert_valid_decision_profile

    assert_valid_decision_profile(
        result,
        fact_profile=fact_profile,
        capability_profile=capability_profile,
        previous_decision_profile=base_decision_profile,
        confirmation_input=confirmation_input,
    )
    return result
