"""Schema, integrity, dependency, and version validation for enterprise decision profiles."""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

from .decision_confirmation_input import assert_valid_confirmation_input
from .enterprise_decision_profile import (
    DECISION_FIELDS,
    DECISION_PROFILE_SCHEMA_VERSION,
    decision_profile_content_hash,
    decision_profile_id,
    load_decision_field_catalog,
)
from .enterprise_fact_profile import fact_profile_content_hash
from .errors import InputDataError

_ROOT = Path(__file__).resolve().parents[1]
_SCHEMA = json.loads((_ROOT / "schemas" / "enterprise_decision_profile.schema.json").read_text(encoding="utf-8"))
Draft202012Validator.check_schema(_SCHEMA)
_VALIDATOR = Draft202012Validator(_SCHEMA, format_checker=FormatChecker())


def _issue(code: str, path: str, message: str) -> dict[str, str]:
    return {"code": code, "field_path": path, "message": message}


def validate_decision_profile(
    profile: Any,
    *,
    fact_profile: dict[str, Any] | None = None,
    capability_profile: dict[str, Any] | None = None,
    previous_decision_profile: dict[str, Any] | None = None,
    confirmation_input: dict[str, Any] | None = None,
) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    if not isinstance(profile, dict):
        return [_issue("decision_profile_schema_invalid", "$", "Decision profile must be an object")]
    for error in sorted(_VALIDATOR.iter_errors(profile), key=lambda item: (list(item.absolute_path), item.message)):
        path = ".".join(str(part) for part in error.absolute_path) or "$"
        issues.append(_issue("decision_profile_schema_invalid", path, error.message))

    try:
        load_decision_field_catalog()
    except InputDataError as exc:
        issues.append(_issue("decision_field_catalog_mismatch", "decision_fields", str(exc)))

    fields = profile.get("decision_fields") if isinstance(profile.get("decision_fields"), list) else []
    codes = [field.get("field_code") for field in fields if isinstance(field, dict)]
    expected_codes = [code for code, _ in DECISION_FIELDS]
    if len(codes) != len(set(codes)):
        issues.append(_issue("decision_field_duplicate", "decision_fields", "Decision field_code values must be unique"))
    if codes != expected_codes:
        issues.append(_issue("decision_field_catalog_mismatch", "decision_fields", f"Decision fields must exactly match {expected_codes}"))
    expected_names = dict(DECISION_FIELDS)
    for index, field in enumerate(fields):
        if not isinstance(field, dict):
            continue
        code = field.get("field_code")
        if code in expected_names and field.get("field_name") != expected_names[code]:
            issues.append(_issue("decision_field_catalog_mismatch", f"decision_fields.{index}.field_name", f"Expected {expected_names[code]}"))
        status = field.get("confirmation_status")
        value = field.get("value")
        update = field.get("last_user_update")
        if status == "not_provided" and value is not None:
            issues.append(_issue("decision_field_status_value_mismatch", f"decision_fields.{index}.value", "not_provided fields must have null value"))
        if status == "confirmed" and not isinstance(update, dict):
            issues.append(_issue("decision_actor_required", f"decision_fields.{index}.last_user_update", "confirmed fields require explicit user update metadata"))
        if isinstance(update, dict):
            actor_id = update.get("actor_id")
            if update.get("actor_type") != "user":
                issues.append(_issue("decision_actor_required", f"decision_fields.{index}.last_user_update.actor_type", "actor_type must be user"))
            if not isinstance(actor_id, str) or not actor_id.strip():
                issues.append(_issue("decision_actor_id_invalid", f"decision_fields.{index}.last_user_update.actor_id", "actor_id must be non-empty"))
            if status == "confirmed" and update.get("action") != "set":
                issues.append(_issue("decision_field_status_value_mismatch", f"decision_fields.{index}.last_user_update.action", "confirmed fields must originate from set"))
            if status == "not_provided" and update.get("action") != "clear":
                issues.append(_issue("decision_field_status_value_mismatch", f"decision_fields.{index}.last_user_update.action", "provided metadata for not_provided fields must describe clear"))

    counts = Counter(field.get("confirmation_status") for field in fields if isinstance(field, dict))
    expected_summary = {
        "total_field_count": len(fields),
        "confirmed_field_count": counts["confirmed"],
        "not_provided_field_count": counts["not_provided"],
    }
    if profile.get("decision_summary") != expected_summary:
        issues.append(_issue("decision_summary_mismatch", "decision_summary", f"Expected {expected_summary}"))

    version = profile.get("profile_version")
    previous_id = profile.get("previous_decision_profile_id")
    previous_hash = profile.get("previous_decision_profile_content_hash")
    if version == 1:
        if previous_id is not None or previous_hash is not None:
            issues.append(_issue("decision_profile_version_invalid", "profile_version", "Version 1 must not have previous profile dependencies"))
    elif isinstance(version, int) and version > 1:
        if not previous_id or not previous_hash:
            issues.append(_issue("decision_profile_version_invalid", "profile_version", "Updated versions require previous profile ID and hash"))
    else:
        issues.append(_issue("decision_profile_version_invalid", "profile_version", "profile_version must be a positive integer"))

    digest = decision_profile_content_hash(profile)
    if profile.get("decision_profile_content_hash") != digest:
        issues.append(_issue("decision_profile_content_hash_mismatch", "decision_profile_content_hash", "Decision profile content hash mismatch"))
    try:
        expected_id = decision_profile_id(profile, digest)
    except InputDataError as exc:
        issues.append(_issue("decision_profile_id_mismatch", "decision_profile_id", str(exc)))
    else:
        if profile.get("decision_profile_id") != expected_id:
            issues.append(_issue("decision_profile_id_mismatch", "decision_profile_id", "Decision profile ID mismatch"))

    if fact_profile is not None or capability_profile is not None:
        if fact_profile is None or capability_profile is None:
            issues.append(_issue("decision_context_dependencies_incomplete", "confirmation_context_dependencies", "Both fact and capability profiles are required"))
        else:
            from .decision_profile_builder import validate_decision_context
            try:
                validate_decision_context(fact_profile, capability_profile)
            except InputDataError as exc:
                text = str(exc)
                code = "decision_capability_dependency_mismatch" if "capability" in text else "decision_fact_dependency_mismatch"
                if "enterprise" in text:
                    code = "decision_context_enterprise_mismatch"
                issues.append(_issue(code, "confirmation_context_dependencies", text))
            if profile.get("enterprise") != fact_profile.get("enterprise"):
                issues.append(_issue("decision_context_enterprise_mismatch", "enterprise", "Decision profile enterprise differs from context"))
            if profile.get("as_of_date") != fact_profile.get("as_of_date"):
                issues.append(_issue("decision_context_as_of_date_mismatch", "as_of_date", "Decision profile as_of_date differs from context"))
            expected_dependencies = {
                "fact_profile_schema_version": fact_profile.get("fact_profile_schema_version"),
                "fact_profile_content_hash": fact_profile_content_hash(fact_profile),
                "capability_profile_schema_version": capability_profile.get("capability_profile_schema_version"),
                "capability_profile_id": capability_profile.get("capability_profile_id"),
                "capability_profile_content_hash": capability_profile.get("capability_content_hash"),
            }
            deps = profile.get("confirmation_context_dependencies") or {}
            for key, expected in expected_dependencies.items():
                if deps.get(key) != expected:
                    code = "decision_fact_dependency_mismatch" if key.startswith("fact_") else "decision_capability_dependency_mismatch"
                    issues.append(_issue(code, f"confirmation_context_dependencies.{key}", f"Expected {expected}"))

    if previous_decision_profile is not None:
        nested = validate_decision_profile(previous_decision_profile)
        if nested:
            issues.append(_issue("decision_previous_profile_mismatch", "previous_decision_profile_id", nested[0]["message"]))
        if profile.get("previous_decision_profile_id") != previous_decision_profile.get("decision_profile_id") or profile.get("previous_decision_profile_content_hash") != previous_decision_profile.get("decision_profile_content_hash"):
            issues.append(_issue("decision_previous_profile_mismatch", "previous_decision_profile_id", "Previous profile dependency mismatch"))
        if profile.get("profile_version") != previous_decision_profile.get("profile_version", 0) + 1:
            issues.append(_issue("decision_profile_version_invalid", "profile_version", "Updated profile version must equal previous version plus one"))
        if profile.get("enterprise") != previous_decision_profile.get("enterprise"):
            issues.append(_issue("decision_previous_profile_mismatch", "enterprise", "Previous profile belongs to a different enterprise"))

    if confirmation_input is not None:
        try:
            assert_valid_confirmation_input(confirmation_input)
        except InputDataError as exc:
            issues.append(_issue("decision_confirmation_input_schema_invalid", "generation.confirmation_input_content_hash", str(exc)))
        generation = profile.get("generation") or {}
        if generation.get("confirmation_input_schema_version") != confirmation_input.get("confirmation_input_schema_version") or generation.get("confirmation_input_content_hash") != confirmation_input.get("confirmation_input_content_hash"):
            issues.append(_issue("decision_confirmation_input_hash_mismatch", "generation", "Decision profile does not reference the supplied confirmation input"))
        if confirmation_input.get("enterprise") != profile.get("enterprise"):
            issues.append(_issue("decision_context_enterprise_mismatch", "enterprise", "Confirmation enterprise differs from decision profile"))
        base = previous_decision_profile
        if base is None:
            if confirmation_input.get("base_decision_profile_id") is not None or confirmation_input.get("base_decision_profile_content_hash") is not None:
                issues.append(_issue("decision_previous_profile_mismatch", "generation", "Initial confirmation must not reference a base profile"))
            starting_fields = [
                {"field_code": code, "field_name": name, "confirmation_status": "not_provided", "value": None, "last_user_update": None}
                for code, name in DECISION_FIELDS
            ]
        else:
            if confirmation_input.get("base_decision_profile_id") != base.get("decision_profile_id") or confirmation_input.get("base_decision_profile_content_hash") != base.get("decision_profile_content_hash"):
                issues.append(_issue("decision_previous_profile_mismatch", "generation", "Confirmation base dependency mismatch"))
            starting_fields = deepcopy(base.get("decision_fields") or [])
        from .decision_profile_builder import apply_confirmation_updates
        try:
            expected_fields, changed = apply_confirmation_updates(starting_fields, confirmation_input)
        except (KeyError, TypeError) as exc:
            issues.append(_issue("decision_confirmation_input_schema_invalid", "decision_fields", str(exc)))
        else:
            if previous_decision_profile is not None and not changed:
                issues.append(_issue("decision_profile_no_change", "decision_fields", "Confirmation produces no decision field change"))
            if profile.get("decision_fields") != expected_fields:
                issues.append(_issue("decision_field_confirmation_mismatch", "decision_fields", "Decision fields were not produced solely from the supplied confirmation input"))
    return issues


def assert_valid_decision_profile(profile: Any, **kwargs: Any) -> None:
    issues = validate_decision_profile(profile, **kwargs)
    if issues:
        raise InputDataError(json.dumps(issues[0], ensure_ascii=False, sort_keys=True))
